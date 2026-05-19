"""
config.py — Configuration sport-picks

Les valeurs ci-dessous sont les defauts pour le dev local. Sur GitHub Actions,
les variables d'environnement (homonymes) prennent le pas via env_or().
"""
import os

def env_or(name, default):
    """Retourne os.environ[name] si defini et non vide, sinon default."""
    v = os.environ.get(name)
    return v if (v and v.strip()) else default

# ─── api-football (api-sports.io) — utilise uniquement pour les odds ──────────
API_KEY  = env_or("API_KEY", "7c8c61e65bc47178790aab94763e96c5")
API_BASE = "https://v3.football.api-sports.io"

# ─── The Odds API (https://the-odds-api.com) — lignes NBA player props ───────
# Gratuit 500 req/mois.
ODDS_API_KEY  = env_or("ODDS_API_KEY", "a35e3286e887969427b9cdc1444ed37f")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# ─── Telegram bot (notifications) ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = env_or("TELEGRAM_BOT_TOKEN", "8812256746:AAFXFOALPvTTckvb4ak1JUQovE79_2FuLDE")
TELEGRAM_CHAT_ID   = env_or("TELEGRAM_CHAT_ID",   "1041292568")

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
}
CUP_LEAGUES = {7, 679, 17015}

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
