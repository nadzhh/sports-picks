"""
scraper_players_today.py
Identique à scraper_players.py mais ne traite que les matchs du jour.
Beaucoup plus rapide — à utiliser au quotidien.
"""

import asyncio, json, os
from datetime import datetime
from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.player import Player
from sofascore_wrapper.league import League
from sofascore_wrapper.team import Team
from sofascore_wrapper.match import Match

os.makedirs("data", exist_ok=True)

LEAGUE_IDS = {
    "Premier League":      17,
    "La Liga":             8,
    "Bundesliga":          35,
    "Serie A":             23,
    "Ligue 1":             34,
    "Champions League":    7,
    "Europa League":       679,
    "Conference League":   17015,
}

# Pour les équipes jouant en coupe d'europe, scraper aussi leur championnat
# Mapping : nom d'équipe → (league_id_champ, league_name)
# On détecte automatiquement via la nationalité de l'équipe
COUNTRY_TO_LEAGUE = {
    "England":     (17,  "Premier League"),
    "Spain":       (8,   "La Liga"),
    "Germany":     (35,  "Bundesliga"),
    "Italy":       (23,  "Serie A"),
    "France":      (34,  "Ligue 1"),
    "Netherlands": (37,  "Eredivisie"),
    "Portugal":    (238, "Primeira Liga"),
}

CUP_LEAGUES = {7, 679, 17015}

SKIP_POSITIONS      = {"G"}
OFFENSIVE_POSITIONS = {"F", "M"}

# ─── Helpers (identiques à scraper_players.py) ─────────────────────────────

async def get_season_ids(api):
    season_ids = {}
    for name, lid in LEAGUE_IDS.items():
        try:
            league       = League(api, lid)
            season       = await league.current_season()
            season_id    = season["id"]
            # Récupère le vrai nombre de matchs joués via current_round
            try:
                current_round = await league.current_round(season_id)
                games_played  = max(1, current_round - 1)  # round actuel - 1 = matchs joués
            except Exception:
                games_played = None
            season_ids[name] = {
                "league_id":    lid,
                "season_id":    season_id,
                "games_played": games_played,
            }
            print(f"  ✅ {name} → {season['name']} · journée {current_round} ({games_played} matchs joués)")
        except Exception as e:
            print(f"  ❌ {name} : {e}")
    return season_ids

def get_missing_ids(lineup):
    return {s.get("player", {}).get("id") for s in lineup.get("missing_players", [])}

def get_starters(lineup):
    players = []
    for s in lineup.get("starters", []):
        p   = s.get("player", {})
        pos = p.get("position", "")
        pid = p.get("id")
        if pid and pos not in SKIP_POSITIONS:
            players.append({"id": pid, "name": p.get("name"),
                            "shortName": p.get("shortName", p.get("name")),
                            "position": pos, "is_sub": False,
                            "country": p.get("country", {}).get("name", "")})
    return players

async def get_squad_subs(api, team_id, starter_ids, missing_ids):
    try:
        team  = Team(api, team_id)
        squad = await team.squad()
        subs  = []
        for entry in squad.get("players", []):
            p   = entry.get("player", {})
            pid = p.get("id")
            pos = p.get("position", "")
            if pid and pos in OFFENSIVE_POSITIONS and pid not in starter_ids and pid not in missing_ids:
                subs.append({"id": pid, "name": p.get("name"),
                             "shortName": p.get("shortName", p.get("name")),
                             "position": pos, "is_sub": True,
                             "country": p.get("country", {}).get("name", "")})
        return subs
    except Exception as e:
        print(f"    ⚠️ squad: {e}")
        return []

async def fetch_player_stats(api, player_id, league_id, season_id):
    p     = Player(api, player_id)
    stats = await p.league_stats(league_id, season_id)
    return stats.get("statistics", {})

