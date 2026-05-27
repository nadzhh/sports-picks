"""
tennis_sofascore.py - Client HTTP Sofascore pour le tennis (gratuit, sans cle).

Endpoints utilises:
- /api/v1/sport/tennis/scheduled-events/{YYYY-MM-DD}  : matchs du jour
- /api/v1/event/{id}                                  : details event
- /api/v1/event/{id}/odds/1/all                       : odds disponibles
- /api/v1/event/{id}/h2h                              : H2H des 2 joueurs
- /api/v1/team/{playerId}                             : info joueur (ranking)
- /api/v1/team/{playerId}/events/last/0               : 10 derniers matchs

Tout en cache disque (TTL variable selon type de data) pour eviter de spammer
Sofascore. Throttle ~400ms entre 2 calls pour rester poli.
"""
import gzip, hashlib, json, time, urllib.request, urllib.error
from pathlib import Path

CACHE_DIR = Path("data/cache_sofascore_tennis")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HEADERS = {
    "User-Agent": UA,
    "Accept-Encoding": "gzip",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sofascore.com/",
    "Origin":  "https://www.sofascore.com",
}

BASE = "https://api.sofascore.com/api/v1"
MIN_DELAY = 0.4  # 400ms entre 2 calls
_last_call = 0
_session_calls = 0


def _throttle():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    _last_call = time.time()


def _cache_path(url):
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    safe = url.replace("://", "_").replace("/", "_").replace("?", "_")[-90:]
    return CACHE_DIR / f"{safe}_{h}.json"


def fetch(url, ttl=3600, force=False, silent_404=False):
    """GET avec cache + throttle. Retourne dict/list ou None."""
    global _session_calls
    path = _cache_path(url)
    if not force and path.exists():
        age = time.time() - path.stat().st_mtime
        if age < ttl:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

    _throttle()
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        raw = r.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        data = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404 and silent_404:
            return None
        print(f"  [sofa {e.code}] {url[len(BASE):]}")
        # Fallback : cache stale si dispo
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None
    except Exception as e:
        print(f"  [sofa err] {url[len(BASE):]}: {e}")
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    _session_calls += 1
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return data


# ── Endpoints publics ────────────────────────────────────────────────────────

def scheduled_events(date_str, ttl=1800):
    """date_str = YYYY-MM-DD. Retourne dict avec 'events' = [match, ...]."""
    return fetch(f"{BASE}/sport/tennis/scheduled-events/{date_str}", ttl=ttl)


def event_details(event_id, ttl=600):
    return fetch(f"{BASE}/event/{event_id}", ttl=ttl)


def event_odds(event_id, ttl=900):
    return fetch(f"{BASE}/event/{event_id}/odds/1/all", ttl=ttl, silent_404=True)


def event_h2h(event_id, ttl=7 * 24 * 3600):
    return fetch(f"{BASE}/event/{event_id}/h2h", ttl=ttl, silent_404=True)


def player_info(player_id, ttl=24 * 3600):
    return fetch(f"{BASE}/team/{player_id}", ttl=ttl)


def player_last_events(player_id, ttl=6 * 3600):
    """10 derniers matchs FT du joueur."""
    return fetch(f"{BASE}/team/{player_id}/events/last/0", ttl=ttl)


def session_calls():
    return _session_calls
