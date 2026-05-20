"""
nba_client.py — Client ESPN public API (gratuit, sans auth).

Migration depuis stats.nba.com qui black-hole les IPs data center (GH Actions).
ESPN public endpoints n'ont pas ce problem.

API surface conservee identique pour ne pas casser nba_scraper / nba_resolver /
nba_picks_engine. Les fonctions retournent les memes formats de dicts.

Endpoints ESPN utilises :
- /sports/basketball/nba/scoreboard?dates=YYYYMMDD  -> games du jour
- /sports/basketball/nba/teams/{team_id}/roster    -> roster equipe
- /sports/basketball/nba/summary?event={game_id}   -> boxscore final
- /common/v3/sports/basketball/nba/athletes/{id}/stats     -> season avg
- /common/v3/sports/basketball/nba/athletes/{id}/gamelog   -> recent games

Limitations ESPN :
- Pas de pace/DvP/usage rate => les multipliers correspondants sont desactives
  (defaut 1.0, pipeline reste fonctionnel)
- Game/team/player IDs sont differents de stats.nba.com (l'historique ancien
  reste avec ses IDs, ne sera plus resolu mais aucune perte de fonctionnalite)
"""
import hashlib, json, time, urllib.parse, urllib.request, urllib.error, gzip
from pathlib import Path
from datetime import datetime

CACHE_DIR = Path("data/cache_nba")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE_SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
BASE_WEB  = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"

_last_call = 0
MIN_DELAY = 0.3


def _throttle():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    _last_call = time.time()


def _cache_path(url):
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"espn_{h}.json"


def _cache_get(path, ttl):
    if not path.exists(): return None
    if time.time() - path.stat().st_mtime > ttl: return None
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return None


def _cache_set(path, data):
    try: path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception: pass


def _fetch(url, ttl=3600, force=False):
    """GET + cache disque + retry."""
    path = _cache_path(url)
    if not force:
        cached = _cache_get(path, ttl)
        if cached is not None: return cached

    data, last_err = None, None
    for attempt, timeout_s in enumerate([20, 30, 45]):
        _throttle()
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            r = urllib.request.urlopen(req, timeout=timeout_s)
            raw = r.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            data = json.loads(raw)
            if attempt > 0:
                print(f"  [espn OK retry {attempt+1}/3] {url[:80]}")
            break
        except urllib.error.HTTPError as e:
            print(f"  [espn HTTP {e.code}] {url[:80]}")
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            print(f"  [espn timeout {attempt+1}/3 t={timeout_s}s] {url[:80]}")
            continue
        except Exception as e:
            print(f"  [espn err] {url[:80]}: {type(e).__name__}: {e}")
            return None
    if data is None:
        print(f"  [espn GIVE UP] {url[:80]}  (derniere err: {last_err})")
        return None

    _cache_set(path, data)
    return data


# ─── API conviviale (interface identique a l'ancienne version) ────────────────

def games_on_date(date_str, ttl=30 * 60):
    """
    Games NBA pour une date YYYY-MM-DD.
    Retourne liste [{game_id, home, away, status_code, date, ...}].
    """
    d = date_str.replace("-", "")
    url = f"{BASE_SITE}/scoreboard?dates={d}"
    r = _fetch(url, ttl=ttl)
    if not r: return []
    events = r.get("events") or []
    out = []
    for ev in events:
        comps = (ev.get("competitions") or [{}])[0]
        competitors = comps.get("competitors") or []
        home_team = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away_team = next((c for c in competitors if c.get("homeAway") == "away"), {})
        status = (ev.get("status") or {}).get("type") or {}
        state = status.get("state", "")
        # Mapping state -> code : pre=1, in=2, post=3
        status_code = {"pre": 1, "in": 2, "post": 3}.get(state, 1)
        out.append({
            "game_id":      str(ev.get("id", "")),
            "date":         ev.get("date", ""),
            "status":       status.get("shortDetail") or status.get("description") or "",
            "status_code":  status_code,
            "home":         (home_team.get("team") or {}).get("name"),
            "home_city":    (home_team.get("team") or {}).get("location"),
            "home_tricode": (home_team.get("team") or {}).get("abbreviation"),
            "home_id":      str((home_team.get("team") or {}).get("id", "")),
            "away":         (away_team.get("team") or {}).get("name"),
            "away_city":    (away_team.get("team") or {}).get("location"),
            "away_tricode": (away_team.get("team") or {}).get("abbreviation"),
            "away_id":      str((away_team.get("team") or {}).get("id", "")),
        })
    return out