async def fetch_team_stats(api, team_id, league_id, season_id, games_played=None):
    """
    Récupère les stats agrégées d'équipe.
    games_played = nb de matchs joués (depuis current_round) pour des moyennes exactes.
    """
    try:
        t     = Team(api, team_id)
        data  = await t.league_stats(league_id, season_id)
        stats = data.get("statistics", {})

        # Priorité : games_played passé en param (depuis current_round)
        # Fallback : estimation via les passes (plus fiable que corners)
        if not games_played or games_played < 1:
            # Estimation : total passes / ~420 passes par match en moyenne
            total_passes = stats.get("totalPasses", 0)
            if total_passes > 0:
                games_played = max(1, round(total_passes / 420))
            else:
                # Dernier recours : corners / 5.0
                games_played = max(1, round(stats.get("corners", 5) / 5.0))

        n = games_played
        return {
            "shots_pm":     round(stats.get("shots", 0) / n, 2),
            "sot_pm":       round(stats.get("shotsOnTarget", 0) / n, 2),
            "goals_pm":     round(stats.get("goalsScored", 0) / n, 2),
            "conceded_pm":  round(stats.get("goalsConceded", 0) / n, 2),
            "shots_ag_pm":  round(stats.get("shotsAgainst", 0) / n, 2),
            "big_chances_pm": round(stats.get("bigChances", 0) / n, 2),
            "possession":   round(stats.get("averageBallPossession", 50), 1),
            "games_played": n,
        }
    except Exception as e:
        print(f"    ⚠️ team_stats: {e}")
        return {}

