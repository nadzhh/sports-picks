"""
config.py — Configuration sport-picks

Aucun secret n'est commit ici. Les valeurs sont lues depuis :
  1. les variables d'environnement (utilise par GitHub Actions via Secrets)
  2. un fichier .env local (gitignore) au format KEY=VALUE pour le dev local

Si une valeur est vide, le script concerne se desactive proprement
(ex: notify.py n'envoie rien si TELEGRAM_BOT_TOKEN est vide).
"""
import os
from pathlib import Path


def _load_dotenv():
    """Charge data/.env ou .env dans os.environ si present (dev local)."""
    for candidate in [Path(__file__).parent / ".env", Path(__file__).parent / "data" / ".env"]:
        if not candidate.exists(): continue
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line: continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass


_load_dotenv()


def env_or(name, default=""):
    """Retourne os.environ[name] si defini et non vide, sinon default."""
    v = os.environ.get(name)
    return v if (v and v.strip()) else default


# ─── api-football (api-sports.io) — utilise uniquement pour les odds ──────────
API_KEY  = env_or("API_KEY")
API_BASE = "https://v3.football.api-sports.io"

# ─── The Odds API (https://the-odds-api.com) — lignes NBA player props ───────
# Gratuit 500 req/mois par cle. On separe les pools par sport pour eviter
# qu'un module bouffe le quota de l'autre :
#   - NBA   : clefs #1 et #2 (ODDS_API_KEY, ODDS_API_KEY2)
#   - Foot  : clef #3 (ODDS_API_KEY3)
# En cas d'epuisement d'un pool, on fallback transparent sur l'autre pool
# (rotation gracieuse pour ne pas tomber en heuristique trop vite).
ODDS_API_KEY  = env_or("ODDS_API_KEY")
ODDS_API_KEY2 = env_or("ODDS_API_KEY2")
ODDS_API_KEY3 = env_or("ODDS_API_KEY3")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Pools dedies par sport
ODDS_API_KEYS_NBA  = [k for k in [ODDS_API_KEY, ODDS_API_KEY2] if k]
ODDS_API_KEYS_FOOT = [k for k in [ODDS_API_KEY3] if k]

# Legacy : liste totale (utilisee si appel sans pool explicite)
ODDS_API_KEYS = [k for k in [ODDS_API_KEY, ODDS_API_KEY2, ODDS_API_KEY3] if k]

# ─── Telegram bot (notifications) ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = env_or("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = env_or("TELEGRAM_CHAT_ID")

# ─── RapidAPI - Tank01 Fantasy Stats (NBA injuries + DvP + projections) ─────
# Free tier : 1000 req/mois. Cache agressif pour rester ~300/mois.
RAPIDAPI_KEY    = env_or("RAPIDAPI_KEY")
TANK01_API_HOST = "tank01-fantasy-stats.p.rapidapi.com"


# ─── FotMob IDs ──────────────────────────────────────────────────────────────
# league_id, season_id pour endpoint /stats/{lid}/season/{sid}/{stat}.json
FOTMOB_LEAGUES = {
    "Premier League":      {"id": 47,    "name_match": "Premier League"},
    "La Liga":             {"id": 87,    "name_match": "LaLiga"},
    "Bundesliga":          {"id": 54,    "name_match": "Bundesliga"},
    "Serie A":             {"id": 55,    "name_match": "Serie A"},
    "Ligue 1":             {"id": 53,    "name_match": "Ligue 1"},
    "Champions League":    {"id": 42,    "name_match": "Champions League"},
    "Europa League":       {"id": 73,    "name_match": "Europa League"},
    "Conference League":   {"id": 10216, "name_match": "Conference League"},
    # Coupes nationales (FotMob ids verifies via API). Les coupes n'ont pas
    # de "stats" mais les fixtures+events restent accessibles.
    "FA Cup":              {"id": 132,   "name_match": "FA Cup"},
    "Coupe de France":     {"id": 134,   "name_match": "Coupe de France"},
    "Copa del Rey":        {"id": 138,   "name_match": "Copa del Rey"},
    "Coppa Italia":        {"id": 141,   "name_match": "Coppa Italia"},
    "DFB Pokal":           {"id": 209,   "name_match": "DFB-Pokal"},
    # Amerique du Sud + USA + competitions continentales (verifies via FotMob 2026)
    "Brasileirao":         {"id": 268,   "name_match": "Serie A"},        # 380 fixtures/saison
    "Argentina Primera":   {"id": 112,   "name_match": "Liga Profesional"},
    "Colombia Primera A":  {"id": 274,   "name_match": "Primera A"},
    "Copa Libertadores":   {"id": 45,    "name_match": "Copa Libertadores"},
    "MLS":                 {"id": 130,   "name_match": "MLS"},
}

# Pour mapper FotMob league id -> "internal id" picks_engine (utilise CUP_LEAGUES)
INTERNAL_LEAGUE_IDS = {
    "Premier League":    17,
    "La Liga":           8,
    "Bundesliga":        35,
    "Serie A":           23,
    "Ligue 1":           34,
    "Champions League":  7,
    "Europa League":     679,
    "Conference League": 17015,
    "FA Cup":            45,
    "Coupe de France":   66,
    "Copa del Rey":      143,
    "Coppa Italia":      137,
    "DFB Pokal":         81,
    # SA + MLS + Libertadores (internal id = api-football id pour simplicite)
    "Brasileirao":       71,
    "Argentina Primera": 128,
    "Colombia Primera A":239,
    "Copa Libertadores": 13,
    "MLS":               253,
}
# Toutes les coupes (matchs a elimination - traitement special : pas de classement utile)
CUP_LEAGUES = {7, 679, 17015, 45, 66, 143, 137, 81, 13}

# IDs api-football (pour endpoint odds) — saison 2025
APIFOOTBALL_LEAGUES = {
    "Premier League":    39,
    "La Liga":           140,
    "Bundesliga":        78,
    "Serie A":           135,
    "Ligue 1":           61,
    "Champions League":  2,
    "Europa League":     3,
    "Conference League": 848,
    "FA Cup":            45,
    "Coupe de France":   66,
    "Copa del Rey":      143,
    "Coppa Italia":      137,
    "DFB Pokal":         81,
    # SA + MLS + Libertadores (IDs api-football reels)
    "Brasileirao":       71,
    "Argentina Primera": 128,
    "Colombia Primera A":239,
    "Copa Libertadores": 13,
    "MLS":               253,
}

# ─── TTL cache (secondes) ────────────────────────────────────────────────────
TTL = {
    # api-football
    "fixtures_date":   6 * 3600,
    "odds":            6 * 3600,
    "predictions":     6 * 3600,
    "fixture_stats":   30 * 24 * 3600,
    # generique
    "team_stats":      7 * 24 * 3600,
    "h2h":             7 * 24 * 3600,
    "lineups":              30 * 60,
    "top_players":     7 * 24 * 3600,
    "team_players":    3 * 24 * 3600,
    "standings":      24 * 3600,
}
