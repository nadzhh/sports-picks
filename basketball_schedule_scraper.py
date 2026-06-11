"""
basketball_schedule_scraper.py - Fetch upcoming basketball matches via TheSportsDB.

Couvre les ligues européennes en playoffs / saison régulière NON disponibles
sur The Odds API : Liga ACB (Espagne), Lega Basket (Italie), LNB Pro A
(France), German BBL, Greek Basket League, Turkish BSL.

Sortie : data/basketball_schedule.json — matchs upcoming sans cote.
Affichage dans Pronos V1 avec mention "Cotes non disponibles - source TheSportsDB".

Quota : free tier "3" = 30 req/min. On limite à ~10 leagues, freshness 6h.
"""
import json, sys, urllib.request, urllib.error, hashlib, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path("data")
DATA.mkdir(exist_ok=True)
OUT_PATH      = DATA / "basketball_schedule.json"
CACHE_PATH    = DATA / "cache_thesportsdb_basket.json"

CACHE_TTL = 6 * 3600
UA = "Mozilla/5.0 (sport-picks/1.0)"
API_KEY = "3"
BASE = f"https://www.thesportsdb.com/api/v1/json/{API_KEY}"

# League id -> (label, country flag emoji, tier)
LEAGUES = {
    "4408": ("Liga ACB (Espagne)",       "🇪🇸", "TIER1"),
    "4433": ("Lega Basket (Italie)",     "🇮🇹", "TIER1"),
    "4423": ("LNB Pro A (France)",       "🇫🇷", "TIER1"),
    "4452": ("Greek Basket League",      "🇬🇷", "TIER1"),
    "4475": ("Turkish BSL",              "🇹🇷", "TIER1"),
    "4441": ("German BBL",               "🇩🇪", "TIER1"),
    "5408": ("Supercoupe d'Italie",      "🇮🇹", "CUP"),
    "5409": ("Coupe d'Italie Basket",    "🇮🇹", "CUP"),
    "4832": ("Copa del Rey (Espagne)",   "🇪🇸", "CUP"),
    "5380": ("Coupe de France Basket",   "🇫🇷", "CUP"),
    "5507": ("Turkish Basketball Cup",   "🇹🇷", "CUP"),
    "5786": ("German BBL Pokal",         "🇩🇪", "CUP"),
}


def _http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _read_cache():
    if not CACHE_PATH.exists(): return None
    age = time.time() - CACHE_PATH.stat().st_mtime
    if age > CACHE_TTL: return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(data):
    try:
        CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _event_id(home, away, date):
    raw = f"{home}|{away}|{date}".encode("utf-8")
    return "sdb_" + hashlib.md5(raw).hexdigest()[:14]


def _parse_event(ev, league_label, country_emoji):
    home = ev.get("strHomeTeam") or ""
    away = ev.get("strAwayTeam") or ""
    if not (home and away): return None
    if home.lower() in ("tbd","?") or away.lower() in ("tbd","?"): return None
    # Date format : 2026-06-11 + strTime 18:00:00
    d = ev.get("dateEvent") or ""
    t = (ev.get("strTime") or "00:00:00")
    # strTimestamp est ISO
    ts_iso = ev.get("strTimestamp") or f"{d}T{t}+00:00"
    try:
        start_dt = datetime.fromisoformat(ts_iso.replace("Z","+00:00"))
        start_ts = int(start_dt.timestamp())
    except Exception:
        return None
    # Skip si > 7 jours futur (eviter spam)
    if start_ts > datetime.now(timezone.utc).timestamp() + 7 * 86400:
        return None
    return {
        "event_id":   _event_id(home, away, d),
        "league":     league_label,
        "country_emoji": country_emoji,
        "round":      ev.get("strDescriptionEN","") or (ev.get("intRound") and f"R{ev.get('intRound')}") or "",
        "start_ts":   start_ts,
        "start_iso":  start_dt.isoformat(),
        "home":       home,
        "away":       away,
        "season":     ev.get("strSeason",""),
        "venue":      ev.get("strVenue",""),
        "source":     "thesportsdb",
    }


def fetch_all():
    cached = _read_cache()
    if cached is not None:
        now_ts = datetime.now(timezone.utc).timestamp()
        futures = [m for m in cached.get("matches", []) if (m.get("start_ts") or 0) > now_ts]
        if futures:
            print(f"  [tsdb basket] cache frais : {len(futures)} matchs futurs")
            return {**cached, "matches": futures, "n_matches": len(futures)}
    all_matches = []
    for lid, (label, emoji, tier) in LEAGUES.items():
        url = f"{BASE}/eventsnextleague.php?id={lid}"
        try:
            data = _http_get(url)
        except urllib.error.HTTPError as e:
            print(f"  [tsdb basket {lid}] HTTP {e.code}")
            continue
        except Exception as e:
            print(f"  [tsdb basket {lid}] err: {e}")
            continue
        events = data.get("events") or []
        kept = 0
        for ev in events:
            parsed = _parse_event(ev, label, emoji)
            if parsed:
                all_matches.append(parsed)
                kept += 1
        print(f"  [tsdb basket {lid} - {label}] {kept} matchs upcoming")
        time.sleep(0.3)  # poli
    all_matches.sort(key=lambda m: m.get("start_ts") or 0)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_matches":    len(all_matches),
        "matches":      all_matches,
    }
    _write_cache(payload)
    return payload


def main():
    print(f"Basketball schedule (TheSportsDB) -> {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    payload = fetch_all()
    if payload.get("n_matches", 0) == 0 and OUT_PATH.exists():
        try:
            old = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            cutoff = datetime.now(timezone.utc).timestamp() - 6 * 3600
            still_fresh = [m for m in old.get("matches", []) if (m.get("start_ts") or 0) > cutoff]
            if still_fresh:
                print(f"  [tsdb basket] 0 nouveaux - on preserve {len(still_fresh)} matchs")
                payload = {**old, "matches": still_fresh, "n_matches": len(still_fresh), "preserved": True}
        except Exception as e:
            print(f"  [tsdb basket preserve err] {e}")
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {OUT_PATH} ({payload['n_matches']} matchs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