async def fetch_recent_shots(api, team_id, team_name, n=10):
    """
    Récupère les stats tirs des N derniers matchs d'une équipe.
    Triés chronologiquement pour avoir les N plus récents.
    """
    try:
        t        = Team(api, team_id)
        fixtures = await t.last_fixtures()
        events   = fixtures if isinstance(fixtures, list) else fixtures.get("events", [])

        # Filtre terminés + tri chronologique + N derniers
        finished = sorted(
            [e for e in events if e.get("status", {}).get("type") == "finished"],
            key=lambda x: x.get("startTimestamp", 0)
        )[-n:]

        shots_list, sot_list, xg_list, corners_list = [], [], [], []
        goals_for_list, goals_ag_list, btts_list, match_details = [], [], [], []

        for event in finished:
            mid     = event.get("id")
            is_home = event.get("homeTeam", {}).get("id") == team_id
            comp    = (event.get("tournament", {})
                           .get("uniqueTournament", {})
                           .get("name", "?"))
            home_n  = event.get("homeTeam", {}).get("shortName", "?")
            away_n  = event.get("awayTeam", {}).get("shortName", "?")
            from datetime import datetime as _dt
            ts_str  = _dt.fromtimestamp(event.get("startTimestamp", 0)).strftime("%d/%m/%y")
            # Buts depuis les scores du fixture (sans appel API supplémentaire)
            h_score    = event.get("homeScore", {}).get("current", 0) or 0
            a_score    = event.get("awayScore", {}).get("current", 0) or 0
            # Tirs aux buts : normaltime score
            h_norm     = event.get("homeScore", {}).get("normaltime")
            a_norm     = event.get("awayScore", {}).get("normaltime")
            h_pen      = event.get("homeScore", {}).get("penalties")
            a_pen      = event.get("awayScore", {}).get("penalties")
            has_pens   = h_pen is not None and a_pen is not None
            # Pour les buts marqués, utilise le score temps réglementaire si dispo
            gf_real = (h_norm if is_home else a_norm) if (h_norm is not None) else (h_score if is_home else a_score)
            ga_real = (a_norm if is_home else h_norm) if (a_norm is not None) else (a_score if is_home else h_score)
            gf = gf_real if gf_real is not None else (h_score if is_home else a_score)
            ga = ga_real if ga_real is not None else (a_score if is_home else h_score)
            goals_for_list.append(gf)
            goals_ag_list.append(ga)
            btts_list.append(1 if (gf > 0 and ga > 0) else 0)
            opp_name = away_n if is_home else home_n
            result   = "W" if gf > ga else ("D" if gf == ga else "L")
            if has_pens:
                pen_h = h_pen if is_home else a_pen
                pen_a = a_pen if is_home else h_pen
                score_str = f"{gf}-{ga} (pen {pen_h}-{pen_a})"
                result = "W" if pen_h > pen_a else "L"
            else:
                score_str = f"{gf}-{ga}"
            match_details.append({
                "date":   ts_str,
                "opp":    opp_name,
                "score":  score_str,
                "result": result,
                "home":   is_home,
                "comp":   comp[:15],
            })

            try:
                m     = Match(api, mid)
                stats = await m.stats()
                shots_found = sot_found = xg_found = corners_found = False
                for period in stats.get("statistics", []):
                    if period.get("period") != "ALL": continue
                    for group in period.get("groups", []):
                        for item in group.get("statisticsItems", []):
                            key = item.get("key", "")
                            val = item.get("homeValue" if is_home else "awayValue", 0)
                            if key == "totalShotsOnGoal" and not shots_found:
                                shots_list.append(val); shots_found = True
                            elif key == "shotsOnGoal" and not sot_found:
                                sot_list.append(val); sot_found = True
                            elif key == "expectedGoals" and not xg_found:
                                xg_list.append(float(val) if val else 0); xg_found = True
                            elif key == "cornerKicks" and not corners_found:
                                corners_list.append(val); corners_found = True
                side     = "DOM" if is_home else "EXT"
                shots_val = shots_list[-1] if shots_list else "?"
                sot_val   = sot_list[-1]   if sot_list   else "?"
                print(f"      {ts_str} [{comp[:12]:12}] {side} {home_n} vs {away_n} {gf}-{ga} → {shots_val} tirs ({sot_val} cadrés)")
                # Récupère les buteurs via incidents
                scorers = []
                try:
                    inc_data = await m.incidents()
                    for inc in inc_data.get("incidents", []):
                        if (inc.get("incidentType") == "goal"
                                and inc.get("isHome") == is_home
                                and inc.get("incidentClass") != "ownGoal"):
                            # Buteur
                            pname = (
                                inc.get("playerName")
                                or (inc.get("player") or {}).get("shortName")
                                or (inc.get("player") or {}).get("name")
                                or "?"
                            )
                            t   = inc.get("time", "")
                            cls = inc.get("incidentClass", "")
                            tag = " (pen)" if cls == "penalty" else ""
                            # Passeur décisif via assist1
                            assist = inc.get("assist1")
                            if not assist:
                                # Cherche dans footballPassingNetworkAction
                                for action in inc.get("footballPassingNetworkAction", []):
                                    if action.get("isAssist") and action.get("isHome") == is_home:
                                        assist = action.get("player")
                                        break
                            assist_name = assist.get("shortName") or assist.get("name") if assist else None
                            if assist_name:
                                scorers.append(f"{pname} {t}'{tag} ({assist_name})")
                            else:
                                scorers.append(f"{pname} {t}'{tag}")
                except Exception:
                    pass
                match_details[-1]["scorers"] = scorers
                await asyncio.sleep(0.4)
            except Exception:
                side = "DOM" if is_home else "EXT"
                print(f"      {ts_str} [{comp[:12]:12}] {side} {home_n} vs {away_n} {gf}-{ga}")

        if not shots_list:
            return {}

        def avg(lst): return round(sum(lst) / len(lst), 2) if lst else 0

        def trend(lst):
            if len(lst) < 6: return "→ stable"
            recent = sum(lst[-5:]) / 5
            older  = sum(lst[:-5]) / len(lst[:-5]) if lst[:-5] else recent
            if recent > older * 1.1: return "↑ en hausse"
            if recent < older * 0.9: return "↓ en baisse"
            return "→ stable"

        btts_count = sum(btts_list)
        btts_n     = len(btts_list)
        btts_rate  = round(btts_count / btts_n * 100) if btts_n else 0

        return {
            "shots_pm":      avg(shots_list),
            "sot_pm":        avg(sot_list) if sot_list else round(avg(shots_list) * 0.35, 2),
            "xg_pm":         avg(xg_list),
            "corners_pm":    avg(corners_list),
            "shots_trend":   trend(shots_list),
            "n_matches":     len(shots_list),
            "shots_raw":     shots_list,
            "goals_for_pm":  avg(goals_for_list),
            "goals_ag_pm":   avg(goals_ag_list),
            "total_goals_pm":round(avg(goals_for_list) + avg(goals_ag_list), 2),
            "btts_count":    btts_count,
            "btts_n":        btts_n,
            "btts_rate":     btts_rate,
            "goals_raw":     goals_for_list,
            "goals_ag_raw":  goals_ag_list,
            "btts_raw":      btts_list,
            "match_details":  match_details,
        }
    except Exception as e:
        print(f"    ⚠️ recent_shots {team_name}: {e}")
        return {}


