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
    # SA suite (ids confirmes via /api/data/matches?date=YYYYMMDD)
    "Ecuador LigaPro":     {"id": 246,   "name_match": "Serie A"},        # ECU, 240 fixtures
    "Chile Primera":       {"id": 273,   "name_match": "Liga de Primera"},# CHI, 240 fixtures
    "Peru Liga 1":         {"id": 131,   "name_match": "Liga 1"},         # PER, 153 fixtures
    "Uruguay Primera":     {"id": 161,   "name_match": "Primera Division"},# URU, 151
    "Paraguay Profesional":{"id": 199,   "name_match": "Division Profesional"}, # PAR, 138
    # Europe : ligues secondaires + Scandinavie + Europe de l'Est (verifies live 2026)
    # Volume eleve, value souvent meilleure (bookmakers moins efficacement priced)
    "Eredivisie":              {"id": 57,    "name_match": "Eredivisie"},        # NED, 309 fix
    "Liga Portugal":           {"id": 61,    "name_match": "Liga Portugal"},     # POR, 306 fix
    "Belgium Pro":             {"id": 40,    "name_match": "First Division A"},  # BEL, 313 fix
    "Super Lig":               {"id": 71,    "name_match": "Super Lig"},         # TUR, 306 fix
    "Russia Premier":          {"id": 63,    "name_match": "Premier League"},    # RUS, 270 fix
    "Swiss Super":             {"id": 69,    "name_match": "Super League"},      # SUI, 229 fix
    "Greek Super":             {"id": 135,   "name_match": "Super League 1"},    # GRE, 236 fix
    "Polish Ekstraklasa":      {"id": 196,   "name_match": "Ekstraklasa"},       # POL, 306 fix
    # Scandinavie
    "Swedish Allsvenskan":     {"id": 67,    "name_match": "Allsvenskan"},       # SWE, 240
    "Norwegian Eliteserien":   {"id": 59,    "name_match": "Eliteserien"},       # NOR, 240
    "Danish Superligaen":      {"id": 46,    "name_match": "Superligaen"},       # DEN, 193
    "Finnish Veikkausliiga":   {"id": 51,    "name_match": "Veikkausliiga"},     # FIN, 132
    # Petits championnats (volume, value)
    "Hungarian NB I":          {"id": 212,   "name_match": "NB I"},              # HUN, 198
    "Icelandic Besta":         {"id": 215,   "name_match": "Besta deildin"},     # ISL, 132
    "Estonian Meistriliiga":   {"id": 248,   "name_match": "Premium liiga"},     # EST, 180
    "Czech 1 Liga":            {"id": 122,   "name_match": "1. Liga"},           # CZE, 276
    "Croatian HNL":            {"id": 252,   "name_match": "HNL"},               # CRO, 180
    "Israeli Ligat":           {"id": 127,   "name_match": "Ligat ha'Al"},       # ISR, 240
    "Scottish Premiership":    {"id": 64,    "name_match": "Premiership"},       # SCO, 228
    "Bulgarian First":         {"id": 270,   "name_match": "First Professional League"}, # BUL, 293
    "Macedonian Prva":         {"id": 249,   "name_match": "Prva Liga"},         # MKD, 198
    "Bosnia Premier":          {"id": 267,   "name_match": "Premier League"},    # BIH, 180
    # Afrique du Nord
    "Morocco Botola Pro":      {"id": 530,   "name_match": "Botola Pro"},        # MAR
    "Algeria Ligue 1":         {"id": 516,   "name_match": "Ligue 1"},           # ALG
    # ── Competitions internationales (selections nationales + amicaux) ───────
    # IDs verifies live via /api/data/leagues?id=X (2026)
    "Friendlies":              {"id": 114,   "name_match": "Friendlies"},        # INT, amicaux 292
    "World Cup":               {"id": 77,    "name_match": "World Cup"},         # INT, 104
    "EURO":                    {"id": 50,    "name_match": "EURO"},              # INT, 51
    "Copa America":            {"id": 44,    "name_match": "Copa America"},      # INT, 32
    "Africa Cup of Nations":   {"id": 289,   "name_match": "Africa Cup of Nations"}, # INT, 52
    "Asian Cup":               {"id": 290,   "name_match": "Asian Cup"},         # INT, 51
    "WC Qualif UEFA":          {"id": 10195, "name_match": "World Cup Qualification UEFA"},     # 204
    "WC Qualif CONMEBOL":      {"id": 10199, "name_match": "World Cup Qualification CONMEBOL"}, # 90
    "WC Qualif CONCACAF":      {"id": 10198, "name_match": "World Cup Qualification CONCACAF"}, # 100
    "WC Qualif CAF":           {"id": 10196, "name_match": "World Cup Qualification CAF"},      # 263
    "WC Qualif AFC":           {"id": 10197, "name_match": "World Cup Qualification AFC"},      # 226
    "UEFA Nations League A":   {"id": 9806,  "name_match": "UEFA Nations League A"},            # 48
    "UEFA Nations League B":   {"id": 9807,  "name_match": "UEFA Nations League B"},            # 48
    "UEFA Nations League C":   {"id": 9808,  "name_match": "UEFA Nations League C"},            # 48
    "UEFA Nations League D":   {"id": 9809,  "name_match": "UEFA Nations League D"},            # 12
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
    # SA suite
    "Ecuador LigaPro":   240,    # api-football ID
    "Chile Primera":     265,
    "Peru Liga 1":       281,
    "Uruguay Primera":   268,    # api-football
    "Paraguay Profesional": 277,
    # Europe : secondaires + Scandinavie + petits championnats
    # (internal id = api-football id quand connu, sinon fotmob id en repli)
    "Eredivisie":            88,
    "Liga Portugal":         94,
    "Belgium Pro":           144,
    "Super Lig":             203,    # Turkey
    "Russia Premier":        235,
    "Swiss Super":           207,
    "Greek Super":           197,
    "Polish Ekstraklasa":    106,
    "Swedish Allsvenskan":   113,
    "Norwegian Eliteserien": 103,
    "Danish Superligaen":    119,
    "Finnish Veikkausliiga": 244,
    "Hungarian NB I":        271,
    "Icelandic Besta":       164,
    "Estonian Meistriliiga": 327,
    "Czech 1 Liga":          345,
    "Croatian HNL":          210,
    "Israeli Ligat":         383,
    "Scottish Premiership":  179,
    "Bulgarian First":       172,
    "Macedonian Prva":       388,    # incertain api-football
    "Bosnia Premier":        315,    # incertain api-football
    # Afrique du Nord (id = fotmob id, mapping api-football optionnel)
    "Morocco Botola Pro":    530,
    "Algeria Ligue 1":       516,
    # Competitions internationales (id = fotmob id, pas toujours dans api-football)
    "Friendlies":            114,
    "World Cup":             77,
    "EURO":                  50,
    "Copa America":          44,
    "Africa Cup of Nations": 289,
    "Asian Cup":             290,
    "WC Qualif UEFA":        10195,
    "WC Qualif CONMEBOL":    10199,
    "WC Qualif CONCACAF":    10198,
    "WC Qualif CAF":         10196,
    "WC Qualif AFC":         10197,
    "UEFA Nations League A": 9806,
    "UEFA Nations League B": 9807,
    "UEFA Nations League C": 9808,
    "UEFA Nations League D": 9809,
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