def team_roster(team_id, season=None, ttl=3 * 24 * 3600):
    """Roster d'une equipe NBA (ESPN team_id, string). Retourne liste de joueurs."""
    url = f"{BASE_SITE}/teams/{team_id}/roster"
    r = _fetch(url, ttl=ttl)
    if not r: return []
    athletes = r.get("athletes") or []
    out = []
    for a in athletes:
        out.append({
            "PLAYER_ID":   str(a.get("id", "")),
            "PLAYER_NAME": a.get("fullName") or a.get("displayName") or "",
            "POSITION":    (a.get("position") or {}).get("abbreviation", ""),
            "JERSEY":      a.get("jersey", ""),
        })
    return out


def _parse_split_stat(s):
    """Parse '5.0-9.8' (made-attempted) -> 5.0 (made). Sinon float direct."""
    if s is None: return 0
    s = str(s)
    if "-" in s:
        try: return float(s.split("-")[0])
        except Exception: return 0
    try: return float(s)
    except Exception: return 0


def _player_season_avg(athlete_id, season_year=2026, ttl=12 * 3600):
    """
    Recupere les season averages d'un joueur depuis ESPN.
    Retourne dict format compatible : {GP, MIN, PTS, REB, AST, FG3M, FGM, FGA, ...}
    """
    url = f"{BASE_WEB}/athletes/{athlete_id}/stats"
    r = _fetch(url, ttl=ttl)
    if not r: return {}
    # Format ESPN : categories[].statistics[] avec season + stats array
    # On utilise "labels" (codes courts GP, MIN, PTS, ...) plutot que "names" (longs)
    categories = r.get("categories") or []
    for cat in categories:
        if cat.get("name") != "averages": continue
        cols = cat.get("labels") or cat.get("names") or []
        for entry in (cat.get("statistics") or []):
            season = entry.get("season") or {}
            if season.get("year") != season_year: continue
            stats = entry.get("stats") or []
            if len(stats) < len(cols):
                stats = list(stats) + [None] * (len(cols) - len(stats))
            d = dict(zip(cols, stats))
            return {
                "GP":     int(_parse_split_stat(d.get("GP", 0))),
                "MIN":    _parse_split_stat(d.get("MIN", 0)),
                "PTS":    _parse_split_stat(d.get("PTS", 0)),
                "REB":    _parse_split_stat(d.get("REB", 0)),
                "AST":    _parse_split_stat(d.get("AST", 0)),
                "FG3M":   _parse_split_stat(d.get("3PT", 0)),
                "FGM":    _parse_split_stat(d.get("FG", 0)),
                "FGA":    _parse_split_stat((d.get("FG") or "0-0").split("-")[-1] if "-" in str(d.get("FG", "")) else 0),
                "STL":    _parse_split_stat(d.get("STL", 0)),
                "BLK":    _parse_split_stat(d.get("BLK", 0)),
                "TOV":    _parse_split_stat(d.get("TO", 0)),
                "FG_PCT": (_parse_split_stat(d.get("FG%", 0)) / 100.0) if d.get("FG%") else 0,
                "PLAYER_NAME": "",   # rempli par l'appelant
                "PLAYER_ID":   athlete_id,
            }
    return {}


def team_player_averages(team_id, season="2025-26", ttl=12 * 3600):
    """
    Pour chaque joueur du roster, recupere ses season averages.
    Retourne liste de dicts (format compatible avec ancien stats.nba.com).
    """
    roster = team_roster(team_id, ttl=ttl)
    if not roster: return []
    season_year = int(season.split("-")[0]) + 1  # "2025-26" -> 2026
    out = []
    for player in roster:
        pid = player["PLAYER_ID"]
        if not pid: continue
        avg = _player_season_avg(pid, season_year=season_year, ttl=ttl)
        if not avg: continue
        avg["PLAYER_NAME"] = player["PLAYER_NAME"]
        avg["PLAYER_ID"]   = pid
        out.append(avg)
    return out


