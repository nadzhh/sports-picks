"""
basketball_scraper.py - Recupere matchs basket Europe (Euroleague, Eurocup,
LNB Pro A, ACB, etc.) via The Odds API.

Sortie : data/basketball_matches.json

Note : pas de stats joueurs (data sources non integrees pour ces ligues).
On expose juste les matchs + cotes consensus + best_odd pour permettre :
  - affichage dans Pronos V1 (sidebar competitions)
  - picks basiques type "Vainqueur" basés sur consensus + edge vs ligne marche

Quota : 1 call par sport_key actif (~3-5 calls/run avec freshness 6h).
"""
import json, sys, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from config import ODDS_API_KEYS, ODDS_API_BASE
except Exception:
    ODDS_API_KEYS = []
    ODDS_API_BASE = "https://api.the-odds-api.com/v4"

DATA = Path("data")
DATA.mkdir(exist_ok=True)
OUT_PATH      = DATA / "basketball_matches.json"
CACHE_ODDS    = DATA / "cache_basketball_odds.json"
ACTIVE_CACHE  = DATA / "cache_basketball_active_sports.json"

ACTIVE_TTL = 12 * 3600   # liste sports : 12h
ODDS_TTL   = 6  * 3600   # odds matchs : 6h

# Labels et tier des competitions cibles. On exclut NBA (gere a part avec
# stats joueurs) et NCAA (trop bruyant). On reste sur les grands championnats
# europeens + Asia/Australia pro.
LEAGUE_LABEL = {
    "basketball_euroleague":             ("Euroleague",          "TIER1"),
    "basketball_euroleague_basketball_championship": ("Euroleague", "TIER1"),
    "basketball_eurocup":                ("Eurocup",             "TIER1"),
    "basketball_acb":                    ("Liga ACB (Espagne)",  "TIER1"),
    "basketball_spain_acb":              ("Liga ACB (Espagne)",  "TIER1"),
    "basketball_lnb":                    ("LNB Pro A (France)",  "TIER1"),
    "basketball_france_lnb":             ("LNB Pro A (France)",  "TIER1"),
    "basketball_lba":                    ("Lega A (Italie)",     "TIER2"),
    "basketball_italy_lba":              ("Lega A (Italie)",     "TIER2"),
    "basketball_bbl":                    ("BBL (Allemagne)",     "TIER2"),
    "basketball_germany_bbl":            ("BBL (Allemagne)",     "TIER2"),
    "basketball_vtb":                    ("VTB United League",   "TIER2"),
    "basketball_fiba_world_cup":         ("FIBA World Cup",      "TIER1"),
    "basketball_eurobasket":             ("Eurobasket",          "TIER1"),
    "basketball_nbl":                    ("NBL (Australie)",     "TIER2"),
    "basketball_wnba":                   ("WNBA",                "TIER1"),
    "basketball_supercup":               ("Supercup",            "TIER1"),
    "basketball_olympics":               ("JO Basketball",       "TIER1"),
}

# Sports a SKIP (gere ailleurs ou peu pertinents pour notre audience FR/EU)
SKIP_KEYS = {"basketball_nba", "basketball_ncaab"}


def _http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _read_cache(path, ttl):
    if not path.exists(): return None
    age = (datetime.now().timestamp()) - path.stat().st_mtime
    if age > ttl: return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _try_keys(url_template):
    for i, key in enumerate(ODDS_API_KEYS):
        url = url_template.replace("{APIKEY}", key)
        try:
            return _http_get(url), i
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429):
                continue
            print(f"  [basketball odds] key {i} HTTP {e.code}")
            continue
        except Exception as e:
            print(f"  [basketball odds] key {i} err : {e}")
            continue
    return None, None


def get_active_basketball_sports():
    """Renvoie la liste des sport_keys basket actifs (hors NBA / NCAA)."""
    cached = _read_cache(ACTIVE_CACHE, ACTIVE_TTL)
    if cached is not None:
        return cached
    if not ODDS_API_KEYS:
        return []
    url_tpl = f"{ODDS_API_BASE}/sports/?all=true&apiKey={{APIKEY}}"
    data, _ = _try_keys(url_tpl)
    if not data:
        return []
    active = []
    unknown_active = []
    for s in data:
        k = s.get("key", "")
        if not k.startswith("basketball_"): continue
        if not s.get("active"): continue
        if k in SKIP_KEYS: continue
        if k in LEAGUE_LABEL:
            active.append(k)
        else:
            unknown_active.append(k)
    if unknown_active:
        print(f"  [basketball] sport_keys actifs non labellises (ignorés): {unknown_active}")
    print(f"  [basketball] {len(active)} ligue(s) active(s) : {', '.join(active)}")
    _write_cache(ACTIVE_CACHE, active)
    return active


def get_matches_for_sport(sport_key):
    if not ODDS_API_KEYS:
        return []
    url_tpl = (f"{ODDS_API_BASE}/sports/{sport_key}/odds/"
               f"?regions=eu&markets=h2h,totals&oddsFormat=decimal&apiKey={{APIKEY}}")
    data, _ = _try_keys(url_tpl)
    return data or []


