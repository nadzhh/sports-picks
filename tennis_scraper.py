"""
tennis_scraper.py - Recupere matchs ATP/WTA des tournois actifs via The Odds API
puis enrichit chaque joueur avec stats Sackmann (rank, L10, surface, jeux/match).

Sortie : data/tennis_matches.json

Quota Odds API : 1 call par tournoi actif (ATP + WTA) -> 2-4 calls/run avec freshness 6h.

Resolution des noms : matching tolerant via tennis_stats.find_player_id (last name + fuzzy).
"""
import json, sys, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from config import ODDS_API_KEYS, ODDS_API_BASE
except Exception:
    ODDS_API_KEYS = []
    ODDS_API_BASE = "https://api.the-odds-api.com/v4"

import tennis_stats as ts

DATA = Path("data")
DATA.mkdir(exist_ok=True)
OUT_PATH      = DATA / "tennis_matches.json"
CACHE_TENNIS  = DATA / "cache_tennis_odds.json"
ACTIVE_CACHE  = DATA / "cache_tennis_active_sports.json"

ACTIVE_TTL = 12 * 3600   # liste sports actifs : 12h
ODDS_TTL   = 6  * 3600   # odds matchs : 6h

# Surface par tournoi (heuristique - Odds API ne renvoie pas la surface)
TOURNAMENT_SURFACE = {
    # Grand Slams
    "tennis_atp_aus_open_singles":   ("Hard", "Australian Open"),
    "tennis_atp_french_open":        ("Clay", "Roland-Garros"),
    "tennis_atp_wimbledon":          ("Grass","Wimbledon"),
    "tennis_atp_us_open":            ("Hard", "US Open"),
    "tennis_wta_aus_open_singles":   ("Hard", "Australian Open"),
    "tennis_wta_french_open":        ("Clay", "Roland-Garros"),
    "tennis_wta_wimbledon":          ("Grass","Wimbledon"),
    "tennis_wta_us_open":            ("Hard", "US Open"),
    # ATP 1000 / 500 (clay)
    "tennis_atp_monte_carlo_masters":("Clay", "Monte-Carlo"),
    "tennis_atp_madrid_open":        ("Clay", "Madrid"),
    "tennis_atp_italian_open":       ("Clay", "Rome"),
    "tennis_atp_barcelona_open":     ("Clay", "Barcelona"),
    "tennis_atp_hamburg_open":       ("Clay", "Hamburg"),
    "tennis_atp_munich":             ("Clay", "Munich"),
    # ATP 1000 / 500 (hard)
    "tennis_atp_indian_wells":       ("Hard", "Indian Wells"),
    "tennis_atp_miami_open":         ("Hard", "Miami"),
    "tennis_atp_canadian_open":      ("Hard", "Canada"),
    "tennis_atp_cincinnati_open":    ("Hard", "Cincinnati"),
    "tennis_atp_shanghai_masters":   ("Hard", "Shanghai"),
    "tennis_atp_paris_masters":      ("Hard", "Paris"),
    "tennis_atp_china_open":         ("Hard", "China Open"),
    "tennis_atp_dubai":              ("Hard", "Dubai"),
    "tennis_atp_qatar_open":         ("Hard", "Doha"),
    "tennis_atp_acapulco":           ("Hard", "Acapulco"),
    "tennis_atp_rotterdam":          ("Hard", "Rotterdam"),
    "tennis_atp_marseille":          ("Hard", "Marseille"),
    "tennis_atp_montpellier":        ("Hard", "Montpellier"),
    "tennis_atp_basel":              ("Hard", "Basel"),
    "tennis_atp_vienna":             ("Hard", "Vienna"),
    "tennis_atp_tokyo":              ("Hard", "Tokyo"),
    "tennis_atp_atp_finals":         ("Hard", "ATP Finals"),
    # ATP grass season (juin-juillet)
    "tennis_atp_stuttgart":          ("Grass", "Stuttgart (Boss Open)"),
    "tennis_atp_halle":              ("Grass", "Halle (Terra Wortmann)"),
    "tennis_atp_queens":             ("Grass", "Queen's Club (London)"),
    "tennis_atp_eastbourne":         ("Grass", "Eastbourne"),
    "tennis_atp_hertogenbosch":      ("Grass", "'s-Hertogenbosch (Libema)"),
    "tennis_atp_mallorca":           ("Grass", "Mallorca"),
    # WTA equivalents
    "tennis_wta_madrid_open":        ("Clay", "Madrid"),
    "tennis_wta_italian_open":       ("Clay", "Rome"),
    "tennis_wta_strasbourg":         ("Clay", "Strasbourg"),
    "tennis_wta_charleston_open":    ("Clay", "Charleston"),
    "tennis_wta_stuttgart_open":     ("Clay", "Stuttgart"),
    "tennis_wta_indian_wells":       ("Hard", "Indian Wells"),
    "tennis_wta_miami_open":         ("Hard", "Miami"),
    "tennis_wta_canadian_open":      ("Hard", "Canada"),
    "tennis_wta_cincinnati_open":    ("Hard", "Cincinnati"),
    "tennis_wta_wuhan_open":         ("Hard", "Wuhan"),
    "tennis_wta_china_open":         ("Hard", "China Open"),
    "tennis_wta_dubai":              ("Hard", "Dubai"),
    "tennis_wta_qatar_open":         ("Hard", "Doha"),
    "tennis_wta_finals":             ("Hard", "WTA Finals"),
    # WTA grass season
    "tennis_wta_berlin":             ("Grass", "Berlin"),
    "tennis_wta_eastbourne":         ("Grass", "Eastbourne"),
    "tennis_wta_birmingham":         ("Grass", "Birmingham"),
    "tennis_wta_nottingham":         ("Grass", "Nottingham"),
    "tennis_wta_bad_homburg":        ("Grass", "Bad Homburg"),
    "tennis_wta_hertogenbosch":      ("Grass", "'s-Hertogenbosch"),
}


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
    """Iterate keys, return first successful (data, key_index) or (None, None)."""
    for i, key in enumerate(ODDS_API_KEYS):
        url = url_template.replace("{APIKEY}", key)
        try:
            return _http_get(url), i
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                continue
            elif e.code == 429:
                print(f"  [tennis odds] key {i} 429 quota epuisee, suivante…")
                continue
            else:
                print(f"  [tennis odds] key {i} HTTP {e.code} : {url[:80]}")
                continue
        except Exception as e:
            print(f"  [tennis odds] key {i} err : {e}")
            continue
    return None, None


