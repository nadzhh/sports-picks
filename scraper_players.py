"""
scraper_players.py — v3
- Titulaires du lineup (tous sauf gardien)
- Si subs vides → squad complet pour trouver les offensifs potentiels banc
- Exclusion des blessés (missing_players)
- Flag is_sub=True pour les remplaçants potentiels
"""

import asyncio, json, os
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

SKIP_POSITIONS   = {"G"}
OFFENSIVE_POSITIONS = {"F", "M"}

async def get_season_ids(api):
    season_ids = {}
    for name, lid in LEAGUE_IDS.items():
        try:
            league = League(api, lid)
            season = await league.current_season()
            season_ids[name] = {"league_id": lid, "season_id": season["id"]}
            print(f"  ✅ {name} → {season['name']} (id={season['id']})")
        except Exception as e:
            print(f"  ❌ {name} : {e}")
    return season_ids

def get_missing_ids(lineup):
    """IDs des joueurs blessés/suspendus."""
    return {
        s.get("player", {}).get("id")
        for s in lineup.get("missing_players", [])
    }

def get_starters(lineup):
    """Tous les titulaires sauf gardien."""
    players = []
    for s in lineup.get("starters", []):
        p   = s.get("player", {})
        pos = p.get("position", "")
        pid = p.get("id")
        if pid and pos not in SKIP_POSITIONS:
            players.append({
                "id":        pid,
                "name":      p.get("name"),
                "shortName": p.get("shortName", p.get("name")),
                "position":  pos,
                "is_sub":    False,
            })
    return players

async def get_squad_subs(api, team_id, starter_ids, missing_ids):
    """
    Récupère le squad complet et retourne les offensifs
    qui ne sont ni titulaires ni blessés → remplaçants potentiels.
    """
    try:
        team  = Team(api, team_id)
        squad = await team.squad()
        subs  = []
        for entry in squad.get("players", []):
            p   = entry.get("player", {})
            pid = p.get("id")
            pos = p.get("position", "")
            if (pid and
                pos in OFFENSIVE_POSITIONS and
                pid not in starter_ids and
                pid not in missing_ids):
                subs.append({
                    "id":        pid,
                    "name":      p.get("name"),
                    "shortName": p.get("shortName", p.get("name")),
                    "position":  pos,
                    "is_sub":    True,
                })
        return subs
    except Exception as e:
        print(f"    ⚠️ squad: {e}")
        return []

async def fetch_stats(api, player_id, league_id, season_id):
    p     = Player(api, player_id)
    stats = await p.league_stats(league_id, season_id)
    return stats.get("statistics", {})

async def process_team(api, lineup, team_id, lid, sid, label):
    """Retourne la liste complète des joueurs avec leurs stats."""
    missing_ids = get_missing_ids(lineup)
    starters    = get_starters(lineup)
    starter_ids = {p["id"] for p in starters}

    # Remplaçants offensifs potentiels depuis le squad
    subs = await get_squad_subs(api, team_id, starter_ids, missing_ids)

    all_players = starters + subs
    print(f"  {label}: {len(starters)} titulaires + {len(subs)} remplaçants offensifs potentiels")

    results = []
    for p in all_players:
        pid = p["id"]
        try:
            stats       = await fetch_stats(api, pid, lid, sid)
            appearances = stats.get("appearances", 0)
            if appearances == 0:
                continue

            goals        = stats.get("goals", 0)
            assists      = stats.get("assists", 0)
            shots        = stats.get("totalShots", 0)
            shots_target = stats.get("shotsOnTarget", 0)
            xg           = stats.get("expectedGoals", 0)
            xa           = stats.get("expectedAssists", 0)
            key_passes   = stats.get("keyPasses", 0)
            minutes      = stats.get("minutesPlayed", 0)
            rating       = stats.get("rating", 0)

            results.append({
                "id":            pid,
                "name":          p["name"],
                "shortName":     p["shortName"],
                "position":      p["position"],
                "is_sub":        p["is_sub"],
                "appearances":   appearances,
                "goals":         goals,
                "assists":       assists,
                "shots":         shots,
                "shotsOnTarget": shots_target,
                "xG":            round(xg, 3),
                "xA":            round(xa, 3),
                "keyPasses":     key_passes,
                "minutes":       minutes,
                "rating":        round(rating, 2),
                "goals_pm":      round(goals / appearances, 3),
                "assists_pm":    round(assists / appearances, 3),
                "shots_pm":      round(shots / appearances, 2),
                "sot_pm":        round(shots_target / appearances, 2),
                "xG_pm":         round(xg / appearances, 3),
                "xA_pm":         round(xa / appearances, 3),
                "g_a_pm":        round((goals + assists) / appearances, 3),
            })

            sub_tag = " 🔄sub" if p["is_sub"] else ""
            print(f"    ✅ {p['shortName']}{sub_tag} — {goals}G {assists}A {round(xg,2)}xG ({appearances} matchs)")
            await asyncio.sleep(0.4)

        except Exception as e:
            print(f"    ⚠️ {p['shortName']} : {e}")
            await asyncio.sleep(0.2)

    return results

