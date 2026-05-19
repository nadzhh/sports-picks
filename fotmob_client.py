"""
fotmob_client.py — HTTP client pour FotMob (gratuit, sans rate limit, saison en cours)

Endpoints utilises:
- https://www.fotmob.com/api/data/leagues?id=X  -> standings + fixtures + season_id
- https://www.fotmob.com/api/data/teams?id=X    -> team detail (player stats URLs)
- https://data.fotmob.com/stats/{lid}/season/{sid}/{stat}.json  -> stat data

Tout est cache disque pour eviter d'hammer FotMob.
"""
import gzip, hashlib, json, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path

CACHE_DIR = Path("data/cache_fotmob")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "application/json"}

_session_calls = 0
_last_call_time = 0
MIN_DELAY = 0.3  # politesse: 300ms entre calls


def _throttle():
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    _last_call_time = time.time()


def _cache_path(url):
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    safe = url.replace("://", "_").replace("/", "_").replace("?", "_").replace("&", "_")[:80]
    return CACHE_DIR / f"{safe}_{h}.json"


def _cache_get(path, ttl):
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > ttl:
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


def fetch(url, ttl=6 * 3600, force=False):
    """GET + gzip + cache. Retourne dict/list ou None si echec."""
    global _session_calls
    path = _cache_path(url)
    if not force:
        cached = _cache_get(path, ttl)
        if cached is not None:
            return cached

    _throttle()
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        raw = r.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print(f"  [fotmob 403] {url[:80]}")
        elif e.code == 404:
            print(f"  [fotmob 404] {url[:80]}")
        else:
            print(f"  [fotmob {e.code}] {url[:80]}")
        return None
    except Exception as e:
        print(f"  [fotmob err] {url[:80]}: {e}")
        return None

    _session_calls += 1
    _cache_set(path, data)
    return data


def league(league_id, ttl=6 * 3600):
    """Donnees de ligue (standings + fixtures + season info)."""
    return fetch(f"https://www.fotmob.com/api/data/leagues?id={league_id}", ttl=ttl)


def team(team_id, ttl=24 * 3600):
    """Donnees d'equipe (overview + stats URLs)."""
    return fetch(f"https://www.fotmob.com/api/data/teams?id={team_id}", ttl=ttl)


def stat(league_id, season_id, stat_name, ttl=24 * 3600):
    """Stat file (data.fotmob.com)."""
    url = f"https://data.fotmob.com/stats/{league_id}/season/{season_id}/{stat_name}.json"
    return fetch(url, ttl=ttl)


def _fetch_match_page(page_url, ttl=30 * 24 * 3600, force=False):
    """
    Recupere une page de match FotMob (HTML) et extrait __NEXT_DATA__.
    Retourne le dict 'pageProps' ou None si echec.
    page_url ex: '/matches/real-madrid-vs-barcelona/2grk20'
    Si force=True, bypass le cache (utile pour resoudre apres FT).
    """
    import re
    full_url = f"https://www.fotmob.com{page_url.split('#')[0]}"
    path = _cache_path(full_url + "_page")
    if not force:
        cached = _cache_get(path, ttl)
        if cached is not None:
            return cached

    _throttle()
    req = urllib.request.Request(full_url, headers=HEADERS)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        raw = r.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        html = raw.decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  [page err] {page_url}: {e}")
        return None

    global _session_calls
    _session_calls += 1

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        page_props = data.get("props", {}).get("pageProps", {})
        # Slim: on garde events (buts) + top stats (tirs, xG, ...) pour gagner du disque
        top_stats = {}
        try:
            groups = (
                page_props.get("content", {}).get("stats", {})
                          .get("Periods", {}).get("All", {}).get("stats", []) or []
            )
            for grp in groups:
                for st in grp.get("stats", []) or []:
                    title = st.get("title")
                    vals  = st.get("stats")
                    if title and vals and len(vals) == 2:
                        # garde la 1ere valeur trouvee
                        if title not in top_stats:
                            top_stats[title] = vals
        except Exception:
            pass

        # Lineup (predicted XI + indisponibles pour matchs upcoming)
        lineup_raw = page_props.get("content", {}).get("lineup") or {}
        lineup_slim = None
        if lineup_raw and lineup_raw.get("homeTeam"):
            def _slim_team(t):
                if not t: return None
                return {
                    "name": t.get("name"),
                    "id": t.get("id"),
                    "formation": t.get("formation"),
                    "coach": (t.get("coach") or {}).get("name") if t.get("coach") else None,
                    "starters": [
                        {
                            "id":      p.get("id"),
                            "name":    p.get("name"),
                            "shirt":   p.get("shirtNumber"),
                            "pos_id":  p.get("positionId"),
                            "rating":  (p.get("performance") or {}).get("seasonRating"),
                        } for p in (t.get("starters") or [])
                    ],
                    "unavailable": [
                        {
                            "id":     u.get("id"),
                            "name":   u.get("name"),
                            "type":   (u.get("unavailability") or {}).get("type"),
                            "return": (u.get("unavailability") or {}).get("expectedReturn"),
                            "rating": (u.get("performance") or {}).get("seasonRating"),
                            "goals":  (u.get("performance") or {}).get("seasonGoals"),
                            "assists":(u.get("performance") or {}).get("seasonAssists"),
                        } for u in (t.get("unavailable") or [])
                    ],
                }
            lineup_slim = {
                "type":   lineup_raw.get("lineupType"),
                "source": lineup_raw.get("source"),
                "home":   _slim_team(lineup_raw.get("homeTeam")),
                "away":   _slim_team(lineup_raw.get("awayTeam")),
            }

        slim = {
            "general":   page_props.get("general", {}),
            "header":    page_props.get("header", {}),
            "top_stats": top_stats,
            "lineup":    lineup_slim,
        }
        _cache_set(path, slim)
        return slim
    except Exception as e:
        print(f"  [parse err] {page_url}: {e}")
        return None