def get_active_tennis_sports():
    """Liste sport_keys tennis actuellement actifs (refresh 12h)."""
    cached = _read_cache(ACTIVE_CACHE, ACTIVE_TTL)
    if cached is not None:
        return cached
    if not ODDS_API_KEYS:
        return []
    url_tpl = f"{ODDS_API_BASE}/sports/?all=true&apiKey={{APIKEY}}"
    data, key_idx = _try_keys(url_tpl)
    if not data:
        return []
    active = [s["key"] for s in data
              if s.get("key","").startswith("tennis_") and s.get("active")]
    print(f"  [tennis] {len(active)} tournoi(s) actif(s) : {', '.join(active)}")
    _write_cache(ACTIVE_CACHE, active)
    return active


def get_matches_for_sport(sport_key):
    """Fetch h2h odds pour un sport_key (1 call Odds API)."""
    if not ODDS_API_KEYS:
        return []
    url_tpl = (f"{ODDS_API_BASE}/sports/{sport_key}/odds/"
               f"?regions=eu&markets=h2h&oddsFormat=decimal&apiKey={{APIKEY}}")
    data, _ = _try_keys(url_tpl)
    return data or []


def _best_odds(bookmakers, team_a, team_b):
    """Renvoie (best_a, best_b, best_book) parmi bookmakers."""
    best_a = best_b = 0.0
    best_book = None
    for bk in bookmakers or []:
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h": continue
            outs = mk.get("outcomes", [])
            d = {o.get("name",""): float(o.get("price") or 0) for o in outs}
            oa = d.get(team_a, 0); ob = d.get(team_b, 0)
            if oa > best_a:
                best_a = oa; best_book = bk.get("key","")
            if ob > best_b:
                best_b = ob
                if not best_book: best_book = bk.get("key","")
    return (best_a or None, best_b or None, best_book)


def _consensus_odds(bookmakers, team_a, team_b):
    """Moyenne des odds h2h sur tous les bookmakers (pour de-vigging fiable)."""
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


def _h2h_from_sackmann(pid_a, pid_b, tour):
    """Calcule H2H entre 2 joueurs depuis Sackmann matches (multi-annees).

    Renvoie (a_wins, b_wins).
    """
    if not (pid_a and pid_b):
        return (0, 0)
    aw = bw = 0
    for year_offset in range(0, 4):
        year = datetime.now().year - year_offset
        for r in ts._load_matches(tour, year):
            try:
                wid = int(r.get("winner_id") or 0)
                lid = int(r.get("loser_id")  or 0)
            except Exception:
                continue
            if {wid, lid} != {pid_a, pid_b}: continue
            if wid == pid_a: aw += 1
            else:            bw += 1
    return (aw, bw)