def player_gamelog(player_id, season="2025-26", season_type="Regular Season", ttl=3 * 3600):
    """
    Game log d'un joueur. Retourne liste de games (1 dict par match).
    Combine regular season + playoffs (ESPN seasonTypes).
    """
    url = f"{BASE_WEB}/athletes/{player_id}/gamelog"
    r = _fetch(url, ttl=ttl)
    if not r: return []

    # Labels au niveau racine : ['MIN','FG','FG%','3PT','3P%','FT','FT%','REB','AST','BLK','STL','PF','TO','PTS']
    cols = r.get("labels") or r.get("names") or []
    events_meta = r.get("events") or {}  # {event_id: {gameDate, opponent, ...}}

    out = []
    for st in (r.get("seasonTypes") or []):
        for cat in (st.get("categories") or []):
            for ev_stat in (cat.get("events") or []):
                evid = str(ev_stat.get("eventId", ""))
                stats = ev_stat.get("stats") or []
                if len(stats) < len(cols):
                    stats = list(stats) + [None] * (len(cols) - len(stats))
                d = dict(zip(cols, stats))
                meta = events_meta.get(evid, {}) or {}
                date_raw = meta.get("gameDate", "")
                fg_str = str(d.get("FG", "0-0"))
                fga = 0
                try: fga = float(fg_str.split("-")[-1]) if "-" in fg_str else 0
                except Exception: fga = 0
                opp_dict = meta.get("opponent") if isinstance(meta.get("opponent"), dict) else {}
                at_vs = (meta.get("atVs") or "").lower()  # "vs" = home, "@" = away
                is_home = at_vs == "vs"
                out.append({
                    "GAME_ID":   evid,
                    "GAME_DATE": date_raw,
                    "MATCHUP":   opp_dict.get("displayName", ""),
                    "OPP_ABBR":  opp_dict.get("abbreviation", ""),
                    "IS_HOME":   is_home,
                    "WL":        meta.get("gameResult", ""),
                    "MIN":       _parse_split_stat(d.get("MIN", 0)),
                    "PTS":       _parse_split_stat(d.get("PTS", 0)),
                    "REB":       _parse_split_stat(d.get("REB", 0)),
                    "AST":       _parse_split_stat(d.get("AST", 0)),
                    "FG3M":      _parse_split_stat(d.get("3PT", 0)),
                    "FGM":       _parse_split_stat(d.get("FG", 0)),
                    "FGA":       fga,
                    "STL":       _parse_split_stat(d.get("STL", 0)),
                    "BLK":       _parse_split_stat(d.get("BLK", 0)),
                    "TOV":       _parse_split_stat(d.get("TO", 0)),
                })

    # Dedupe par GAME_ID (regular + playoffs peuvent doublonner)
    seen = set(); deduped = []
    for g in out:
        if g["GAME_ID"] in seen: continue
        seen.add(g["GAME_ID"]); deduped.append(g)

    # Tri par date desc
    def _date_key(g):
        try:
            return datetime.fromisoformat(g.get("GAME_DATE", "").replace("Z", "+00:00"))
        except Exception:
            return datetime.min
    deduped.sort(key=_date_key, reverse=True)
    return deduped


def player_recent_form(player_id, season="2025-26", n=5):
    """Retourne les n derniers matchs."""
    games = player_gamelog(player_id, season)
    return games[:n]


def _is_box_complete(summary):
    """
    Verifie que le match est REELLEMENT termine (state='post').
    ESPN expose le state dans header.competitions[0].status.type.{state, completed}.
    Si pre/in (avant match ou en cours), on ne cache PAS de stats partielles.
    """
    if not summary: return False
    header = summary.get("header") or {}
    comps = header.get("competitions") or []
    if not comps: return False
    status = (comps[0].get("status") or {}).get("type") or {}
    state = status.get("state", "")
    completed = status.get("completed")
    if completed is True: return True
    if state == "post": return True
    return False


def boxscore_traditional(game_id, ttl=14 * 24 * 3600, force=False):
    """
    Compat: pour le resolver, retourne l'event summary ESPN.
    Si le match n'est pas termine, on purge le cache et on force un refetch
    a la prochaine resolution (evite de figer des stats partielles).
    """
    url = f"{BASE_SITE}/summary?event={game_id}"
    data = _fetch(url, ttl=ttl, force=force)
    # Garde-fou : si le match n'est pas termine, on PURGE le cache et on
    # ne retourne pas les stats partielles (sinon resolver marque LOSS a tort).
    if data and not _is_box_complete(data):
        try:
            path = _cache_path(url)
            if path.exists(): path.unlink()
        except Exception: pass
        return None
    return data


