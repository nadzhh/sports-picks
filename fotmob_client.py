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


def player(player_id, ttl=24 * 3600):
    """Stats d'un joueur via la page HTML FotMob (pas d'endpoint API direct).

    Retourne un dict slim :
    {
      "id": int, "name": str,
      "club_stats": {league, season, goals, assists, matches, minutes, rating},
      "l10": {n, goals, assists, minutes, goals_pm, assists_pm}
      "recent": [last 10 matches summary]
    }
    Cache 24h.
    """
    if not player_id: return None
    import re as _re
    full_url = f"https://www.fotmob.com/players/{player_id}/x"
    path = _cache_path(full_url + "_slim")
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
    except Exception:
        return None
    global _session_calls
    _session_calls += 1
    m = _re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, _re.DOTALL)
    if not m:
        return None
    try:
        nd = json.loads(m.group(1))
        data = nd.get("props", {}).get("pageProps", {}).get("data", {}) or {}
    except Exception:
        return None

    # mainLeague stats : transforme {title->value} en dict propre
    ml = data.get("mainLeague") or {}
    ml_stats = {}
    for s in ml.get("stats") or []:
        if not isinstance(s, dict): continue
        title = (s.get("title") or "").lower()
        val = s.get("value")
        if title in ("goals","assists","started","matches","minutes played","rating",
                      "yellow cards","red cards"):
            ml_stats[title.replace(" ", "_")] = val
    club_stats = {
        "league":   ml.get("leagueName"),
        "season":   ml.get("season"),
        "goals":    ml_stats.get("goals"),
        "assists":  ml_stats.get("assists"),
        "matches":  ml_stats.get("matches"),
        "minutes":  ml_stats.get("minutes_played"),
        "rating":   ml_stats.get("rating"),
    }

    # L10 a partir de recentMatches : on prend les 10 derniers (deja tries desc)
    rm = data.get("recentMatches") or []
    l10 = rm[:10]
    n10 = len(l10)
    g10 = sum((x.get("goals") or 0) for x in l10)
    a10 = sum((x.get("assists") or 0) for x in l10)
    mins10 = sum((x.get("minutesPlayed") or 0) for x in l10)
    l10_summary = {
        "n":           n10,
        "goals":       g10,
        "assists":     a10,
        "minutes":     mins10,
        "goals_pm":    round(g10 / n10, 3) if n10 else 0,
        "assists_pm":  round(a10 / n10, 3) if n10 else 0,
        "minutes_pm":  round(mins10 / n10, 1) if n10 else 0,
    }
    recent_slim = [
        {
            "date":     (x.get("matchDate") or {}).get("utcTime"),
            "team":     x.get("teamName"),
            "opp":      x.get("opponentTeamName"),
            "is_home":  x.get("isHomeTeam"),
            "league":   x.get("leagueName"),
            "goals":    x.get("goals"),
            "assists":  x.get("assists"),
            "minutes":  x.get("minutesPlayed"),
            "rating":   ((x.get("ratingProps") or {}).get("rating")),
        } for x in l10
    ]

    # Stats NAT TEAM via filtre recentMatches par teamName == nat team courant.
    # On detecte la nat team du joueur via primaryTeam (national team field
    # ou via le ccode FIFA). Approche simple : on cherche dans recentMatches
    # un teamName ayant un ccode different du club courant ET qui est
    # une selection (heuristique : nom court, pas de "FC", "United", etc.).
    primary = data.get("primaryTeam") or {}
    primary_team = primary.get("teamName") or ""
    # Heuristique nat team : on prend le teamName le PLUS FREQUENT dans
    # recentMatches qui n'est PAS le club primaryTeam. Pour Mbappe ce sera
    # 'France'. Pour Salah ce sera 'Egypt'.
    from collections import Counter
    team_counts = Counter()
    for x in rm:
        nm = x.get("teamName") or ""
        if nm and nm != primary_team:
            team_counts[nm] += 1
    nat_team_name = team_counts.most_common(1)[0][0] if team_counts else None

    nat_matches = [x for x in rm if (x.get("teamName") or "") == nat_team_name] if nat_team_name else []
    # Filtre matchs joues (au moins 1 min)
    nat_played = [x for x in nat_matches if (x.get("minutesPlayed") or 0) > 0]
    n_nat = len(nat_played)
    g_nat = sum((x.get("goals") or 0) for x in nat_played)
    a_nat = sum((x.get("assists") or 0) for x in nat_played)
    mins_nat = sum((x.get("minutesPlayed") or 0) for x in nat_played)
    # Recent nat form : matchs nat dans les 3 derniers
    last_3_nat = nat_played[:3]
    recent_nat_goals = sum((x.get("goals") or 0) for x in last_3_nat)
    recent_nat_ga = sum((x.get("goals") or 0) + (x.get("assists") or 0) for x in last_3_nat)
    # Stats du dernier match nat (pour detection drought)
    last_nat = nat_played[0] if nat_played else None
    last_min = (last_nat or {}).get("minutesPlayed") or 0
    last_g   = (last_nat or {}).get("goals") or 0
    last_a   = (last_nat or {}).get("assists") or 0
    # Drought : dernier match avec 60+ min et 0G+0A = pas de signal en nat team
    drought_last_match = (last_min >= 60 and last_g == 0 and last_a == 0)
    # Drought 2 matchs : enchaine 2 derniers avec 60+ min et 0G+0A
    drought_2_matches = False
    if drought_last_match and len(nat_played) >= 2:
        m2 = nat_played[1]
        if (m2.get("minutesPlayed") or 0) >= 60 and not (m2.get("goals") or m2.get("assists")):
            drought_2_matches = True

    nat_stats = {
        "team":            nat_team_name,
        "n":               n_nat,
        "n_total":         len(nat_matches),  # incl. matches sans minutes
        "goals":           g_nat,
        "assists":         a_nat,
        "minutes":         mins_nat,
        "goals_pm":        round(g_nat / n_nat, 3) if n_nat else 0,
        "assists_pm":      round(a_nat / n_nat, 3) if n_nat else 0,
        "ga_pm":           round((g_nat + a_nat) / n_nat, 3) if n_nat else 0,
        "minutes_pm":      round(mins_nat / n_nat, 1) if n_nat else 0,
        "last3_goals":     recent_nat_goals,
        "last3_ga":        recent_nat_ga,
        "drought_1_match": drought_last_match,
        "drought_2_matches": drought_2_matches,
        "last_match_mins": last_min,
        "last_match_g":    last_g,
        "last_match_a":    last_a,
    }

    out = {
        "id":         player_id,
        "name":       data.get("name"),
        "club_stats": club_stats,
        "l10":        l10_summary,
        "recent":     recent_slim,
        "nat_stats":  nat_stats,
    }
    _cache_set(path, out)
    return out


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
            # Garde-fou anti-cache-stale : si le cache est un match "non-finished"
            # (score partiel captured pendant le match), on force un refetch pour
            # éviter de bloquer un résultat provisoire pendant 30 jours.
            # Bug historique : Netherlands vs Morocco cache à "1-0" à la 60e alors
            # qu'il a fini 1-1 (Morocco qualifié aux tab).
            _c_status = ((cached.get("header") or {}).get("status") or {})
            _c_finished = bool(_c_status.get("finished"))
            _c_reason = ((_c_status.get("reason") or {}).get("long") or "").lower()
            _live_markers = ("half time","1st half","2nd half","extra time",
                             "1h","2h"," et","live","in progress")
            _looks_live = any(k in _c_reason for k in _live_markers)
            if _c_finished and not _looks_live:
                return cached
            # Sinon (pas fini OU marqueur live) → force refetch

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
                            "goals":   (p.get("performance") or {}).get("seasonGoals"),
                            "assists": (p.get("performance") or {}).get("seasonAssists"),
                            "apps":    (p.get("performance") or {}).get("seasonAppearances"),
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