async def fetch_h2h_shots(api, home_id, away_id, home_name, away_name):
    """
    Récupère les stats tirs des H2H historiques.
    Cherche dans les last_fixtures de l'équipe domicile les matchs contre l'adversaire.
    """
    try:
        t        = Team(api, home_id)
        fixtures = await t.last_fixtures()
        events   = fixtures if isinstance(fixtures, list) else fixtures.get("events", [])
        # Filtre les matchs contre l'adversaire (home ou away)
        h2h_events = [
            e for e in events
            if e.get("status", {}).get("type") == "finished"
            and (e.get("homeTeam", {}).get("id") == away_id
                 or e.get("awayTeam", {}).get("id") == away_id)
        ][-8:]
        finished = h2h_events

        h2h_stats = []
        for event in finished:
            mid  = event.get("id")
            hs   = event.get("homeScore", {}).get("current", 0)
            as_  = event.get("awayScore", {}).get("current", 0)
            try:
                ms    = Match(api, mid)
                stats = await ms.stats()
                shots_h = shots_a = xg_h = xg_a = 0
                for period in stats.get("statistics", []):
                    if period.get("period") != "ALL": continue
                    shots_done = xg_done = False
                    for group in period.get("groups", []):
                        for item in group.get("statisticsItems", []):
                            k = item.get("key", "")
                            if k == "totalShotsOnGoal" and not shots_done:
                                shots_h = item.get("homeValue", 0)
                                shots_a = item.get("awayValue", 0)
                                shots_done = True
                            elif k == "expectedGoals" and not xg_done:
                                xg_h = float(item.get("homeValue", 0) or 0)
                                xg_a = float(item.get("awayValue", 0) or 0)
                                xg_done = True
                h2h_stats.append({
                    "total_shots": shots_h + shots_a,
                    "total_goals": hs + as_,
                    "xg_total": round(xg_h + xg_a, 2),
                })
                await asyncio.sleep(0.3)
            except Exception:
                pass

        if not h2h_stats: return {}
        avg_shots = round(sum(x["total_shots"] for x in h2h_stats) / len(h2h_stats), 1)
        avg_goals = round(sum(x["total_goals"] for x in h2h_stats) / len(h2h_stats), 2)
        return {"avg_total_shots": avg_shots, "avg_total_goals": avg_goals,
                "n_matches": len(h2h_stats), "matches": h2h_stats}
    except Exception as e:
        print(f"    ⚠️ h2h_shots: {e}")
        return {}

