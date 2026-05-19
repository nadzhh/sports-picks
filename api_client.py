"""
api_client.py — client api-football avec cache disque + tracking quota
"""
import hashlib, json, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path
from config import API_KEY, API_BASE, TTL

CACHE_DIR = Path("data/cache_api")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

QUOTA_FILE = Path("data/.quota")

_session_calls = 0
_call_times = []  # timestamps des appels reels (rate limit 10/min)
RATE_LIMIT_PER_MIN = 9  # marge de securite vs 10/min officiel


def _throttle():
    """Attend si necessaire pour respecter 10 req/min."""
    global _call_times
    now = time.time()
    # Garde les appels < 60s
    _call_times = [t for t in _call_times if now - t < 60]
    if len(_call_times) >= RATE_LIMIT_PER_MIN:
        wait = 60 - (now - _call_times[0]) + 0.5
        if wait > 0:
            print(f"  [throttle] attente {wait:.1f}s (rate limit)...")
            time.sleep(wait)
    _call_times.append(time.time())


def _quota_load():
    if not QUOTA_FILE.exists():
        return {"date": "", "count": 0}
    try:
        return json.loads(QUOTA_FILE.read_text())
    except Exception:
        return {"date": "", "count": 0}


def _quota_save(q):
    QUOTA_FILE.write_text(json.dumps(q))


def quota_today():
    from datetime import date
    q = _quota_load()
    today = date.today().isoformat()
    if q.get("date") != today:
        return 0
    return q.get("count", 0)


def _quota_inc():
    from datetime import date
    q = _quota_load()
    today = date.today().isoformat()
    if q.get("date") != today:
        q = {"date": today, "count": 0}
    q["count"] = q.get("count", 0) + 1
    _quota_save(q)


def _cache_path(endpoint, params):
    key = endpoint + "?" + urllib.parse.urlencode(sorted(params.items()))
    h   = hashlib.md5(key.encode()).hexdigest()[:16]
    slug = endpoint.strip("/").replace("/", "_")
    return CACHE_DIR / f"{slug}_{h}.json"


def _cache_get(path, ttl_seconds):
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > ttl_seconds:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_set(path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def get(endpoint, params=None, ttl_key="fixtures_date", force=False):
    """
    Appelle api-football avec cache disque.
    Retourne le payload "response" deja extrait (la liste des resultats).
    """
    global _session_calls
    params = params or {}
    ttl    = TTL.get(ttl_key, 3600)
    path   = _cache_path(endpoint, params)

    if not force:
        cached = _cache_get(path, ttl)
        if cached is not None:
            return cached.get("response", cached)

    url = f"{API_BASE}/{endpoint.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    _throttle()
    req = urllib.request.Request(url, headers={"x-apisports-key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"  [429] rate limit, attente 65s puis retry...")
            time.sleep(65)
            _call_times.clear()
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    raw = json.loads(r.read())
                _call_times.append(time.time())
            except Exception as e2:
                print(f"  [!] retry failed: {e2}")
                return []
        else:
            print(f"  [!] HTTP {e.code} sur {endpoint}")
            return []
    except Exception as e:
        print(f"  [!] {endpoint}: {e}")
        return []

    _quota_inc()
    _session_calls += 1

    # Si payload contient des erreurs, log
    if isinstance(raw, dict) and raw.get("errors"):
        errs = raw["errors"]
        if errs:
            print(f"  [!] Erreurs API sur {endpoint}: {errs}")
            # Ne pas cacher une réponse d'erreur (quota épuisé, etc.)
            if any(k in str(errs).lower() for k in ["rate", "limit", "subscription"]):
                return []

    _cache_set(path, raw)
    return raw.get("response", [])


def session_calls():
    return _session_calls


def reset_session():
    global _session_calls
    _session_calls = 0


def cache_clean(days=30):
    cutoff = time.time() - days * 86400
    for f in CACHE_DIR.glob("*.json"):
        if f.stat().st_mtime < cutoff:
            try: f.unlink()
            except: pass
