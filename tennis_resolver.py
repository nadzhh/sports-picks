"""
tennis_resolver.py - Recupere les scores finaux des matchs tennis via The Odds
API /scores endpoint et produit data/tennis_results.json pour permettre
l'auto-resolve cote front (similaire au pattern NBA_BOX_SCORES).

Structure de sortie :
{
  "<event_id>": {
    "completed": true,
    "winner":    "home" | "away",
    "home_name": "...",
    "away_name": "...",
    "score":     "6-4 6-2",        # si dispo
    "total_games": 18,             # si parsable
    "set_score": "2-0",            # 2-0/2-1/0-2/1-2/3-0/3-1/3-2/0-3/1-3/2-3
  }
}

Quota : 1 call par tournoi actif (cache 30min).
"""
import json, sys, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

try:
    from config import ODDS_API_KEYS, ODDS_API_BASE
except Exception:
    ODDS_API_KEYS = []
    ODDS_API_BASE = "https://api.the-odds-api.com/v4"

import tennis_scraper  # pour get_active_tennis_sports + _try_keys

DATA = Path("data")
OUT_PATH      = DATA / "tennis_results.json"
PICKS_PATH    = DATA / "tennis_picks.json"
HISTORY_PATH  = DATA / "tennis_picks_history.json"
CACHE = DATA / "cache_tennis_scores.json"
CACHE_TTL = 30 * 60  # 30 min


def _read_cache():
    if not CACHE.exists(): return None
    age = datetime.now().timestamp() - CACHE.stat().st_mtime
    if age > CACHE_TTL: return None
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(data):
    try:
        CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _fetch_scores(sport_key):
    """Recupere /scores pour un sport. Renvoie list de matchs."""
    if not ODDS_API_KEYS: return []
    url_tpl = f"{ODDS_API_BASE}/sports/{sport_key}/scores/?daysFrom=3&apiKey={{APIKEY}}"
    data, _ = tennis_scraper._try_keys(url_tpl)
    return data or []


def _parse_score_string(scores_array, home_name, away_name):
    """Parse l'array 'scores' de The Odds API : list de {name, score}.

    score est typiquement "6-4 6-2" ou "6,4 / 6,3" selon book - on accepte tout.
    Renvoie (total_games_home, total_games_away, set_score) ou (None,None,None).
    """
    if not scores_array:
        return None, None, None
    by_name = {s.get("name"): s.get("score","") for s in scores_array}
    home_raw = by_name.get(home_name, "")
    away_raw = by_name.get(away_name, "")
    if not home_raw and not away_raw:
        return None, None, None
    # The Odds API renvoie chaque score sous forme de chiffres separes par virgule
    # Ex: home "6,7,6" away "4,5,4"
    def _parse(raw):
        # virgule, espace, slash : separateurs possibles
        for sep in (",", "/", " "):
            if sep in raw:
                parts = [p.strip() for p in raw.split(sep) if p.strip().isdigit()]
                if parts:
                    return [int(p) for p in parts]
        if raw.strip().isdigit():
            return [int(raw.strip())]
        return []
    h_sets = _parse(home_raw)
    a_sets = _parse(away_raw)
    if not h_sets or not a_sets:
        return None, None, None
    h_total = sum(h_sets)
    a_total = sum(a_sets)
    # Set score : compte combien de sets chacun a gagne
    h_won = sum(1 for h, a in zip(h_sets, a_sets) if h > a)
    a_won = sum(1 for h, a in zip(h_sets, a_sets) if a > h)
    set_score = f"{h_won}-{a_won}"
    return h_total, a_total, set_score


def fetch_all():
    cached = _read_cache()
    if cached is not None:
        return cached
    sports = tennis_scraper.get_active_tennis_sports()
    out = {}
    for sk in sports:
        events = _fetch_scores(sk)
        print(f"  [tennis scores {sk}] {len(events)} events")
        for ev in events:
            if not ev.get("completed"):
                continue
            eid = ev.get("id")
            home = ev.get("home_team") or ""
            away = ev.get("away_team") or ""
            scores = ev.get("scores") or []
            h_total, a_total, set_score = _parse_score_string(scores, home, away)
            # Determine winner via set count
            winner = None
            if set_score and "-" in set_score:
                hs, as_ = set_score.split("-")
                try:
                    winner = "home" if int(hs) > int(as_) else "away"
                except Exception:
                    pass
            out[eid] = {
                "completed":   True,
                "winner":      winner,
                "home_name":   home,
                "away_name":   away,
                "set_score":   set_score,
                "total_games": (h_total + a_total) if (h_total is not None and a_total is not None) else None,
            }
    _write_cache(out)
    return out


