"""
tennis_resolver.py - Recupere les scores finaux des matchs tennis.

Source principale : Sofascore via Camoufox (browser stealth, robuste long terme).
Fallback : The Odds API /scores endpoint (decevant sur tennis, retourne rarement
les matchs completed).

Produit :
- data/tennis_results.json : par Odds API event_id, pour auto-resolve user picks
  cote client (via window.TENNIS_RESULTS).
- data/tennis_picks_history.json : algo picks resolus pour l'historique tennis.

Strategie matching : on combine algo picks (qui ont les noms joueurs + Odds API
event_id) et events Sofascore (qui ont les noms + scores). Match par paire
normalisee de noms joueurs sur la meme date.
"""
import json, sys, unicodedata
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    from config import ODDS_API_KEYS, ODDS_API_BASE
except Exception:
    ODDS_API_KEYS = []
    ODDS_API_BASE = "https://api.the-odds-api.com/v4"

import tennis_scraper  # pour get_active_tennis_sports + _try_keys
try:
    import tennis_browser
    BROWSER_AVAILABLE = True
except Exception as _e:
    print(f"  [tennis_resolver] tennis_browser indispo : {_e}")
    BROWSER_AVAILABLE = False

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
        d = json.loads(CACHE.read_text(encoding="utf-8"))
        # IMPORTANT : si le cache est vide (echec precedent : Camoufox KO,
        # 0 picks resolus), on le considere comme stale -> on retry.
        # Sinon on reste bloque sur 0 resultats pendant 30 min.
        if not d:
            return None
        return d
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


def _norm_name(s):
    """Normalisation tolerante pour match noms joueurs Odds API vs Sofascore."""
    if not s: return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.strip().lower()


def _fetch_sofascore_dates(dates):
    """Fetch Sofascore events pour une liste de dates (YYYY-MM-DD).

    Retourne {date_str: [event_slim, ...]}.
    """
    if not BROWSER_AVAILABLE:
        return {}
    out = {}
    for d in dates:
        try:
            evs = tennis_browser.fetch_sofascore_events_sync(d)
            out[d] = evs or []
        except Exception as e:
            print(f"  [tennis_resolver sofascore err {d}] {e}")
            out[d] = []
    return out


def _build_sofascore_index(by_date):
    """Indexe les events par (date, pair_normalisee_de_noms) pour matching rapide.

    Renvoie dict { (date, frozenset({norm_home, norm_away})) : event_slim }.
    """
    index = {}
    for d, events in (by_date or {}).items():
        for ev in events:
            h = _norm_name(ev.get("home"))
            a = _norm_name(ev.get("away"))
            if not h or not a: continue
            key = (d, frozenset({h, a}))
            # Si plusieurs events sur la meme paire le meme jour (peu probable),
            # on garde le completed en priorite
            existing = index.get(key)
            if not existing or (ev.get("completed") and not existing.get("completed")):
                index[key] = ev
    return index


def _lookup_sofascore(index, date_str, home_name, away_name):
    """Cherche l'event Sofascore correspondant a (date, home, away)."""
    if not date_str or not home_name or not away_name: return None
    pair = frozenset({_norm_name(home_name), _norm_name(away_name)})
    # Essai date exacte d'abord, puis +/- 1 jour (decalages timezone)
    try:
        d0 = datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return index.get((date_str, pair))
    for delta in (0, -1, 1):
        d = (d0 + timedelta(days=delta)).strftime("%Y-%m-%d")
        ev = index.get((d, pair))
        if ev: return ev
    return None


def _fetch_odds_api_fallback():
    """Fallback : Odds API /scores. Souvent vide pour tennis mais on tente."""
    sports = tennis_scraper.get_active_tennis_sports()
    out = {}
    for sk in sports:
        events = _fetch_scores(sk)
        for ev in events:
            if not ev.get("completed"):
                continue
            eid = ev.get("id")
            home = ev.get("home_team") or ""
            away = ev.get("away_team") or ""
            scores = ev.get("scores") or []
            h_total, a_total, set_score = _parse_score_string(scores, home, away)
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
                "source":      "odds_api",
            }
    return out


