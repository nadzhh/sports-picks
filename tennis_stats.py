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
    gw, gl, _ = _parse_score_games_with_sets(score_str)
    if gw is None: return None, None
    return gw, gl


def _parse_score_games_with_sets(score_str):
    """Score 'Sackmann' -> (gw, gl, n_sets).

    Ex: '6-4 6-3'           -> (12, 7,  2)   # bo3 fini en 2 sets
        '7-6(3) 4-6 6-4'    -> (17, 16, 3)   # bo3 fini en 3 sets
        '6-4 7-5 6-3'       -> (19, 12, 3)   # bo5 fini en 3 sets
        '4-6 6-4 6-3 7-6'   -> (23, 19, 4)   # bo5 fini en 4 sets
    """
    if not score_str:
        return None, None, None
    gw = gl = n_sets = 0
    for token in score_str.split():
        if any(x in token.upper() for x in ("RET","W/O","DEF","ABN")):
            continue
        token = token.split("(")[0].split("[")[0]
        if "-" in token:
            try:
                a, b = token.split("-")
                gw += int(a); gl += int(b)
                n_sets += 1
            except Exception:
                continue
    if gw + gl == 0: return None, None, None
    return gw, gl, n_sets


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


def _resolve_opponent_name(row, pid, tour):
    """Renvoie le nom de l'adversaire dans cette row Sackmann."""
    try:
        wid = int(row.get("winner_id") or 0)
        lid = int(row.get("loser_id")  or 0)
    except Exception:
        return "?"
    opp_id = lid if wid == pid else wid
    if not opp_id: return "?"
    # Sackmann inclut le nom directement
    if wid == pid:
        return (row.get("loser_name") or "?").strip()
    else:
        return (row.get("winner_name") or "?").strip()


def _fmt_date(yyyymmdd):
    s = str(yyyymmdd or "")
    if len(s) == 8:
        return f"{s[6:8]}/{s[4:6]}/{s[0:4]}"
    return s


