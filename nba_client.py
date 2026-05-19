"""
nba_client.py — Client stats.nba.com (gratuit, sans cle API)

Endpoints utilises:
- scoreboardv3       : games du jour
- commonteamroster   : roster equipe
- playergamelog      : derniers matchs d'un joueur
- leaguedashplayerstats : moyennes saison par joueur

Cache disque pour eviter d'hammer stats.nba.com (qui rate-limit agressivement).
"""
import hashlib, json, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path

CACHE_DIR = Path("data/cache_nba")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Headers requis (NBA bloque sans)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

BASE = "https://stats.nba.com/stats"

_last_call = 0
MIN_DELAY = 0.6  # rate limit


def _throttle():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    _last_call = time.time()


def _cache_path(endpoint, params):
    key = endpoint + "?" + urllib.parse.urlencode(sorted(params.items()))
    h = hashlib.md5(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{endpoint}_{h}.json"


def _cache_get(path, ttl):
    if not path.exists(): return None
    if time.time() - path.stat().st_mtime > ttl: return None
    try: return json.loads(path.read_text(encoding="utf-8"))
    except: return None


def _cache_set(path, data):
    try: path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except: pass


def get(endpoint, params=None, ttl=3600, force=False):
    """GET + cache disque."""
    import gzip
    params = params or {}
    path = _cache_path(endpoint, params)
    if not force:
        cached = _cache_get(path, ttl)
        if cached is not None:
            return cached

    url = f"{BASE}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    _throttle()
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        raw = r.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        print(f"  [nba {e.code}] {endpoint}")
        return None
    except Exception as e:
        print(f"  [nba err] {endpoint}: {e}")
        return None

    _cache_set(path, data)
    return data


def _to_dicts(payload, result_set_idx=0):
    """Convertit resultSets[idx] en liste de dicts."""
    if not payload: return []
    rs = payload.get("resultSets", []) or payload.get("resultSet", [])
    if isinstance(rs, list) and rs:
        rs = rs[result_set_idx] if isinstance(rs, list) else rs
    elif isinstance(rs, dict):
        pass
    else:
        return []
    headers = rs.get("headers", [])
    rows = rs.get("rowSet", [])
    return [dict(zip(headers, row)) for row in rows]


# ─── API conviviale ──────────────────────────────────────────────────────────

def games_on_date(date_str, ttl=3 * 3600):
    """
    Games NBA pour une date YYYY-MM-DD.
    Retourne liste [{game_id, home, away, status, ...}].
    """
    r = get("scoreboardv3", {"GameDate": date_str, "LeagueID": "00"}, ttl=ttl)
    if not r: return []
    games = r.get("scoreboard", {}).get("games", []) or []
    out = []
    for g in games:
        out.append({
            "game_id":     g.get("gameId"),
            "date":        g.get("gameEt") or g.get("gameTimeUTC"),
            "status":      g.get("gameStatusText"),
            "status_code": g.get("gameStatus"),  # 1 = pre, 2 = live, 3 = final
            "home":        g.get("homeTeam", {}).get("teamName"),
            "home_city":   g.get("homeTeam", {}).get("teamCity"),
            "home_tricode":g.get("homeTeam", {}).get("teamTricode"),
            "home_id":     g.get("homeTeam", {}).get("teamId"),
            "away":        g.get("awayTeam", {}).get("teamName"),
            "away_city":   g.get("awayTeam", {}).get("teamCity"),
            "away_tricode":g.get("awayTeam", {}).get("teamTricode"),
            "away_id":     g.get("awayTeam", {}).get("teamId"),
        })
    return out


def team_roster(team_id, season="2025-26", ttl=7 * 24 * 3600):
    """Roster d'une equipe NBA pour la saison donnee."""
    r = get("commonteamroster", {
        "LeagueID": "00", "Season": season, "TeamID": team_id,
    }, ttl=ttl)
    return _to_dicts(r)


def team_player_averages(team_id, season="2025-26", season_type="Regular Season", ttl=24 * 3600):
    """Moyennes par match des joueurs d'une equipe."""
    params = {
        "Season": season, "SeasonType": season_type, "TeamID": team_id,
        "PerMode": "PerGame", "MeasureType": "Base", "LeagueID": "00",
        "PaceAdjust": "N", "PlusMinus": "N", "Rank": "N",
        "LastNGames": 0, "Month": 0, "OpponentTeamID": 0, "PORound": 0, "Period": 0,
        "PlayerExperience": "", "PlayerPosition": "", "StarterBench": "",
        "Outcome": "", "Location": "", "SeasonSegment": "",
        "DateFrom": "", "DateTo": "", "GameSegment": "",
        "VsConference": "", "VsDivision": "", "TwoWay": 0, "ShotClockRange": "",
        "Conference": "", "Division": "", "College": "", "Country": "",
        "Height": "", "Weight": "", "Draft": "", "DraftYear": "", "DraftPick": "",
    }
    r = get("leaguedashplayerstats", params, ttl=ttl)
    return _to_dicts(r)


def player_gamelog(player_id, season="2025-26", season_type="Regular Season", ttl=6 * 3600):
    """Game log d'un joueur pour la saison donnee (un match par row)."""
    r = get("playergamelog", {
        "PlayerID": player_id, "Season": season, "SeasonType": season_type,
    }, ttl=ttl)
    return _to_dicts(r)


def boxscore_traditional(game_id, ttl=14 * 24 * 3600, force=False):
    """Boxscore traditional v2 d'un match (apres game final).
    Si force=True, bypass le cache (utile pour retries quand match en cours).
    """
    params = {
        "GameID":      game_id,
        "StartPeriod": 0, "EndPeriod": 10, "RangeType": 0,
        "StartRange":  0, "EndRange":   28800,
    }
    return get("boxscoretraditionalv2", params, ttl=ttl, force=force)


def boxscore_players(game_id, force=False):
    """
    Retourne la liste des stats joueurs d'un match termine.
    Chaque dict : PLAYER_NAME, MIN, PTS, REB, AST, FG3M, ... (et TEAM_ABBREVIATION).
    Liste vide si game pas encore joue ou indispo. Si vide, on PURGE le cache pour
    permettre un retry au prochain run (game se termine plus tard).
    """
    r = boxscore_traditional(game_id, force=force)
    rows_out = []
    if r:
        rs = r.get("resultSets", [])
        if isinstance(rs, list):
            for s in rs:
                if s.get("name") == "PlayerStats":
                    headers = s.get("headers", [])
                    rows = s.get("rowSet", []) or []
                    rows_out = [dict(zip(headers, row)) for row in rows]
                    break
    # Si vide, purge le cache (eviter de re-recuperer du vide pendant la TTL)
    if not rows_out:
        try:
            params = {
                "GameID":      game_id,
                "StartPeriod": 0, "EndPeriod": 10, "RangeType": 0,
                "StartRange":  0, "EndRange":   28800,
            }
            path = _cache_path("boxscoretraditionalv2", params)
            if path.exists(): path.unlink()
        except Exception:
            pass
    return rows_out


def player_recent_form(player_id, season="2025-26", n=5):
    """Retourne les n derniers matchs combines (playoffs si dispo, sinon regular)."""
    pl = player_gamelog(player_id, season, "Playoffs")
    reg = player_gamelog(player_id, season, "Regular Season")
    combined = (pl or []) + (reg or [])
    # Tri par date desc
    from datetime import datetime
    def _date(g):
        try: return datetime.strptime(g.get("GAME_DATE",""), "%b %d, %Y")
        except: return datetime.min
    combined.sort(key=_date, reverse=True)
    return combined[:n]


def team_advanced_stats(season="2025-26", season_type="Regular Season", ttl=12 * 3600):
    """
    Retourne les stats avancees (pace, OffRtg, DefRtg) par equipe pour la saison.
    Retourne liste de dicts avec TEAM_ID, TEAM_NAME, PACE, OFF_RATING, DEF_RATING, NET_RATING...
    """
    params = {
        "Season": season, "SeasonType": season_type,
        "PerMode": "PerGame", "MeasureType": "Advanced", "LeagueID": "00",
        "PaceAdjust": "N", "PlusMinus": "N", "Rank": "N",
        "LastNGames": 0, "Month": 0, "OpponentTeamID": 0, "PORound": 0, "Period": 0,
        "PlayerExperience": "", "PlayerPosition": "", "StarterBench": "",
        "Outcome": "", "Location": "", "SeasonSegment": "",
        "DateFrom": "", "DateTo": "", "GameSegment": "",
        "VsConference": "", "VsDivision": "", "TwoWay": 0, "ShotClockRange": "",
        "Conference": "", "Division": "",
        "TeamID": 0,
    }
    r = get("leaguedashteamstats", params, ttl=ttl)
    return _to_dicts(r)


def team_advanced_map(season="2025-26"):
    """Retourne dict {team_id: {pace, off_rating, def_rating, net_rating}}."""
    rows = team_advanced_stats(season)
    out = {}
    for r in rows:
        tid = r.get("TEAM_ID")
        if not tid: continue
        out[tid] = {
            "team_name":   r.get("TEAM_NAME"),
            "pace":        r.get("PACE", 0) or 0,
            "off_rating":  r.get("OFF_RATING", 0) or 0,
            "def_rating":  r.get("DEF_RATING", 0) or 0,
            "net_rating":  r.get("NET_RATING", 0) or 0,
            "ts_pct":      r.get("TS_PCT", 0) or 0,
            "ppg":         r.get("PTS", 0) or 0,
        }
    return out


def team_opponent_stats(season="2025-26", season_type="Regular Season", ttl=12 * 3600):
    """
    Stats encaissees (par adversaire) par equipe.
    Permet de mesurer la "faille" defensive d'une equipe par stat.
    """
    params = {
        "Season": season, "SeasonType": season_type,
        "PerMode": "PerGame", "MeasureType": "Opponent", "LeagueID": "00",
        "PaceAdjust": "N", "PlusMinus": "N", "Rank": "Y",  # avec ranks
        "LastNGames": 0, "Month": 0, "OpponentTeamID": 0, "PORound": 0, "Period": 0,
        "PlayerExperience": "", "PlayerPosition": "", "StarterBench": "",
        "Outcome": "", "Location": "", "SeasonSegment": "",
        "DateFrom": "", "DateTo": "", "GameSegment": "",
        "VsConference": "", "VsDivision": "", "TwoWay": 0, "ShotClockRange": "",
        "Conference": "", "Division": "",
        "TeamID": 0,
    }
    r = get("leaguedashteamstats", params, ttl=ttl)
    return _to_dicts(r)


def team_opponent_map(season="2025-26"):
    """Retourne dict {team_id: {opp_pts, opp_reb, opp_ast, opp_fg3m, ...}}.
    rank_X = position 1..30 ou 1 = "encaisse le plus" et 30 = "encaisse le moins"
    (= 1 est la pire defense pour ce stat = la cible la + facile pour un over).
    """
    rows = team_opponent_stats(season)
    out = {}
    # Pour calculer les ranks correctement, on tri sur chaque stat
    # OPP_X est la moyenne encaissee par game pour le team
    metrics = ["OPP_PTS", "OPP_REB", "OPP_AST", "OPP_FG3M", "OPP_FGA", "OPP_FG3A"]
    sorted_by = {}
    for m in metrics:
        sorted_by[m] = sorted(rows, key=lambda r: r.get(m, 0) or 0, reverse=True)
    for r in rows:
        tid = r.get("TEAM_ID")
        if not tid: continue
        entry = {
            "team_name": r.get("TEAM_NAME"),
            "opp_pts":   r.get("OPP_PTS", 0) or 0,
            "opp_reb":   r.get("OPP_REB", 0) or 0,
            "opp_ast":   r.get("OPP_AST", 0) or 0,
            "opp_fg3m":  r.get("OPP_FG3M", 0) or 0,
        }
        # ranks (1 = pire defense = encaisse le plus)
        for m in metrics:
            for idx, rr in enumerate(sorted_by[m]):
                if rr.get("TEAM_ID") == tid:
                    entry[f"rank_{m.lower()}"] = idx + 1
                    break
        out[tid] = entry
    return out
