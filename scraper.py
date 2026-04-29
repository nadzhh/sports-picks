import asyncio
import json
import os
import time
from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.league import League
from sofascore_wrapper.match import Match

os.makedirs("data", exist_ok=True)

LIGUES = {
    "Premier League":      17,
    "La Liga":             8,
    "Bundesliga":          35,
    "Serie A":             23,
    "Ligue 1":             34,
    "Champions League":    7,
    "Europa League":       679,
    "Conference League":   17015,
}

# Compétitions à format coupe (phases élim, rounds variables)
CUP_LEAGUES = {7, 679, 17015}

async def fetch_match_data(api, match):
    """Récupère toutes les données pre-match disponibles."""
    match_id   = match.get("id")
    home       = match.get("homeTeam", {}).get("name", "?")
    away       = match.get("awayTeam", {}).get("name", "?")
    home_id    = match.get("homeTeam", {}).get("id")
    away_id    = match.get("awayTeam", {}).get("id")
    start_ts   = match.get("startTimestamp")

    m = Match(api, match_id)
    data = {
        "id":         match_id,
        "home":       home,
        "away":       away,
        "home_id":    home_id,
        "away_id":    away_id,
        "start_ts":   start_ts,
    }

    for methode in ["h2h", "pre_match_form", "lineups_home",
                    "lineups_away", "match_odds", "team_streaks"]:
        try:
            result = await getattr(m, methode)()
            data[methode] = result
        except Exception:
            data[methode] = None

    return data

async def main():
    api = SofascoreAPI()
    all_matches = []

    import time as _time
    now_ts = _time.time()

    for nom, league_id in LIGUES.items():
        print(f"⏳ {nom}...")
        try:
            league   = League(api, league_id)
            fixtures = []
            season   = await league.current_season()
            sid      = season["id"]
            cur_rnd  = await league.current_round(sid)

            # Construit le mapping round → slug pour les coupes
            round_slugs = {}
            if league_id in CUP_LEAGUES:
                try:
                    rounds_data = await league.rounds(sid)
                    if isinstance(rounds_data, dict):
                        for r in rounds_data.get("rounds", []):
                            rnd_n = r.get("round"); slug = r.get("slug")
                            if rnd_n and slug:
                                round_slugs[rnd_n] = slug
                except Exception:
                    pass

            # Cherche à partir du round courant jusqu'à +5 rounds
            for rnd in range(cur_rnd, cur_rnd + 6):
                events = []
                if league_id in CUP_LEAGUES and rnd in round_slugs:
                    # Coupe : appel direct avec le vrai slug
                    slug = round_slugs[rnd]
                    url  = f"/unique-tournament/{league_id}/season/{sid}/events/round/{rnd}/slug/{slug}"
                    try:
                        resp   = await league.api._get(url)
                        events = resp.get("events", []) if isinstance(resp, dict) else []
                    except Exception:
                        pass
                else:
                    # Ligue normale (ou coupe sans slug connu)
                    try:
                        resp   = await league.league_fixtures_per_round(sid, rnd)
                        events = resp.get("events", []) if isinstance(resp, dict) else []
                    except Exception:
                        pass

                upcoming = [e for e in events if e.get("startTimestamp", 0) > now_ts]
                if upcoming:
                    fixtures = upcoming
                    label = round_slugs.get(rnd, f"round {rnd}")
                    print(f"  → {label}: {len(fixtures)} matchs à venir")
                    break

            if not fixtures:
                print(f"  ⚠️ Aucun match à venir")
                continue

            print(f"  → {len(fixtures)} matchs, récupération des données...")
            for fixture in fixtures:
                try:
                    match_data = await fetch_match_data(api, fixture)
                    match_data["league"] = nom
                    all_matches.append(match_data)
                    print(f"    ✅ {match_data['home']} vs {match_data['away']}")
                except Exception as e:
                    print(f"    ❌ {e}")
                await asyncio.sleep(1)

        except Exception as e:
            print(f"  ❌ {e}")

    await api.close()

    with open("data/matches.json", "w", encoding="utf-8") as f:
        json.dump(all_matches, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 {len(all_matches)} matchs sauvegardés → data/matches.json")

asyncio.run(main())