def _consensus_h2h(bookmakers, team_a, team_b):
    sum_a = sum_b = 0.0; n = 0
    for bk in bookmakers or []:
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h": continue
            d = {o.get("name",""): float(o.get("price") or 0) for o in mk.get("outcomes", [])}
            oa = d.get(team_a, 0); ob = d.get(team_b, 0)
            if oa > 1 and ob > 1:
                sum_a += oa; sum_b += ob; n += 1
    if n == 0: return None, None
    return (sum_a / n, sum_b / n)


def _best_h2h(bookmakers, team_a, team_b):
    best_a = best_b = 0.0; book = None
    for bk in bookmakers or []:
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h": continue
            d = {o.get("name",""): float(o.get("price") or 0) for o in mk.get("outcomes", [])}
            oa = d.get(team_a, 0); ob = d.get(team_b, 0)
            if oa > best_a: best_a = oa; book = bk.get("key","")
            if ob > best_b: best_b = ob
    return (best_a or None, best_b or None, book)


def _consensus_total(bookmakers):
    """Renvoie le total median + over/under odds moyens (s'il y en a)."""
    lines = []
    for bk in bookmakers or []:
        for mk in bk.get("markets", []):
            if mk.get("key") != "totals": continue
            for o in mk.get("outcomes", []):
                pt = o.get("point")
                if pt is None: continue
                lines.append(pt)
    if not lines: return None
    lines.sort()
    return lines[len(lines) // 2]


def _build_match(odds_match, sport_key):
    home = odds_match.get("home_team", "")
    away = odds_match.get("away_team", "")
    if not (home and away): return None
    commence = odds_match.get("commence_time", "")
    try:
        start_dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        start_ts = int(start_dt.timestamp())
    except Exception:
        return None
    label, tier = LEAGUE_LABEL.get(sport_key, (sport_key, "TIER3"))
    cons_a, cons_b = _consensus_h2h(odds_match.get("bookmakers"), home, away)
    best_a, best_b, best_book = _best_h2h(odds_match.get("bookmakers"), home, away)
    total = _consensus_total(odds_match.get("bookmakers"))
    return {
        "event_id":     odds_match.get("id"),
        "sport_key":    sport_key,
        "league":       label,
        "tier":         tier,
        "start_ts":     start_ts,
        "start_iso":    start_dt.isoformat(),
        "home":         home,
        "away":         away,
        "consensus_odd_home": cons_a,
        "consensus_odd_away": cons_b,
        "best_odd_home":      best_a,
        "best_odd_away":      best_b,
        "best_book":          best_book,
        "total_points":       total,
    }


def fetch_all():
    cached = _read_cache(CACHE_ODDS, ODDS_TTL)
    if cached:
        now_ts = datetime.now(timezone.utc).timestamp()
        futures = [m for m in cached.get("matches", []) if (m.get("start_ts") or 0) > now_ts]
        if futures:
            print(f"  [basketball] cache frais : {len(futures)} matchs futurs")
            return {**cached, "matches": futures, "n_matches": len(futures)}
    sports = get_active_basketball_sports()
    if not sports:
        print("  [basketball] aucune ligue active hors NBA/NCAA")
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "n_matches": 0, "matches": []}
    all_matches = []
    now = datetime.now(timezone.utc)
    for sk in sports:
        events = get_matches_for_sport(sk)
        print(f"  [{sk}] {len(events)} events")
        for ev in events:
            try:
                ct = datetime.fromisoformat(ev["commence_time"].replace("Z","+00:00"))
            except Exception:
                continue
            if ct < now: continue
            if ct > now + timedelta(hours=72): continue  # 3 jours
            try:
                m = _build_match(ev, sk)
                if m: all_matches.append(m)
            except Exception as e:
                print(f"    [build err] {ev.get('home_team')} vs {ev.get('away_team')}: {e}")
    all_matches.sort(key=lambda m: m.get("start_ts") or 0)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_matches":    len(all_matches),
        "matches":      all_matches,
    }
    _write_cache(CACHE_ODDS, payload)
    return payload


def main():
    print(f"Basketball EU scraper -> {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    payload = fetch_all()
    if payload.get("n_matches", 0) == 0 and OUT_PATH.exists():
        try:
            old = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            cutoff = datetime.now(timezone.utc).timestamp() - 6 * 3600
            still_fresh = [m for m in old.get("matches", []) if (m.get("start_ts") or 0) > cutoff]
            if still_fresh:
                print(f"  [basketball] 0 nouveaux - on preserve {len(still_fresh)} matchs encore d'actualite")
                payload = {**old, "matches": still_fresh, "n_matches": len(still_fresh), "preserved": True}
        except Exception as e:
            print(f"  [basketball preserve err] {e}")
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {OUT_PATH} ({payload['n_matches']} matchs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
