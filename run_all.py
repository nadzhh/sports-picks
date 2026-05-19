"""
run_all.py — Pipeline complet
1. scraper.py             (matchs J et J+1 via api-football)
2. scraper_players_today.py (stats joueurs)
3. generate_site.py       (index.html + push GitHub Pages)
"""

import subprocess
import sys
import os
from datetime import datetime
from pathlib import Path

PYTHON = sys.executable
DIR    = Path(__file__).parent


def run(script, description):
    print(f"\n{'='*50}")
    print(f"  {description}...")
    print(f"{'='*50}")
    # Force utf-8 pour eviter les crashes emoji sur Windows
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [PYTHON, script],
        cwd=str(DIR),
        timeout=1200,
        env=env,
    )
    if result.returncode != 0:
        print(f"[X] Erreur dans {script} (code {result.returncode})")
        return False
    return True


def main():
    start = datetime.now()
    print(f"\nDemarrage pipeline — {start.strftime('%d/%m/%Y %H:%M')}")

    matches_path = DIR / "data" / "matches.json"
    ps_path      = DIR / "data" / "player_stats.json"

    # 0. Foot resolver (resout les picks foot d'hier avant de tout regenerer)
    run("foot_resolver.py", "Resolveur picks foot")

    # 1. Fixtures + odds + h2h + form (api-football, ~30-50 reqs cold, 5-10 warm)
    if not run("scraper.py", "Recuperation des matchs (api-football)"):
        print("[!] Le pipeline s'arrete ici. Tes donnees existantes sont preservees.")
        sys.exit(1)

    # 2. Stats joueurs (backup avant ecrasement)
    if ps_path.exists():
        backup_path = ps_path.with_suffix(".backup.json")
        if backup_path.exists():
            backup_path.unlink()
        ps_path.rename(backup_path)
        print("Backup player_stats.json -> .backup.json")
    if not run("scraper_players_today.py", "Stats joueurs"):
        backup = ps_path.with_suffix(".backup.json")
        if backup.exists() and not ps_path.exists():
            backup.rename(ps_path)
            print("Restauration du backup player_stats.json")

    # 3. NBA (stats.nba.com, gratuit)
    # Resolveur picks historiques (avant tout) - resout les picks d'hier
    run("nba_resolver.py", "NBA resolveur (resoud picks pending)")
    if not run("nba_scraper.py", "NBA scraper (stats.nba.com)"):
        print("[!] NBA scraper a echoue, on continue avec football seul")
    else:
        # Odds reelles via The Odds API (optionnel, si ODDS_API_KEY)
        run("nba_odds.py", "NBA odds bookmaker (DK/FD via Odds API)")
        run("nba_picks_engine.py", "NBA picks engine")

    # 4. Genere le site + push GitHub
    if not run("generate_site.py", "Generation du site"):
        sys.exit(1)

    elapsed = (datetime.now() - start).seconds
    print(f"\nPipeline OK en {elapsed//60}m{elapsed%60}s")
    print(f"Site local: {DIR / 'index.html'}")
    print(f"En ligne:   https://nadzhh.github.io/sports-picks/")


if __name__ == "__main__":
    main()