async def process_team(api, lineup, team_id, lid, sid, label, dom_league_id=None, dom_season_id=None, dom_league_name=""):
    missing_ids = get_missing_ids(lineup)
    starters    = get_starters(lineup)
    starter_ids = {p["id"] for p in starters}
    subs        = await get_squad_subs(api, team_id, starter_ids, missing_ids)
    all_players = starters + subs
    print(f"  {label}: {len(starters)} titulaires + {len(subs)} remplaçants offensifs potentiels")

    results = []
    for p in all_players:
        pid = p["id"]
        try:
            stats       = await fetch_player_stats(api, pid, lid, sid)
            appearances = stats.get("appearances", 0)
            if appearances == 0: continue

            goals        = stats.get("goals", 0)
            assists      = stats.get("assists", 0)
            shots        = stats.get("totalShots", 0)
            shots_target = stats.get("shotsOnTarget", 0)
            xg           = stats.get("expectedGoals", 0)
            xa           = stats.get("expectedAssists", 0)

            player_data = {
                "id": pid, "name": p["name"], "shortName": p["shortName"],
                "position": p["position"], "is_sub": p["is_sub"],
                "appearances": appearances, "goals": goals, "assists": assists,
                "shots": shots, "shotsOnTarget": shots_target,
                "xG": round(xg, 3), "xA": round(xa, 3),
                "goals_pm":   round(goals / appearances, 3),
                "assists_pm": round(assists / appearances, 3),
                "shots_pm":   round(shots / appearances, 2),
                "sot_pm":     round(shots_target / appearances, 2),
                "xG_pm":      round(xg / appearances, 3),
                "xA_pm":      round(xa / appearances, 3),
                "g_a_pm":     round((goals + assists) / appearances, 3),
            }

            # Pour matchs européens : récupère aussi stats championnat
            if lid in CUP_LEAGUES and dom_league_id:
                try:
                    dom_stats = await fetch_player_stats(api, pid, dom_league_id, dom_season_id)
                    dom_apps  = dom_stats.get("appearances", 0)
                    if dom_apps > 0:
                        player_data["league_goals"]   = dom_stats.get("goals", 0)
                        player_data["league_assists"]  = dom_stats.get("assists", 0)
                        player_data["league_apps"]     = dom_apps
                        player_data["league_name"]     = dom_league_name
                except Exception:
                    pass
                await asyncio.sleep(0.3)

            results.append(player_data)
            sub_tag = " 🔄" if p["is_sub"] else ""
            print(f"    ✅ {p['shortName']}{sub_tag} — {goals}G {assists}A {round(xg,2)}xG")
            await asyncio.sleep(0.4)
        except Exception as e:
            print(f"    ⚠️ {p['shortName']} : {e}")
            await asyncio.sleep(0.2)

    return results

# ─── Filtre aujourd'hui ─────────────────────────────────────────────────────

def is_today(timestamp):
    if not timestamp: return False
    match_date = datetime.fromtimestamp(timestamp).date()
    today      = datetime.now().date()
    return match_date == today

# ─── Main ───────────────────────────────────────────────────────────────────