def match_lineup(page_url, ttl=2 * 3600, force=False):
    """
    Recupere la lineup (11 probable + indisponibles) pour un match upcoming.
    Cache 2h par defaut (force=True pour bypass et avoir la derniere version,
    utile pour la notif kickoff ou le lineup passe predicted -> standard).
    """
    pp = _fetch_match_page(page_url, ttl=ttl, force=force)
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

    # Détecte si le match est vraiment terminé (FT ou après tirs au but).
    # Sans ce flag, _is_finished du resolver se fait piéger : un fetch pendant
    # les 90min (score partiel 1-0) est mis en cache 30j et jamais réactualisé.
    # Bug : Netherlands vs Morocco cache à "1-0" alors qu'il a fini 1-1.
    _finished = bool(status.get("finished"))
    _reason = ((status.get("reason") or {}).get("long") or "").lower()
    # Certains matchs terminés en prolongation/pen ont finished=True + reason="Pen X-Y"
    # Certains matchs live ont finished=False + reason contient "HT", "1H", "2H", "ET"
    _live_markers = ("half time", "1st half", "2nd half", "extra time",
                     "1h", "2h", "et", "live", "in progress")
    _looks_live = any(k in _reason for k in _live_markers) and not _finished

    return {
        "score":     status.get("scoreStr"),
        "utcTime":   status.get("utcTime"),
        "finished":  _finished,        # FT/AET/Pen officiel côté FotMob
        "looks_live": _looks_live,     # Match en cours → cache non fiable
        "status_reason": _reason,
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