def fetch_all():
    """Combine Sofascore (primary) + Odds API (fallback) -> tennis_results.json.

    Pour chaque algo pick connu (tennis_picks.json), on cherche le score Sofascore
    par paire de noms + date. Le tennis_results.json final est keye par Odds API
    event_id (pour compat front).
    """
    cached = _read_cache()
    if cached is not None:
        return cached
    # 1. Charge algo picks pour connaitre les events a resoudre
    algo = {}
    if PICKS_PATH.exists():
        try:
            algo = json.loads(PICKS_PATH.read_text(encoding="utf-8"))
        except Exception:
            algo = {}
    algo_matches = algo.get("matches", []) or []
    # On charge aussi tennis_picks_history.json pour reresoudre des picks deja
    # historises en cas de fix (cas user pick d'il y a 2 jours non resolus).
    # 2. Liste les dates de matchs (-1j, 0, +1) pour Sofascore
    dates_to_fetch = set()
    today = datetime.now(timezone.utc).date()
    for delta in (-2, -1, 0):
        dates_to_fetch.add((today + timedelta(days=delta)).strftime("%Y-%m-%d"))
    # Ajoute les dates des algo picks (cas pick + ancien)
    for m in algo_matches:
        d = (m.get("start_iso") or "")[:10]
        if d: dates_to_fetch.add(d)
    print(f"  [tennis_resolver] dates a fetch via Sofascore : {sorted(dates_to_fetch)}")
    by_date = _fetch_sofascore_dates(sorted(dates_to_fetch))
    sofa_index = _build_sofascore_index(by_date)
    print(f"  [tennis_resolver] Sofascore : {sum(len(v) for v in by_date.values())} events indexes")
    # 3. Pour chaque algo match, lookup Sofascore par (date, paire de noms)
    out = {}
    matched = 0
    for m in algo_matches:
        eid = m.get("event_id")
        if not eid: continue
        h_name = (m.get("home") or {}).get("name", "")
        a_name = (m.get("away") or {}).get("name", "")
        date_str = (m.get("start_iso") or "")[:10]
        ev = _lookup_sofascore(sofa_index, date_str, h_name, a_name)
        if not ev or not ev.get("completed"):
            continue
        # Map Sofascore winner (home/away cote Sofascore) vers home/away cote algo.
        # Si l'algo home == Sofascore home (apres norm), winner inchange ; sinon
        # on swap.
        sofa_home_norm = _norm_name(ev.get("home"))
        algo_home_norm = _norm_name(h_name)
        if sofa_home_norm == algo_home_norm:
            winner = ev.get("winner")
            home_name_out = h_name
            away_name_out = a_name
        else:
            # Sofascore home = algo away (swap)
            winner = {"home": "away", "away": "home"}.get(ev.get("winner")) or ev.get("winner")
            home_name_out = h_name
            away_name_out = a_name
            # set_score : si on a "h_won-a_won" en POV sofascore, et qu'on swap,
            # on inverse le score
            ss = ev.get("set_score")
            if ss and "-" in ss:
                parts = ss.split("-")
                if len(parts) == 2:
                    ss = f"{parts[1]}-{parts[0]}"
            ev = dict(ev)
            ev["set_score"] = ss
        out[eid] = {
            "completed":     True,
            "winner":        winner,
            "home_name":     home_name_out,
            "away_name":     away_name_out,
            "set_score":     ev.get("set_score"),
            "total_games":   ev.get("total_games"),
            "source":        "sofascore",
            "sofascore_id":  ev.get("id"),
        }
        matched += 1
    print(f"  [tennis_resolver] {matched}/{len(algo_matches)} algo picks resolus via Sofascore")
    # 4. Fallback Odds API pour les events restants (pas matches via Sofascore)
    try:
        fallback = _fetch_odds_api_fallback()
        for eid, r in fallback.items():
            if eid not in out:
                out[eid] = r
        if fallback:
            print(f"  [tennis_resolver] +{len(fallback)} via Odds API fallback")
    except Exception as e:
        print(f"  [tennis_resolver odds fallback err] {e}")
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


DIAG_PATH = DATA / "tennis_resolver_diag.json"


def main():
    print(f"Tennis resolver -> {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    diag = {
        "started_at":   datetime.now(timezone.utc).isoformat(),
        "browser_available": BROWSER_AVAILABLE,
        "camoufox_installed": False,
        "sofascore_events_by_date": {},
        "algo_picks_total":    0,
        "algo_picks_matched":  0,
        "results_count":       0,
        "history_added":       0,
        "errors":              [],
    }
    # Diagnostic camoufox install
    try:
        import camoufox  # noqa
        diag["camoufox_installed"] = True
    except Exception as e:
        diag["errors"].append(f"camoufox import : {e}")
    try:
        results = fetch_all()
        diag["results_count"] = len(results)
    except Exception as e:
        diag["errors"].append(f"fetch_all : {e}")
        results = {}
    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {OUT_PATH} ({len(results)} matchs termines)")
    # Resout les picks algo + maintient l'historique
    try:
        diag["history_added"] = resolve_algo_picks(results) or 0
    except Exception as e:
        diag["errors"].append(f"resolve_algo_picks : {e}")
    diag["finished_at"] = datetime.now(timezone.utc).isoformat()
    # Ecrit aussi un fichier diag pour debug post-mortem (visible dans gh-pages)
    try:
        DIAG_PATH.write_text(json.dumps(diag, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    # S'assure que tennis_picks_history.json existe meme vide
    if not HISTORY_PATH.exists():
        HISTORY_PATH.write_text(json.dumps({"picks": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [tennis] tennis_picks_history.json cree (vide)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
