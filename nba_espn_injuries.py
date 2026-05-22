"""
nba_espn_injuries.py - Statut blessures NBA via l'API publique ESPN.

Source : https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries
  - Gratuit, pas de cle, pas de quota.
  - Renvoie TOUS les blesses NBA groupes par equipe.
  - Statuts : "Out", "Doubtful", "Questionable", "Day-To-Day".

Cache 3h dans data/cache_espn_injuries.json.

API conviviale :
  - get_player_injury(name) -> dict ou None (avec designation, description, return_date)
  - get_team_injuries(team_name_or_abbr) -> list[dict]
"""
import json
import urllib.request
import urllib.error
import unicodedata
from pathlib import Path
from datetime import datetime, timedelta

CACHE_PATH = Path("data/cache_espn_injuries.json")
TTL_SECONDS = 3 * 3600  # 3h

URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"


def _norm(s):
    """Strip + lowercase + retire les diacritiques pour matching tolerant."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.strip().lower()


def _is_fresh(path):
    if not path.exists():
        return False
    age = datetime.now().timestamp() - path.stat().st_mtime
    return age < TTL_SECONDS


def _fetch():
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [espn injuries err] {e}")
        return None


def _load():
    if _is_fresh(CACHE_PATH):
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    data = _fetch()
    if data:
        CACHE_PATH.parent.mkdir(exist_ok=True)
        try:
            CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return data
    # Fallback : cache stale (mieux que rien)
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _build_indexes(data):
    """Renvoie (by_player, by_team_norm)."""
    by_player = {}
    by_team = {}
    if not data:
        return by_player, by_team
    for team_entry in data.get("injuries", []) or []:
        team = (team_entry.get("team") or {})
        team_name = team.get("displayName") or team_entry.get("displayName") or ""
        team_abbr = team.get("abbreviation") or ""
        team_keys = [_norm(team_name), _norm(team_abbr)]
        team_keys = [k for k in team_keys if k]
        items = []
        for i in (team_entry.get("injuries") or []):
            a = i.get("athlete") or {}
            name = a.get("displayName") or ""
            if not name:
                continue
            status = i.get("status", "") or ""
            short = i.get("shortComment", "") or ""
            long_c = i.get("longComment", "") or ""
            ret_date = (i.get("date") or "")[:10]
            entry = {
                "name":         name,
                "designation":  status,
                "description":  short or long_c,
                "return_date":  ret_date,
                "team":         team_name,
                "team_abbr":    team_abbr,
            }
            by_player[_norm(name)] = entry
            items.append(entry)
        for k in team_keys:
            by_team[k] = items
    return by_player, by_team


_CACHE = {"loaded": False, "by_player": {}, "by_team": {}}


def _ensure_loaded():
    if _CACHE["loaded"]:
        return
    data = _load()
    by_player, by_team = _build_indexes(data)
    _CACHE["by_player"] = by_player
    _CACHE["by_team"]   = by_team
    _CACHE["loaded"]    = True


def get_player_injury(name):
    """Retourne dict (designation/description/return_date/team) ou None."""
    if not name:
        return None
    _ensure_loaded()
    return _CACHE["by_player"].get(_norm(name))


def get_team_injuries(team_name_or_abbr):
    """Liste des blesses d'une equipe (matching par nom complet ou abbreviation)."""
    if not team_name_or_abbr:
        return []
    _ensure_loaded()
    return _CACHE["by_team"].get(_norm(team_name_or_abbr), [])


def reset_cache():
    """Force un re-fetch au prochain appel (utile pour les tests)."""
    _CACHE["loaded"] = False
    _CACHE["by_player"] = {}
    _CACHE["by_team"] = {}


if __name__ == "__main__":
    for name in ["Thomas Sorber", "De'Aaron Fox", "Dylan Harper", "Jalen Williams", "LeBron James"]:
        info = get_player_injury(name)
        print(f"  {name}: {info}")
    print()
    for team in ["San Antonio Spurs", "Oklahoma City Thunder"]:
        items = get_team_injuries(team)
        print(f"== {team} ({len(items)} blesses) ==")
        for i in items:
            print(f"    {i['name']} - {i['designation']}")