def match_lineup(page_url, ttl=2 * 3600):
    """
    Recupere la lineup (11 probable + indisponibles) pour un match upcoming.
    Cache 2h (donnees peuvent etre mises a jour avant le match).
    """
    pp = _fetch_match_page(page_url, ttl=ttl)
    if not pp:
        return None
    return pp.get("lineup")


def match_h2h(page_url, ttl=24 * 3600):
    """
    Recupere les confrontations directes (h2h) d'un match.
    Cache 24h (historique stable, peut etre re-fetche pour avoir des donnees recentes).
    Retourne liste de matchs h2h.
    """
    pp = _fetch_match_page_full(page_url, ttl=ttl)
    if not pp:
        return None
    h2h = pp.get("content", {}).get("h2h", {})
    if isinstance(h2h, dict):
        return h2h.get("matches", [])
    return []


def _fetch_match_page_full(page_url, ttl=24 * 3600):
    """
    Fetch HTML page mais retourne le pageProps complet (pas slim).
    Utile pour h2h qui contient des donnees non incluses dans slim.
    """
    import re
    full_url = f"https://www.fotmob.com{page_url.split('#')[0]}"
    cache_path = _cache_path(full_url + "_full")
    cached = _cache_get(cache_path, ttl)
    if cached is not None:
        return cached

    _throttle()
    req = urllib.request.Request(full_url, headers=HEADERS)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        raw = r.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        html = raw.decode("utf-8", errors="ignore")
    except Exception as e:
        return None

    global _session_calls
    _session_calls += 1

    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        page_props = data.get("props", {}).get("pageProps", {})
        # On garde juste ce qu'on veut pour limiter la taille cache
        slim = {
            "content": {
                "h2h": page_props.get("content", {}).get("h2h", {}),
            }
        }
        _cache_set(cache_path, slim)
        return slim
    except Exception:
        return None


def match_events(page_url, ttl=30 * 24 * 3600, force=False):
    """
    Extrait les buts + buteurs + passeurs d'un match.
    Retourne dict avec home_goals/away_goals/score/date.
    Si force=True, refetch (utile pour les matchs venant de se terminer).
    """
    pp = _fetch_match_page(page_url, ttl=ttl, force=force)
    if not pp:
        return None
    header = pp.get("header", {})
    teams = header.get("teams", [])
    status = header.get("status", {})
    events = header.get("events", {}) or {}

    def parse_goals(goals_dict):
        """homeTeamGoals/awayTeamGoals est un dict {scorer_name: [goal_event,...]}."""
        out = []
        for scorer_name, goal_list in (goals_dict or {}).items():
            for g in goal_list:
                if g.get("type") != "Goal":
                    continue
                out.append({
                    "scorer":      g.get("player", {}).get("name") or scorer_name,
                    "scorer_id":   g.get("player", {}).get("id"),
                    "assist":      g.get("assistStr"),
                    "minute":      g.get("time"),
                    "ownGoal":     bool(g.get("ownGoal")),
                    "description": g.get("goalDescription"),
                })
        out.sort(key=lambda x: x.get("minute") or 0)
        return out

    # Top stats (tirs, xG, possession, ...)
    top = pp.get("top_stats", {}) or {}
    def _pair(key):
        v = top.get(key)
        if v and len(v) == 2:
            return v[0], v[1]
        return None, None

    ts_h, ts_a   = _pair("Total shots")
    sot_h, sot_a = _pair("Shots on target")
    soff_h, soff_a = _pair("Shots off target")
    xg_h, xg_a   = _pair("Expected goals (xG)")
    poss_h, poss_a = _pair("Ball possession")

    def _num(v):
        if v is None: return None
        try: return float(v)
        except: return None

    return {
        "score":     status.get("scoreStr"),
        "utcTime":   status.get("utcTime"),
        "home":      teams[0].get("name") if len(teams) > 0 else None,
        "home_id":   teams[0].get("id")   if len(teams) > 0 else None,
        "away":      teams[1].get("name") if len(teams) > 1 else None,
        "away_id":   teams[1].get("id")   if len(teams) > 1 else None,
        "home_goals": parse_goals(events.get("homeTeamGoals")),
        "away_goals": parse_goals(events.get("awayTeamGoals")),
        # Stats agregables
        "home_shots":   _num(ts_h),
        "away_shots":   _num(ts_a),
        "home_sot":     _num(sot_h),
        "away_sot":     _num(sot_a),
        "home_xg":      _num(xg_h),
        "away_xg":      _num(xg_a),
        "home_possess": _num(poss_h),
        "away_possess": _num(poss_a),
    }


def session_calls():
    return _session_calls


def reset_session():
    global _session_calls
    _session_calls = 0
