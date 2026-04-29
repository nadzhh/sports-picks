import asyncio
from sofascore_wrapper.api import SofascoreAPI
from sofascore_wrapper.match import Match
from sofascore_wrapper.player import Player

async def main():
    api = SofascoreAPI()
    
    print("=== Méthodes Match ===")
    m = Match(api, 14025044)
    print([x for x in dir(m) if not x.startswith('_')])
    
    print("\n=== Méthodes Player ===")
    p = Player(api, 341209)
    print([x for x in dir(p) if not x.startswith('_')])
    
    await api.close()

asyncio.run(main())