def boxscore_players(game_id, force=False):
    """
    Retourne la liste des stats joueurs d'un match termine (format compatible).
    PLAYER_NAME, MIN, PTS, REB, AST, FG3M, TEAM_ABBREVIATION.
    """
    r = boxscore_traditional(game_id, force=force)
    rows_out = []
    if not r:
        # Purge cache si vide
        try:
            url = f"{BASE_SITE}/summary?event={game_id}"
            path = _cache_path(url)
            if path.exists(): path.unlink()
        except Exception: pass
        return rows_out

    # ESPN summary "boxscore" structure : boxscore.players[team_idx].statistics[].athletes[]
    box = r.get("boxscore") or {}
    teams_data = box.get("players") or []
    for team_entry in teams_data:
        team_info = team_entry.get("team") or {}
        team_abbr = team_info.get("abbreviation", "")
        for stat_group in (team_entry.get("statistics") or []):
            # stat_group contient les keys + labels + athletes (avec stats)
            keys = stat_group.get("keys") or stat_group.get("labels") or []
            for athlete in (stat_group.get("athletes") or []):
                a_info = athlete.get("athlete") or {}
                stats = athlete.get("stats") or []
                if not stats:
                    rows_out.append({
                        "PLAYER_NAME":       a_info.get("displayName") or a_info.get("fullName") or "",
                        "PLAYER_ID":         str(a_info.get("id", "")),
                        "TEAM_ABBREVIATION": team_abbr,
                        "MIN":  None,  # DNP
                        "PTS":  0, "REB": 0, "AST": 0, "FG3M": 0,
                        "FGM":  0, "FGA": 0, "STL": 0, "BLK": 0, "TOV": 0,
                    })
                    continue
                if len(stats) < len(keys):
                    stats = list(stats) + [None] * (len(keys) - len(stats))
                d = dict(zip(keys, stats))
                rows_out.append({
                    "PLAYER_NAME":       a_info.get("displayName") or a_info.get("fullName") or "",
                    "PLAYER_ID":         str(a_info.get("id", "")),
                    "TEAM_ABBREVIATION": team_abbr,
                    "MIN":  _parse_split_stat(d.get("minutes") or d.get("MIN", 0)) or d.get("minutes"),
                    "PTS":  _parse_split_stat(d.get("points") or d.get("PTS", 0)),
                    "REB":  _parse_split_stat(d.get("rebounds") or d.get("REB", 0)),
                    "AST":  _parse_split_stat(d.get("assists") or d.get("AST", 0)),
                    "FG3M": _parse_split_stat(d.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted") or d.get("3PT", 0)),
                    "FGM":  _parse_split_stat(d.get("fieldGoalsMade-fieldGoalsAttempted") or d.get("FG", 0)),
                    "FGA":  0,
                    "STL":  _parse_split_stat(d.get("steals") or d.get("STL", 0)),
                    "BLK":  _parse_split_stat(d.get("blocks") or d.get("BLK", 0)),
                    "TOV":  _parse_split_stat(d.get("turnovers") or d.get("TO", 0)),
                })
    return rows_out


# ─── Stats avancees (pace, DvP, USG) : delegues a Basketball-Reference ────────
# ESPN ne les expose pas, nba_bbref scrape BR pour les fournir.
# Les noms d'equipes BR ("Oklahoma City Thunder") sont normalises vers le
# format ESPN ("Thunder") afin que les lookups par team name fonctionnent.

def _season_to_year(season):
    """'2025-26' -> 2026."""
    try: return int(str(season).split("-")[0]) + 1
    except Exception: return 2026


def team_advanced_stats(season="2025-26", season_type="Regular Season", ttl=12 * 3600):
    return []  # API list-format pas utilise


def team_advanced_map(season="2025-26"):
    """{team_short_name (ESPN style): {pace, off_rating, def_rating, ...}}"""
    try:
        from nba_bbref import team_advanced_map as bbref_adv, bbref_to_espn
    except ImportError:
        return {}
    raw = bbref_adv(_season_to_year(season))
    # Re-keye en utilisant noms courts ESPN + team_id integer (compat avec ancienne API)
    out = {}
    for full_name, data in raw.items():
        short = bbref_to_espn(full_name)
        out[short] = dict(data)
        out[short]["team_name"] = short
    return out


def team_opponent_stats(season="2025-26", season_type="Regular Season", ttl=12 * 3600):
    return []


def team_opponent_map(season="2025-26"):
    """{team_short_name: {opp_pts, opp_reb, opp_ast, opp_fg3m, rank_opp_*}}"""
    try:
        from nba_bbref import team_opponent_map as bbref_opp, bbref_to_espn
    except ImportError:
        return {}
    raw = bbref_opp(_season_to_year(season))
    out = {}
    for full_name, data in raw.items():
        short = bbref_to_espn(full_name)
        out[short] = dict(data)
        out[short]["team_name"] = short
    return out


def player_usage_map(season="2025-26"):
    """{player_name: {usg_pct, gp, mp_per_g, team}}"""
    try:
        from nba_bbref import player_usage_map as bbref_usg
    except ImportError:
        return {}
    return bbref_usg(_season_to_year(season))
