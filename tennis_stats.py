"""
tennis_stats.py - Stats joueurs ATP/WTA via les CSVs publics de Jeff Sackmann.

Sources GitHub (publics, mises a jour hebdomadaire) :
  - github.com/JeffSackmann/tennis_atp : ATP men
  - github.com/JeffSackmann/tennis_wta : WTA women

Fichiers utilises (download + cache 5j) :
  - atp_rankings_current.csv  / wta_rankings_current.csv : rank + points
  - atp_players.csv           / wta_players.csv          : id -> name
  - atp_matches_<YEAR>.csv    / wta_matches_<YEAR>.csv   : matches + stats

Pour chaque joueur on calcule :
  - rank  (ATP/WTA)
  - L10   (forme: wins/losses sur les 10 derniers matchs)
  - surface_form (wins/losses sur la surface specifiee, derniers ~20 matchs)
  - avg_games_for/against (moyenne sur derniers matchs)

API conviviale :
  - get_player(name, tour="ATP")   -> dict avec rank, recent stats
  - reset_cache()                  -> force re-download au prochain appel
"""
import csv, io, time, unicodedata, urllib.request
from pathlib import Path
from datetime import datetime

CACHE_DIR = Path("data/cache_sackmann")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_TTL = 5 * 24 * 3600  # 5 jours (Sackmann update ~hebdo)

REPOS = {
    "ATP": "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master",
    "WTA": "https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master",
}

# Cache en memoire (parse 1 fois par run)
_CACHE = {
    "ATP": {"rankings": None, "players": None, "matches": None},
    "WTA": {"rankings": None, "players": None, "matches": None},
}


def _norm(s):
    """Normalise un nom pour matching tolerant : strip + lower + sans accents."""
    if not s: return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.strip().lower()


def _last_name(name):
    """Extrait nom de famille (dernier mot)."""
    parts = _norm(name).split()
    return parts[-1] if parts else ""


def _download(url, ttl=CACHE_TTL):
    """Download avec cache disque."""
    fname = url.split("/")[-1]
    path = CACHE_DIR / fname
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            pass
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8")
        path.write_text(raw, encoding="utf-8")
        return raw
    except Exception as e:
        print(f"  [sackmann err] {fname}: {e}")
        # Fallback stale cache si dispo
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                pass
        return None


def _load_rankings(tour):
    """Retourne dict {player_id: (rank, points)} pour le tour donne."""
    if _CACHE[tour]["rankings"] is not None:
        return _CACHE[tour]["rankings"]
    prefix = "atp" if tour == "ATP" else "wta"
    raw = _download(f"{REPOS[tour]}/{prefix}_rankings_current.csv")
    if not raw:
        _CACHE[tour]["rankings"] = {}
        return {}
    out = {}
    for row in csv.DictReader(io.StringIO(raw)):
        try:
            pid = int(row["player"])
            rk  = int(row["rank"])
            pts = int(row.get("points") or 0)
            out[pid] = (rk, pts)
        except Exception:
            continue
    _CACHE[tour]["rankings"] = out
    return out


def _load_players(tour):
    """Retourne dict {norm_name: player_id, ...} + dict {player_id: name}."""
    if _CACHE[tour]["players"] is not None:
        return _CACHE[tour]["players"]
    prefix = "atp" if tour == "ATP" else "wta"
    raw = _download(f"{REPOS[tour]}/{prefix}_players.csv")
    by_name = {}
    by_id = {}
    by_lastname = {}
    if raw:
        for row in csv.DictReader(io.StringIO(raw)):
            try:
                pid = int(row.get("player_id") or row.get("id") or 0)
            except Exception:
                continue
            if not pid: continue
            first = row.get("name_first", "") or ""
            last  = row.get("name_last", "") or ""
            full  = f"{first} {last}".strip()
            full_alt = f"{last} {first}".strip()  # "Cina Federico"
            by_id[pid] = full
            for v in (full, full_alt, last):
                k = _norm(v)
                if k and k not in by_name:
                    by_name[k] = pid
            # Index par nom de famille (utile pour matching fuzzy)
            ln = _norm(last)
            if ln:
                by_lastname.setdefault(ln, []).append((pid, full))
    payload = {"by_name": by_name, "by_id": by_id, "by_lastname": by_lastname}
    _CACHE[tour]["players"] = payload
    return payload


def _load_matches(tour, year=None):
    """Retourne liste de matchs du tour pour l'annee (default = annee courante)."""
    if year is None:
        year = datetime.now().year
    cache_key = f"matches_{year}"
    if _CACHE[tour].get(cache_key) is not None:
        return _CACHE[tour][cache_key]
    prefix = "atp" if tour == "ATP" else "wta"
    raw = _download(f"{REPOS[tour]}/{prefix}_matches_{year}.csv")
    rows = []
    if raw:
        for row in csv.DictReader(io.StringIO(raw)):
            rows.append(row)
    _CACHE[tour][cache_key] = rows
    return rows


