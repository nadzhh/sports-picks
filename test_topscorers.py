import asyncio, json
from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.league import League

async def main():
    api = SofascoreAPI()
    league = League(api, 17)
    season = await league.current_season()
    print("Saison actuelle:", json.dumps(season, indent=2))
    await api.close()

asyncio.run(main())