def _resolve_pick(pick, result):
    """Calcule WIN/LOSS/PUSH pour 1 pick selon kind + result final du match.

    - tennis_winner       : WIN si winner == selection ('home'/'away'), sinon LOSS
    - tennis_total_games  : OVER/UNDER vs line (PUSH si exact)
    - tennis_set_score    : WIN si set_score == score du pick, sinon LOSS
    """
    if not result or not result.get("completed"):
        return None, None
    kind = pick.get("kind")
    if kind == "tennis_winner":
        winner = result.get("winner")
        sel = pick.get("selection")
        if not winner or not sel: return None, None
        return ("WIN" if winner == sel else "LOSS"), winner
    if kind == "tennis_total_games":
        total = result.get("total_games")
        line  = pick.get("line")
        direction = pick.get("direction")
        if total is None or line is None or not direction: return None, None
        if total == line: return "PUSH", total
        if direction == "over":
            return ("WIN" if total > line else "LOSS"), total
        return ("WIN" if total < line else "LOSS"), total
    if kind == "tennis_set_score":
        # Pick.score est du POV joueur favori (home si selection=home).
        # result.set_score est toujours (home_sets)-(away_sets).
        ss_result = result.get("set_score")
        ss_pick   = pick.get("score")
        if not ss_result or not ss_pick: return None, None
        # Si pick.score est defini par "score" champ (engine met le score "POV home"
        # quand selection=home, sinon le score from-home view stocke comme '0-3'/'1-3').
        # On compare directement les chaines.
        return ("WIN" if ss_result == ss_pick else "LOSS"), ss_result
    return None, None


def _load_history():
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"picks": []}


def _save_history(hist):
    HISTORY_PATH.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_algo_picks(results):
    """Pour chaque pick algo des matchs termines, calcule le resultat et
    l'ajoute a tennis_picks_history.json (deduplique par pick_id).
    """
    if not PICKS_PATH.exists():
        print("  [tennis hist] pas de tennis_picks.json -> skip")
        return 0
    try:
        algo = json.loads(PICKS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [tennis hist] read err : {e}")
        return 0
    matches = algo.get("matches", []) or []
    hist = _load_history()
    existing = {p.get("id"): i for i, p in enumerate(hist.get("picks", []))}
    n_new = n_updated = 0
    for m in matches:
        eid = m.get("event_id")
        result = results.get(eid)
        if not result or not result.get("completed"):
            continue
        match_date = (m.get("start_iso") or "")[:10]
        h_name = (m.get("home") or {}).get("name", "")
        a_name = (m.get("away") or {}).get("name", "")
        matchup = f"{h_name} vs {a_name}"
        for p in m.get("picks", []) or []:
            kind = p.get("kind", "")
            # ID unique par (event_id + kind + selection/score/line) pour dedup
            ident = "_".join(str(x) for x in [
                eid, kind,
                p.get("selection") or "",
                p.get("score")     or "",
                p.get("line")      or "",
                p.get("direction") or "",
            ])
            res, actual = _resolve_pick(p, result)
            if res is None:
                continue
            entry = {
                "id":         ident,
                "event_id":   eid,
                "kind":       kind,
                "label":      p.get("label", ""),
                "confidence": p.get("confidence"),
                "real_cote":  p.get("real_cote"),
                "cote_min":   p.get("cote_min"),
                "edge_pp":    p.get("edge_pp"),
                "direction":  p.get("direction"),
                "line":       p.get("line"),
                "selection":  p.get("selection"),
                "score":      p.get("score"),
                "matchup":    matchup,
                "tournament": m.get("tournament", ""),
                "tour":       m.get("tour", ""),
                "surface":    m.get("surface", ""),
                "date":       match_date,
                "result":     res,
                "actual":     actual,
                "resolved_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            }
            if ident in existing:
                hist["picks"][existing[ident]] = entry
                n_updated += 1
            else:
                hist["picks"].append(entry)
                existing[ident] = len(hist["picks"]) - 1
                n_new += 1
    if n_new or n_updated:
        # Trier par date desc
        hist["picks"].sort(key=lambda p: p.get("date") or "", reverse=True)
        _save_history(hist)
    print(f"  [tennis hist] +{n_new} nouveaux, {n_updated} mis a jour -> {HISTORY_PATH} ({len(hist['picks'])} total)")
    return n_new + n_updated


def main():
    print(f"Tennis resolver -> {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    results = fetch_all()
    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {OUT_PATH} ({len(results)} matchs termines)")
    # Resout les picks algo + maintient l'historique
    resolve_algo_picks(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