def _build_match(odds_match, sport_key):
    """Convertit un match Odds API + Sackmann en dict normalise."""
    home = odds_match.get("home_team", "")
    away = odds_match.get("away_team", "")
    if not (home and away): return None
    commence = odds_match.get("commence_time", "")
    try:
        start_dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        start_ts = int(start_dt.timestamp())
    except Exception:
        start_dt = None
        start_ts = 0
    surface, label = TOURNAMENT_SURFACE.get(sport_key, ("Hard", sport_key.replace("tennis_","").replace("_"," ").title()))
    tour = "ATP" if "_atp_" in sport_key else "WTA"
    print(f"    [{label}] {home} vs {away} ({surface}, {tour})")
    h_stats = ts.get_player(home, tour=tour, surface=surface)
    a_stats = ts.get_player(away, tour=tour, surface=surface)
    h2h_a, h2h_b = _h2h_from_sackmann(h_stats.get("player_id"), a_stats.get("player_id"), tour)
    best_a, best_b, best_book = _best_odds(odds_match.get("bookmakers"), home, away)
    cons_a, cons_b = _consensus_odds(odds_match.get("bookmakers"), home, away)
    return {
        "event_id":     odds_match.get("id"),
        "sport_key":    sport_key,
        "tournament":   label,
        "tour":         tour,
        "surface":      surface,
        "start_ts":     start_ts,
        "start_iso":    start_dt.isoformat() if start_dt else None,
        "home":         {**h_stats, "best_odd": best_a, "consensus_odd": cons_a},
        "away":         {**a_stats, "best_odd": best_b, "consensus_odd": cons_b},
        "best_book":    best_book,
        "h2h":          {"home_wins": h2h_a, "away_wins": h2h_b, "total": h2h_a + h2h_b},
    }


def fetch_all():
    """Recupere tous les matchs des tournois ATP/WTA actifs.

    Cache valide uniquement si au moins 1 match futur dedans (sinon refetch).
    """
    cached = _read_cache(CACHE_TENNIS, ODDS_TTL)
    if cached:
        now_ts = datetime.now(timezone.utc).timestamp()
        futures = [m for m in cached.get("matches", []) if (m.get("start_ts") or 0) > now_ts]
        if futures:
            print(f"  [tennis] cache frais : {len(futures)} matchs futurs (sur {len(cached.get('matches',[]))} total)")
            return {**cached, "matches": futures, "n_matches": len(futures)}
        else:
            print(f"  [tennis] cache stale (tous matchs passes, {len(cached.get('matches',[]))} dans cache) -> refetch")
    sports = get_active_tennis_sports()
    if not sports:
        print("  [tennis] aucun tournoi ATP/WTA actif")
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "n_matches": 0, "matches": []}
    all_matches = []
    for sk in sports:
        if sk not in TOURNAMENT_SURFACE:
            # On gere quand meme, juste avec defaults
            pass
        events = get_matches_for_sport(sk)
        print(f"  [{sk}] {len(events)} events")
        # Filtre : on garde upcoming dans les prochains 48h (commence_time future)
        now = datetime.now(timezone.utc)
        kept = []
        for ev in events:
            try:
                ct = datetime.fromisoformat(ev["commence_time"].replace("Z","+00:00"))
            except Exception:
                continue
            # Strict : on n'expose plus les matchs deja commences. Une fois l'heure
            # de debut atteinte, le match disparait du picks tennis (cote serveur).
            # Cote client, un timer JS rafraichit toutes les 60s pour aussi cacher
            # les matchs qui passent l'heure entre 2 cron runs.
            if ct < now: continue
            if ct > now + timedelta(hours=48): continue  # > 48h trop loin
            kept.append(ev)
        for ev in kept:
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
    _write_cache(CACHE_TENNIS, payload)
    return payload


def main():
    print(f"Tennis scraper -> {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    payload = fetch_all()
    # Preserve-on-failure : si on a 0 matchs ET qu'un fichier existe deja avec
    # des matchs encore d'actualite (start_ts > now - 6h), on garde l'ancien.
    if payload.get("n_matches", 0) == 0 and OUT_PATH.exists():
        try:
            old = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            cutoff = datetime.now(timezone.utc).timestamp() - 6 * 3600
            still_fresh = [m for m in old.get("matches", []) if (m.get("start_ts") or 0) > cutoff]
            if still_fresh:
                print(f"  [tennis] 0 nouveaux matchs - on preserve {len(still_fresh)} matchs encore d'actualite de l'ancien fichier")
                payload = {**old, "matches": still_fresh, "n_matches": len(still_fresh), "preserved": True}
        except Exception as e:
            print(f"  [tennis preserve err] {e}")
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {OUT_PATH} ({payload['n_matches']} matchs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