def _parse_score_games(score_str):
    """Score 'Sackmann' -> (total_games_winner, total_games_loser).

    Ex: '6-4 6-3' -> (12, 7)
        '7-6(3) 4-6 6-4' -> (17, 16)
    """
    if not score_str:
        return None, None
    gw = gl = 0
    for token in score_str.split():
        # Cas 'RET' 'W/O' 'DEF' -> skip
        if any(x in token.upper() for x in ("RET","W/O","DEF","ABN")):
            continue
        # '7-6(3)' -> '7-6'
        token = token.split("(")[0].split("[")[0]
        if "-" in token:
            try:
                a, b = token.split("-")
                gw += int(a); gl += int(b)
            except Exception:
                continue
    if gw + gl == 0: return None, None
    return gw, gl


def find_player_id(name, tour="ATP"):
    """Retourne le player_id Sackmann pour ce nom, ou None."""
    players = _load_players(tour)
    if not players: return None
    k = _norm(name)
    pid = players["by_name"].get(k)
    if pid: return pid
    # Fuzzy : prend le dernier mot et matche sur lastname index
    ln = _last_name(name)
    candidates = players["by_lastname"].get(ln, [])
    if not candidates:
        return None
    # Si plusieurs candidats, on prend le premier (le mieux : matcher firstname)
    first_norm = _norm(name).replace(ln, "").strip()
    for pid, full in candidates:
        full_norm = _norm(full)
        if first_norm and first_norm in full_norm:
            return pid
    return candidates[0][0]


def get_player(name, tour="ATP", surface=None):
    """Retourne dict joueur : rank, L10, surface_form, avg games, n_matches.

    Si on ne trouve pas le joueur, retourne dict avec valeurs None mais structure
    coherente pour eviter les KeyError downstream.
    """
    out = {
        "name": name,
        "tour": tour,
        "player_id": None,
        "rank": None,
        "rank_points": None,
        "l10_w": 0, "l10_l": 0, "l10_n": 0,
        "surface_w": 0, "surface_l": 0, "surface_n": 0,
        "avg_games_for": None,
        "avg_games_against": None,
        "n_matches_year": 0,
    }
    pid = find_player_id(name, tour)
    if not pid:
        return out
    out["player_id"] = pid
    rankings = _load_rankings(tour)
    if pid in rankings:
        out["rank"], out["rank_points"] = rankings[pid]
    # Match history (annee en cours, fallback annee -1 si trop maigre)
    year = datetime.now().year
    matches = _load_matches(tour, year)
    rows = [r for r in matches
            if int(r.get("winner_id") or 0) == pid or int(r.get("loser_id") or 0) == pid]
    if len(rows) < 5:
        rows_prev = _load_matches(tour, year - 1)
        rows_prev = [r for r in rows_prev
                     if int(r.get("winner_id") or 0) == pid or int(r.get("loser_id") or 0) == pid]
        # Concat : annee en cours d'abord (tri par date desc fait plus tard)
        rows = rows + rows_prev
    out["n_matches_year"] = len(rows)
    # Tri par tourney_date desc (YYYYMMDD)
    rows.sort(key=lambda r: r.get("tourney_date", "0"), reverse=True)

    # L10
    last10 = rows[:10]
    for r in last10:
        try:
            wid = int(r.get("winner_id") or 0)
            won = (wid == pid)
            if won: out["l10_w"] += 1
            else:   out["l10_l"] += 1
        except Exception:
            pass
    out["l10_n"] = out["l10_w"] + out["l10_l"]

    # Surface form (sur derniers 20 matchs sur cette surface)
    if surface:
        surf_rows = [r for r in rows if (r.get("surface","") or "").lower() == surface.lower()][:20]
        for r in surf_rows:
            try:
                wid = int(r.get("winner_id") or 0)
                if wid == pid: out["surface_w"] += 1
                else:          out["surface_l"] += 1
            except Exception:
                pass
        out["surface_n"] = out["surface_w"] + out["surface_l"]

    # Avg games per match (sur derniers 15 matchs surface si dispo, sinon all)
    sample = [r for r in rows if (not surface or (r.get("surface","") or "").lower() == surface.lower())][:15]
    if len(sample) < 8:
        sample = rows[:15]
    gf_total = ga_total = n_score = 0
    for r in sample:
        score = r.get("score") or ""
        gw, gl = _parse_score_games(score)
        if gw is None: continue
        wid = int(r.get("winner_id") or 0)
        if wid == pid:
            gf_total += gw; ga_total += gl
        else:
            gf_total += gl; ga_total += gw
        n_score += 1
    if n_score > 0:
        out["avg_games_for"]     = round(gf_total / n_score, 1)
        out["avg_games_against"] = round(ga_total / n_score, 1)
    return out


def reset_cache():
    for tour in _CACHE:
        _CACHE[tour] = {"rankings": None, "players": None, "matches": None}


if __name__ == "__main__":
    # Smoke test : quelques joueurs ATP top
    for name in ["Carlos Alcaraz", "Jannik Sinner", "Federico Cina", "Jesper de Jong"]:
        info = get_player(name, tour="ATP", surface="Clay")
        print(f"{name}: {info}")