def get_player(name, tour="ATP", surface=None, deep_surface_years=3):
    """Retourne dict joueur : rank, L10, surface_form, avg games, n_matches.

    deep_surface_years : nombre d'années à charger pour le bilan surface
    (par défaut 3 ans → permet d'avoir un échantillon décent sur gazon).

    Champs ajoutés :
      - surface_recent : liste des N derniers matchs sur la surface (max 8)
        chaque entrée = {date, opponent, score, won, tournament, round}
      - surface_w/l/n : agrégés sur TOUS les matchs surface des 3 ans
    """
    out = {
        "name": name,
        "tour": tour,
        "player_id": None,
        "rank": None,
        "rank_points": None,
        "l10_w": 0, "l10_l": 0, "l10_n": 0,
        "surface_w": 0, "surface_l": 0, "surface_n": 0,
        "surface_recent": [],
        "avg_games_for": None,
        "avg_games_against": None,
        "avg_games_for_surface": None,
        "avg_games_against_surface": None,
        "n_matches_year": 0,
        "n_matches_total": 0,
    }
    pid = find_player_id(name, tour)
    if not pid:
        return out
    out["player_id"] = pid
    rankings = _load_rankings(tour)
    if pid in rankings:
        out["rank"], out["rank_points"] = rankings[pid]

    year = datetime.now().year
    # Matchs ANNEE EN COURS (pour L10 / n_matches_year)
    rows_year = _load_matches(tour, year)
    rows_year = [r for r in rows_year
                 if int(r.get("winner_id") or 0) == pid or int(r.get("loser_id") or 0) == pid]
    if len(rows_year) < 5:
        # Si tres maigre, ajoute annee precedente pour le L10
        rows_prev = _load_matches(tour, year - 1)
        rows_prev = [r for r in rows_prev
                     if int(r.get("winner_id") or 0) == pid or int(r.get("loser_id") or 0) == pid]
        rows_year = rows_year + rows_prev
    out["n_matches_year"] = len(rows_year)
    rows_year.sort(key=lambda r: r.get("tourney_date","0"), reverse=True)

    # L10
    for r in rows_year[:10]:
        try:
            wid = int(r.get("winner_id") or 0)
            if wid == pid: out["l10_w"] += 1
            else:           out["l10_l"] += 1
        except Exception:
            pass
    out["l10_n"] = out["l10_w"] + out["l10_l"]

    # SURFACE : on aggregate sur N annees (3 par defaut) pour avoir un
    # echantillon decent. Les ATP/WTA grass season ne durent que 4 semaines/an
    # donc 1 an = trop peu, 3 ans = ~10-20 matchs par joueur top 100.
    surface_rows_all = []
    if surface:
        for yr_offset in range(0, deep_surface_years):
            yr = year - yr_offset
            ms = _load_matches(tour, yr)
            for r in ms:
                try:
                    wid = int(r.get("winner_id") or 0)
                    lid = int(r.get("loser_id") or 0)
                except Exception:
                    continue
                if pid not in (wid, lid): continue
                if (r.get("surface","") or "").lower() != surface.lower(): continue
                surface_rows_all.append(r)
        surface_rows_all.sort(key=lambda r: r.get("tourney_date","0"), reverse=True)

        # Agrégat W/L
        for r in surface_rows_all:
            try:
                wid = int(r.get("winner_id") or 0)
                if wid == pid: out["surface_w"] += 1
                else:           out["surface_l"] += 1
            except Exception:
                pass
        out["surface_n"] = out["surface_w"] + out["surface_l"]

        # Liste des 8 derniers matchs surface (pour affichage detaillé)
        for r in surface_rows_all[:8]:
            try:
                wid = int(r.get("winner_id") or 0)
                won = (wid == pid)
                out["surface_recent"].append({
                    "date":     _fmt_date(r.get("tourney_date")),
                    "opponent": _resolve_opponent_name(r, pid, tour),
                    "score":    (r.get("score") or "").strip(),
                    "won":      won,
                    "tournament": (r.get("tourney_name") or "?").strip(),
                    "round":    (r.get("round") or "?").strip(),
                })
            except Exception:
                continue

        # Avg games sur la surface (sur les 15 derniers matchs surface)
        # On track aussi le nombre de sets joués pour pouvoir calculer
        # avg_games_per_set (indépendant du format bo3/bo5).
        gf_s = ga_s = n_s = sets_s = 0
        for r in surface_rows_all[:15]:
            score = r.get("score") or ""
            gw, gl, ns = _parse_score_games_with_sets(score)
            if gw is None: continue
            try:
                wid = int(r.get("winner_id") or 0)
            except Exception:
                continue
            if wid == pid:
                gf_s += gw; ga_s += gl
            else:
                gf_s += gl; ga_s += gw
            n_s += 1
            sets_s += (ns or 0)
        if n_s > 0:
            out["avg_games_for_surface"]     = round(gf_s / n_s, 1)
            out["avg_games_against_surface"] = round(ga_s / n_s, 1)
            if sets_s > 0:
                out["avg_games_per_set_surface"] = round((gf_s + ga_s) / sets_s, 2)
                out["avg_sets_per_match_surface"] = round(sets_s / n_s, 2)

    out["n_matches_total"] = len(rows_year) + len(surface_rows_all)

    # Avg games per match (15 derniers all-surface si surface_n trop faible)
    # Track aussi le nombre de sets pour normalisation bo3/bo5.
    sample = surface_rows_all[:15] if surface and len(surface_rows_all) >= 8 else rows_year[:15]
    gf_total = ga_total = n_score = sets_total = 0
    for r in sample:
        score = r.get("score") or ""
        gw, gl, ns = _parse_score_games_with_sets(score)
        if gw is None: continue
        try:
            wid = int(r.get("winner_id") or 0)
        except Exception:
            continue
        if wid == pid:
            gf_total += gw; ga_total += gl
        else:
            gf_total += gl; ga_total += gw
        n_score += 1
        sets_total += (ns or 0)
    if n_score > 0:
        out["avg_games_for"]     = round(gf_total / n_score, 1)
        out["avg_games_against"] = round(ga_total / n_score, 1)
        if sets_total > 0:
            out["avg_games_per_set"]    = round((gf_total + ga_total) / sets_total, 2)
            out["avg_sets_per_match"]   = round(sets_total / n_score, 2)
    return out


def reset_cache():
    for tour in _CACHE:
        _CACHE[tour] = {"rankings": None, "players": None, "matches": None}


if __name__ == "__main__":
    # Smoke test : quelques joueurs ATP top
    for name in ["Carlos Alcaraz", "Jannik Sinner", "Federico Cina", "Jesper de Jong"]:
        info = get_player(name, tour="ATP", surface="Clay")
        print(f"{name}: {info}")
