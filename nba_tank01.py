"""
nba_tank01.py — Wrapper Tank01 Fantasy Stats NBA (RapidAPI).

Source : https://rapidapi.com/tank01/api/tank01-fantasy-stats
Free tier : 1000 req/mois.

UTILISATION dans le projet (uniquement) :
- get_player_info(name) + is_player_out(name) -> filtre blessures
  utilise par nba_picks_engine pour drop les picks Out/Doubtful.

Cache PlayerInfo : 12h. Budget estime : ~100-200 calls/mois (1 call par
joueur unique apparaissant dans nos picks, max 20/run * 2 runs/jour).
"""
import hashlib, json, time, urllib.parse, urllib.request, urllib.error, gzip
from pathlib import Path

try:
    from config import RAPIDAPI_KEY, TANK01_API_HOST
except ImportError:
    RAPIDAPI_KEY, TANK01_API_HOST = "", "tank01-fantasy-stats.p.rapidapi.com"

CACHE_DIR = Path("data/cache_tank01")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = f"https://{TANK01_API_HOST}"

HEADERS = {
    "X-RapidAPI-Key":  RAPIDAPI_KEY,
    "X-RapidAPI-Host": TANK01_API_HOST,
    "Accept":          "application/json",
}

# Throttle entre requetes (anti-spam)
_last_call = 0
MIN_DELAY = 0.4


def _throttle():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    _last_call = time.time()


def _cache_path(endpoint, params):
    key = endpoint + "?" + urllib.parse.urlencode(sorted((params or {}).items()))
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{endpoint}_{h}.json"


def _cache_get(path, ttl):
    if not path.exists(): return None
    if time.time() - path.stat().st_mtime > ttl: return None
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return None


def _cache_set(path, data):
    try: path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception: pass


def _get(endpoint, params=None, ttl=24 * 3600, force=False):
    """GET avec cache + retry doux. Retourne None si quota epuise / err."""
    if not RAPIDAPI_KEY:
        return None
    params = params or {}
    path = _cache_path(endpoint, params)
    if not force:
        cached = _cache_get(path, ttl)
        if cached is not None: return cached

    url = f"{BASE_URL}/{endpoint}"
    if params: url += "?" + urllib.parse.urlencode(params)
    _throttle()
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        raw = r.read()
        if raw[:2] == b"\x1f\x8b": raw = gzip.decompress(raw)
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode("utf-8", "ignore")[:150]
        except Exception: pass
        if e.code == 429:
            print(f"  [tank01 429 QUOTA EPUISE] {endpoint}")
        else:
            print(f"  [tank01 HTTP {e.code}] {endpoint} body={body!r}")
        return None
    except Exception as e:
        print(f"  [tank01 err] {endpoint}: {type(e).__name__}: {e}")
        return None
    _cache_set(path, data)
    return data


# ─── API conviviale ─────────────────────────────────────────────────────────


def get_player_info(player_name, ttl=12 * 3600):
    """
    Fetch getNBAPlayerInfo pour un joueur (cache 12h).
    Retourne dict avec injury (designation, description, returnDate),
    pos, jerseyNum, team, longName.
    """
    if not player_name: return None
    data = _get("getNBAPlayerInfo", params={"playerName": player_name}, ttl=ttl)
    if not data: return None
    body = data.get("body") or {}
    if isinstance(body, list):
        body = body[0] if body else {}
    if not isinstance(body, dict): return None
    return body


def is_player_out(player_name, ttl=12 * 3600):
    """
    Verifie si le joueur est OUT / Doubtful / Day-to-day pour le match a venir.
    Retourne (is_out: bool, status_text: str, returnDate: str).

    is_out=True signifie 'ne devrait pas jouer' (Out / Doubtful pour ce match) :
    on skip alors le pick. Day-To-Day = play possible -> on flag mais on garde.
    """
    info = get_player_info(player_name, ttl=ttl)
    if not info: return False, "", ""
    inj = info.get("injury") or {}
    designation = (inj.get("designation") or "").strip()
    description = inj.get("description") or ""
    return_date = inj.get("injReturnDate") or ""
    if not designation: return False, "", ""
    desig_low = designation.lower()
    # Statuts "ne joue pas"
    out_keywords = ("out", "doubtful", "ruled out")
    is_out = any(k in desig_low for k in out_keywords)
    return is_out, designation, return_date


if __name__ == "__main__":
    print(f"RAPIDAPI_KEY set : {bool(RAPIDAPI_KEY)} (len {len(RAPIDAPI_KEY)})")
    # Smoke test : check le status d'un joueur connu
    for name in ["LeBron James", "Stephen Curry"]:
        is_out, status, ret = is_player_out(name)
        print(f"  {name}: is_out={is_out} status='{status}' return='{ret}'")