async def main():
    # Charge matches.json existant
    with open("data/matches.json", encoding="utf-8") as f:
        all_matches = json.load(f)

    # Filtre uniquement les matchs d'aujourd'hui
    today_str  = datetime.now().strftime("%d/%m/%Y")
    today_matches = [m for m in all_matches if is_today(m.get("start_ts"))]

    if not today_matches:
        print(f"⚠️ Aucun match aujourd'hui ({today_str}) dans matches.json")
        print(f"   Lance d'abord scraper.py pour récupérer les fixtures")
        return

    print(f"📅 {len(today_matches)} matchs aujourd'hui ({today_str}) :")
    for m in today_matches:
        t = datetime.fromtimestamp(m["start_ts"]).strftime("%H:%M")
        print(f"  {t} · {m['home']} vs {m['away']} ({m['league']})")

    api = SofascoreAPI()
    print("\n⏳ Saisons courantes...")
    season_ids = await get_season_ids(api)

    # Charge player_stats.json existant si présent (pour ne pas écraser les autres jours)
    ps_path = "data/player_stats.json"
    if os.path.exists(ps_path):
        with open(ps_path, encoding="utf-8") as f:
            all_stats = json.load(f)
    else:
        all_stats = {}

    for match in today_matches:
        league   = match["league"]
        match_id = match["id"]
        home     = match["home"]
        away     = match["away"]

        if league not in season_ids:
            print(f"\n⚠️ {home} vs {away} — ligue '{league}' non trouvée, ignoré")
            continue

        lid = season_ids[league]["league_id"]
        sid = season_ids[league]["season_id"]
        t   = datetime.fromtimestamp(match["start_ts"]).strftime("%H:%M")

        print(f"\n{'='*50}")
        print(f"⏳ {t} · {home} vs {away} ({league})")
        print(f"{'='*50}")

        home_id = match.get("home_id")
        away_id = match.get("away_id")

        # Pour coupes d'europe : games_played est faux (rounds != matchs joués réels)
        # On passe None pour forcer l'estimation via les passes
        games_played = None if lid in CUP_LEAGUES else season_ids[league].get("games_played")
        games_label  = "matchs joués" if games_played else "estimation"
        print(f"\n📊 Stats saison ({games_played or 'auto'} {games_label})...")
        home_team_stats = await fetch_team_stats(api, home_id, lid, sid, games_played)
        away_team_stats = await fetch_team_stats(api, away_id, lid, sid, games_played)
        if home_team_stats:
            print(f"  🏠 {home}: {home_team_stats['shots_pm']} tirs/match (saison)")
        if away_team_stats:
            print(f"  ✈️  {away}: {away_team_stats['shots_pm']} tirs/match (saison)")

        print(f"\n📈 Forme récente (10 derniers matchs)...")
        home_recent = await fetch_recent_shots(api, home_id, home)
        away_recent = await fetch_recent_shots(api, away_id, away)
        if home_recent.get("shots_pm"):
            print(f"  🏠 {home}: {home_recent['shots_pm']} tirs/match ({home_recent.get('sot_pm','?')}) cadrés · {home_recent.get('shots_trend','')} ({home_recent.get('n_matches',0)} matchs)")
        if away_recent.get("shots_pm"):
            print(f"  ✈️  {away}: {away_recent['shots_pm']} tirs/match ({away_recent.get('sot_pm','?')}) cadrés · {away_recent.get('shots_trend','')} ({away_recent.get('n_matches',0)} matchs)")

        print(f"\n⚔️  H2H tirs...")
        h2h_shots = await fetch_h2h_shots(api, home_id, away_id, home, away)
        if h2h_shots.get("avg_total_shots"):
            print(f"  H2H: ~{h2h_shots['avg_total_shots']} tirs/match ({h2h_shots['n_matches']} confrontations)")

        print(f"\n👥 Joueurs...")
        home_lineup  = match.get("lineups_home") or {}
        away_lineup  = match.get("lineups_away") or {}
        # Pour coupes européennes : pré-calcule le championnat domestique
        dom_h_lid = dom_h_sid = dom_h_name = None
        dom_a_lid = dom_a_sid = dom_a_name = None
        if lid in CUP_LEAGUES:
            # Détecte championnat depuis le pays de l'équipe via team stats
            for team_id_loop, side_label in [(home_id, "home"), (away_id, "away")]:
                try:
                    from sofascore_wrapper.team import Team as _Team
                    t_info = await _Team(api, team_id_loop).get_team()
                    country = (t_info.get("team") or t_info).get("country", {}).get("name", "")
                    dom = COUNTRY_TO_LEAGUE.get(country)
                    if dom:
                        d_lid, d_name = dom
                        d_league = League(api, d_lid)
                        d_season = await d_league.current_season()
                        if side_label == "home":
                            dom_h_lid, dom_h_sid, dom_h_name = d_lid, d_season["id"], d_name
                        else:
                            dom_a_lid, dom_a_sid, dom_a_name = d_lid, d_season["id"], d_name
                        print(f"  → {side_label} championnat: {d_name} (lid={d_lid})")
                except Exception:
                    pass

        home_players = await process_team(api, home_lineup, home_id, lid, sid, f"🏠 {home}",
                                          dom_h_lid, dom_h_sid, dom_h_name or "")
        away_players = await process_team(api, away_lineup, away_id, lid, sid, f"✈️  {away}",
                                          dom_a_lid, dom_a_sid, dom_a_name or "")

        all_stats[str(match_id)] = {
            "home":            home_players,
            "away":            away_players,
            "home_team_stats": home_team_stats,
            "away_team_stats": away_team_stats,
            "home_recent":     home_recent,
            "away_recent":     away_recent,
            "h2h_shots":       h2h_shots,
        }

        # Sauvegarde incrémentale après chaque match
        with open(ps_path, "w", encoding="utf-8") as f:
            json.dump(all_stats, f, ensure_ascii=False, indent=2)
        print(f"  💾 Sauvegardé")

    await api.close()

    total = sum(len(v["home"]) + len(v["away"]) for k, v in all_stats.items()
                if k in [str(m["id"]) for m in today_matches])
    print(f"\n🎉 Terminé — {total} joueurs scrapés pour {len(today_matches)} matchs du jour")
    print(f"   Lance maintenant : python generate_site.py")

asyncio.run(main())