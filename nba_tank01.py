"""
nba_tank01.py — Wrapper Tank01 Fantasy Stats NBA (RapidAPI).

Source : https://rapidapi.com/tank01/api/tank01-fantasy-stats
Free tier : 1000 req/mois.

Endpoints utilises :
- getNBAInjuryList : statuts blessures (le + critique)
- getNBADFS         : projections DFS (cross-check algo)
- getNBATeams       : DvP / team advanced stats
- getNBAPlayerInfo  : USG% / position / role

Strategie quota (~290 req/mois cible) :
- Injuries : cache 30 min   (~150 req/mois en periode active)
- DvP / Teams : cache 24h   (~30 req/mois)
- Projections : cache 6h    (~60 req/mois)
- PlayerInfo : cache 7 jours (~50 req/mois)

Si quota epuise -> fallback gracieux a {} (l'engine continue sans).
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

import re as _re

def _extract_name_from_desc(desc):
    """
    Format Tank01 description : 'May 7: Hart (thumb) is questionable for Friday...'
    On extrait le nom apres le ': ' jusqu'a '('. Si echec, premiere capitalisee.
    """
    if not desc: return None
    m = _re.search(r":\s*([A-Z][a-zA-Z\.\-'\s]+?)\s*\(", desc)
    if m: return m.group(1).strip()
    # fallback : premier mot capitalise apres ':'
    m = _re.search(r":\s*([A-Z][a-zA-Z\.\-']+)", desc)
    return m.group(1).strip() if m else None


def get_injuries(ttl=30 * 60):
    """
    Liste des joueurs blesses / questionables (status, return date).
    Cache 30 min (critique en jour de match).
    Retourne liste de dicts avec extracted_name (nom parse depuis description),
    designation, description, injReturnDate, playerID.
    """
    data = _get("getNBAInjuryList", ttl=ttl)
    if not data: return []
    body = data.get("body") or []
    raw_list = body if isinstance(body, list) else (
        body.get("injuries") if isinstance(body, dict) else list(body.values()) if isinstance(body, dict) else []
    )
    # Enrichit avec le nom extrait
    out = []
    for inj in raw_list:
        if not isinstance(inj, dict): continue
        extracted = inj.get("longName") or inj.get("name") or _extract_name_from_desc(inj.get("description", ""))
        out.append({
            "extracted_name": extracted,
            "playerID":       inj.get("playerID"),
            "designation":    inj.get("designation", ""),
            "description":    inj.get("description", ""),
            "injReturnDate":  inj.get("injReturnDate", ""),
            "injDate":        inj.get("injDate", ""),
        })
    return out


def get_player_injury_status(player_name, injuries=None):
    """
    Retourne le statut blessure d'un joueur, ou None si pas blesse.
    Match par dernier nom (lastname) car les descriptions Tank01 utilisent
    souvent juste le lastname (ex: 'Hart' pour Josh Hart).
    """
    if injuries is None: injuries = get_injuries()
    if not injuries or not player_name: return None
    target_full = player_name.lower().strip()
    target_last = target_full.split()[-1] if target_full else ""
    for inj in injuries:
        name = (inj.get("extracted_name") or "").lower().strip()
        if not name: continue
        if name == target_full: return inj
        if target_last and (name == target_last or target_last in name.split()):
            return inj
        if target_full and target_full in name: return inj
        if name in target_full: return inj
    return None


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


def get_dfs_projections(date_str=None, ttl=6 * 3600):
    """
    Projections DFS Tank01 pour une date (YYYYMMDD).
    Si pas de date, prend aujourd'hui.
    Retourne dict {player_name: {pts, reb, ast, ...}}.
    """
    if not date_str:
        from datetime import date as _d
        date_str = _d.today().strftime("%Y%m%d")
    data = _get("getNBADFS", {"date": date_str}, ttl=ttl)
    if not data: return {}
    body = data.get("body") or {}
    out = {}
    # Structure : body[teamId][playerId] = {projections}
    if isinstance(body, dict):
        for team_data in body.values():
            if not isinstance(team_data, dict): continue
            for p_data in team_data.values():
                if not isinstance(p_data, dict): continue
                name = p_data.get("longName") or p_data.get("playerName") or ""
                if not name: continue
                out[name] = {
                    "pts": float(p_data.get("pts", 0) or 0),
                    "reb": float(p_data.get("reb", 0) or 0),
                    "ast": float(p_data.get("ast", 0) or 0),
                    "fg3m": float(p_data.get("tptfgm", 0) or 0),
                    "min": float(p_data.get("mins", 0) or 0),
                }
    return out


def get_teams(ttl=24 * 3600):
    """
    Liste des equipes NBA + leur DvP (defense vs position).
    Cache 24h (donnees stables).
    """
    data = _get("getNBATeams", ttl=ttl)
    if not data: return []
    body = data.get("body") or []
    return body if isinstance(body, list) else []


def quota_status():
    """Retourne quota info si dispo (header X-RateLimit-...)."""
    # Tank01 ne retourne pas toujours ces headers - on tente un appel leger
    return {"key_set": bool(RAPIDAPI_KEY)}


if __name__ == "__main__":
    # Test rapide
    print(f"RAPIDAPI_KEY set : {bool(RAPIDAPI_KEY)} (len {len(RAPIDAPI_KEY)})")
    print("\n=== Injuries ===")
    inj = get_injuries()
    print(f"{len(inj)} injury entries")
    for i in inj[:5]:
        print(f"  {i.get('longName') or i.get('name')}: {i.get('designation') or i.get('injReturnDate')} ({i.get('description') or i.get('injury')})")