async def fetch_team_stats(api, team_id, league_id, season_id):
    """Récupère les stats agrégées d'équipe (tirs, possession...)."""
    try:
        t     = Team(api, team_id)
        data  = await t.league_stats(league_id, season_id)
        stats = data.get("statistics", {})

        shots     = stats.get("shots", 0)
        sot       = stats.get("shotsOnTarget", 0)
        goals     = stats.get("goalsScored", 0)
        conceded  = stats.get("goalsConceded", 0)
        shots_ag  = stats.get("shotsAgainst", 0)
        sot_ag    = stats.get("shotsOnTargetAgainst",
                    stats.get("savedShotsFromInsideTheBox", 0))
        big_ch    = stats.get("bigChances", 0)
        possession= stats.get("averageBallPossession", 50)
        cs        = stats.get("cleanSheets", 0)

        # Estimation matchs joués (via corners comme proxy si dispo)
        # Sofascore ne retourne pas directement "matchesPlayed" dans team stats
        # On l'estime à partir des buts concédés / moyenne ligue (~1.3/match)
        # Ou on utilise corners / ~5 corners par match
        corners   = stats.get("corners", 1)
        est_games = max(1, round(corners / 5.2))  # ~5.2 corners/match en moyenne

        return {
            "shots_pm":      round(shots / est_games, 2),
            "sot_pm":        round(sot / est_games, 2),
            "goals_pm":      round(goals / est_games, 2),
            "conceded_pm":   round(conceded / est_games, 2),
            "shots_ag_pm":   round(shots_ag / est_games, 2),
            "big_chances_pm":round(big_ch / est_games, 2),
            "possession":    round(possession, 1),
            "clean_sheets":  cs,
            "est_games":     est_games,
            "raw": {
                "shots": shots, "sot": sot,
                "shots_against": shots_ag, "goals": goals,
                "conceded": conceded, "corners": corners,
            }
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
                print(f"      {ts_str} [{comp[:12]:12}] {side} {home_n} vs {away_n} → {shots_val} tirs ({sot_val} cadrés)")
                await asyncio.sleep(0.3)
            except Exception:
                pass

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

        return {
            "shots_pm":    avg(shots_list),
            "sot_pm":      avg(sot_list) if sot_list else round(avg(shots_list) * 0.35, 2),
            "xg_pm":       avg(xg_list),
            "corners_pm":  avg(corners_list),
            "shots_trend": trend(shots_list),
            "n_matches":   len(shots_list),
            "shots_raw":   shots_list,
        }
    except Exception as e:
        print(f"    ⚠️ recent_shots {team_name}: {e}")
        return {}


async def fetch_h2h_shots(api, match_id):
    """
    Récupère les stats tirs des derniers H2H depuis le match à venir.
    """
    try:
        m    = Match(api, match_id)
        h2h  = await m.h2h()
        events = h2h.get("events", [])
        finished = [e for e in events if e.get("status", {}).get("type") == "finished"][-8:]

        h2h_stats = []
        for event in finished:
            mid  = event.get("id")
            home = event.get("homeTeam", {}).get("name", "")
            away = event.get("awayTeam", {}).get("name", "")
            hs   = event.get("homeScore", {}).get("current", 0)
            as_  = event.get("awayScore", {}).get("current", 0)
            try:
                ms    = Match(api, mid)
                stats = await ms.stats()
                shots_h = shots_a = xg_h = xg_a = 0
                for period in stats.get("statistics", []):
                    if period.get("period") != "ALL": continue
                    for group in period.get("groups", []):
                        shots_done = xg_done = False
                    for item in group.get("statisticsItems", []):
                            k = item.get("key","")
                            if k == "totalShotsOnGoal" and not shots_done:
                                shots_h = item.get("homeValue", 0)
                                shots_a = item.get("awayValue", 0)
                                shots_done = True
                            elif k == "expectedGoals" and not xg_done:
                                xg_h = float(item.get("homeValue", 0) or 0)
                                xg_a = float(item.get("awayValue", 0) or 0)
                                xg_done = True
                h2h_stats.append({
                    "home": home, "away": away,
                    "score": f"{hs}-{as_}",
                    "shots_home": shots_h, "shots_away": shots_a,
                    "xg_home": round(xg_h,2), "xg_a": round(xg_a,2),
                    "total_shots": shots_h + shots_a,
                    "total_goals": hs + as_,
                })
                await asyncio.sleep(0.3)
            except Exception:
                pass

        if not h2h_stats: return {}

        avg_shots = round(sum(x["total_shots"] for x in h2h_stats) / len(h2h_stats), 1)
        avg_goals = round(sum(x["total_goals"] for x in h2h_stats) / len(h2h_stats), 2)
        return {
            "avg_total_shots": avg_shots,
            "avg_total_goals": avg_goals,
            "n_matches": len(h2h_stats),
            "matches": h2h_stats,
        }
    except Exception as e:
        print(f"    ⚠️ h2h_shots: {e}")
        return {}


async def main():
    with open("data/matches.json", encoding="utf-8") as f:
        matches = json.load(f)

    api = SofascoreAPI()
    print("⏳ Saisons courantes...")
    season_ids = await get_season_ids(api)

    all_stats = {}

    for match in matches:
        league   = match["league"]
        match_id = match["id"]
        home     = match["home"]
        away     = match["away"]

        if league not in season_ids:
            continue

        lid = season_ids[league]["league_id"]
        sid = season_ids[league]["season_id"]

        print(f"\n⏳ {home} vs {away}")

        home_lineup = match.get("lineups_home") or {}
        away_lineup = match.get("lineups_away") or {}
        home_id     = match.get("home_id")
        away_id     = match.get("away_id")

        # Stats équipe (saison + forme récente + H2H)
        games_played = season_ids[league].get("games_played")
        print(f"  📊 Stats équipe saison ({games_played} matchs joués)...")
        home_team_stats = await fetch_team_stats(api, home_id, lid, sid, games_played)
        away_team_stats = await fetch_team_stats(api, away_id, lid, sid, games_played)

        print(f"  📈 Forme récente tirs (10 derniers matchs)...")
        home_recent = await fetch_recent_shots(api, home_id, home)
        away_recent = await fetch_recent_shots(api, away_id, away)

        print(f"  ⚔️ H2H tirs...")
        h2h_shots = await fetch_h2h_shots(api, match_id)

        if home_recent.get("shots_pm"):
            print(f"    🏠 {home}: {home_recent['shots_pm']} tirs/match (forme) {home_recent.get('shots_trend','')} sur {home_recent.get('n_matches',0)} matchs")
        if away_recent.get("shots_pm"):
            print(f"    ✈️ {away}: {away_recent['shots_pm']} tirs/match (forme) {away_recent.get('shots_trend','')} sur {away_recent.get('n_matches',0)} matchs")
        if h2h_shots.get("avg_total_shots"):
            print(f"    ⚔️ H2H: ~{h2h_shots['avg_total_shots']} tirs/match en moyenne")

        home_players = await process_team(api, home_lineup, home_id, lid, sid, f"🏠 {home}")
        away_players = await process_team(api, away_lineup, away_id, lid, sid, f"✈️ {away}")

        all_stats[str(match_id)] = {
            "home":            home_players,
            "away":            away_players,
            "home_team_stats": home_team_stats,
            "away_team_stats": away_team_stats,
            "home_recent":     home_recent,
            "away_recent":     away_recent,
            "h2h_shots":       h2h_shots,
        }

    await api.close()

    with open("data/player_stats.json", "w", encoding="utf-8") as f:
        json.dump(all_stats, f, ensure_ascii=False, indent=2)

    total = sum(len(v["home"]) + len(v["away"]) for v in all_stats.values())
    print(f"\n🎉 {total} joueurs → data/player_stats.json")

asyncio.run(main())