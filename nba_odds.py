"""
nba_odds.py — Recupere les vraies lignes NBA player props via The Odds API.

Source : https://the-odds-api.com (free tier 500 req/mois)
Necessite ODDS_API_KEY dans config.py.

Produit data/nba_odds.json :
{
  "<game_id_nba>": {                    # match_id stats.nba.com
    "<player_name>": {
      "PTS":  {"line": 24.5, "over": 1.85, "under": 1.95, "book": "draftkings"},
      "REB":  {"line": 7.5,  ...},
      "AST":  {"line": 5.5,  ...},
      "FG3M": {"line": 2.5,  ...},
      "PRA":  {"line": 39.5, ...},
      "PR":   {"line": 32.5, ...},
      "PA":   {"line": 30.5, ...},
    },
    ...
  }
}

Match-id mapping : on map (home, away, date) entre les events Odds API et nba_matches.json.
"""
import json, os, hashlib, urllib.request, urllib.parse, urllib.error, gzip, time
from datetime import datetime
from pathlib import Path

try:
    from config import ODDS_API_KEY, ODDS_API_BASE
except ImportError:
    ODDS_API_KEY = ""
    ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Cache disque pour eviter de brule le quota (24h TTL)
CACHE_DIR = Path("data/cache_odds")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL = 24 * 3600   # 24h - les odds player props bougent peu une fois publiees

# Markets supportes par Odds API (NBA)
MARKETS = {
    "player_points":                  "PTS",
    "player_rebounds":                "REB",
    "player_assists":                 "AST",
    "player_threes":                  "FG3M",
    "player_points_rebounds_assists": "PRA",
    "player_points_rebounds":         "PR",
    "player_points_assists":          "PA",
}

# Bookmakers fetched (regions us + eu)
# us : draftkings, fanduel, betmgm, caesars, pointsbet (etats unis)
# eu : pinnacle, unibet, betfair, marathonbet, betclic, bwin (europe + france pour certains)
PREFERRED_BOOKS = [
    # US (lignes early, reference sharp)
    "draftkings", "fanduel", "betmgm", "caesars", "pointsbetus",
    # EU sharps + matches Betclic
    "pinnacle", "unibet_eu", "unibet_uk", "betfair_ex_eu", "marathonbet", "betclic", "bwin",
]
REGIONS = "us"   # quota-economique. Pour Pinnacle/Unibet/Betclic -> "us,eu" mais x2 quota

# Affichage human-friendly des noms de books pour l'UI
BOOK_DISPLAY = {
    "draftkings":    "DK",
    "fanduel":       "FD",
    "betmgm":        "MGM",
    "caesars":       "Caesars",
    "pointsbetus":   "PointsBet",
    "pinnacle":      "Pinnacle",
    "unibet_eu":     "Unibet",
    "unibet_uk":     "Unibet UK",
    "betfair_ex_eu": "Betfair",
    "marathonbet":   "Marathon",
    "betclic":       "Betclic",
    "bwin":          "Bwin",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}


def _cache_path(url):
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"odds_{h}.json"


def _cache_get(path, ttl=CACHE_TTL):
    if not path.exists(): return None
    if time.time() - path.stat().st_mtime > ttl: return None
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return None


def _cache_set(path, data):
    try: path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception: pass


def _get(url, use_cache=True):
    """Fetch + cache 6h (sauf list_events qui doit etre frais)."""
    if use_cache:
        path = _cache_path(url)
        cached = _cache_get(path)
        if cached is not None:
            return cached["data"], cached.get("headers", {})
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        raw = r.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        data = json.loads(raw)
        headers = dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:200]
        print(f"  [odds {e.code}] {body}")
        return None, {}
    except Exception as e:
        print(f"  [odds err] {e}")
        return None, {}
    if use_cache:
        _cache_set(path, {"data": data, "headers": headers})
    return data, headers


def list_events():
    """Liste des matchs NBA upcoming. Pas de cache (toujours frais)."""
    if not ODDS_API_KEY:
        return []
    url = f"{ODDS_API_BASE}/sports/basketball_nba/events?apiKey={ODDS_API_KEY}"
    data, hdr = _get(url, use_cache=False)
    remaining = hdr.get("x-requests-remaining") or hdr.get("X-Requests-Remaining")
    if remaining:
        print(f"  [odds-api] requetes restantes : {remaining}")
    return data or []


def event_props(event_id):
    """Recupere tous les markets player props pour un event (us + eu)."""
    if not ODDS_API_KEY:
        return None
    markets = ",".join(MARKETS.keys())
    books   = ",".join(PREFERRED_BOOKS)
    params = {
        "apiKey":     ODDS_API_KEY,
        "regions":    REGIONS,           # us,eu
        "markets":    markets,
        "bookmakers": books,
        "oddsFormat": "decimal",
    }
    url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds?" + urllib.parse.urlencode(params)
    data, _ = _get(url)
    return data


def event_game_lines(event_id):
    """LEGACY : The Odds API pour game lines. Garde au cas ou ESPN pickcenter
    serait vide. Mais par defaut on utilise _espn_game_lines() (gratuit)."""
    if not ODDS_API_KEY:
        return None
    books = ",".join(PREFERRED_BOOKS)
    params = {
        "apiKey":     ODDS_API_KEY,
        "regions":    REGIONS,
        "markets":    "h2h,spreads,totals",
        "bookmakers": books,
        "oddsFormat": "decimal",
    }
    url = f"{ODDS_API_BASE}/sports/basketball_nba/events/{event_id}/odds?" + urllib.parse.urlencode(params)
    data, _ = _get(url)
    return data


def espn_game_lines(espn_game_id):
    """
    Recupere spread / total / moneyline directement depuis ESPN pickcenter
    (gratuit, illimite, pas de quota). Format compatible avec _parse_game_lines.
    Retourne dict {game_total, home_spread, away_spread, home_total, away_total, book}.
    """
    import urllib.request as _ur
    espn_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={espn_game_id}"
    req = _ur.Request(espn_url, headers={"User-Agent": HEADERS["User-Agent"]})
    try:
        r = _ur.urlopen(req, timeout=15)
        raw = r.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        data = json.loads(raw)
    except Exception as e:
        print(f"  [espn lines err] {e}")
        return {}
    pc = data.get("pickcenter") or []
    if not pc: return {}
    # Prend le 1er provider (typiquement DraftKings)
    p = pc[0]
    spread = p.get("spread")
    total = p.get("overUnder")
    provider = (p.get("provider") or {}).get("name", "espn")
    if spread is None or total is None: return {}
    home_spread = spread  # spread est du POV de home (negatif si home favori)
    away_spread = -spread
    home_total = round((total - home_spread) / 2, 1)
    away_total = round((total - away_spread) / 2, 1)
    # Recupere noms equipes depuis ESPN
    header = data.get("header", {})
    teams_arr = (header.get("competitions") or [{}])[0].get("competitors", [])
    home_team = next((c.get("team", {}).get("displayName", "") for c in teams_arr if c.get("homeAway") == "home"), "")
    away_team = next((c.get("team", {}).get("displayName", "") for c in teams_arr if c.get("homeAway") == "away"), "")
    return {
        "home_team":   home_team,
        "away_team":   away_team,
        "book":        provider.lower(),
        "game_total":  total,
        "home_spread": home_spread,
        "away_spread": away_spread,
        "home_total":  home_total,
        "away_total":  away_total,
    }


def _parse_game_lines(event_data):
    """Extrait {total, home_spread, away_spread, home_total, away_total, book}."""
    if not event_data: return {}
    out = {"home_team": event_data.get("home_team"), "away_team": event_data.get("away_team")}
    for book in event_data.get("bookmakers", []):
        bkey = book.get("key")
        total, h_spread, a_spread = None, None, None
        for mkt in book.get("markets", []):
            k = mkt.get("key")
            for o in mkt.get("outcomes", []):
                name  = o.get("name")
                point = o.get("point")
                if k == "totals" and name == "Over":
                    total = point
                elif k == "spreads":
                    if name == event_data.get("home_team"):
                        h_spread = point
                    elif name == event_data.get("away_team"):
                        a_spread = point
        if total is not None and h_spread is not None and a_spread is not None:
            # Team total = (game_total - spread) / 2 .... actually formula:
            # home_total = (total - home_spread) / 2 ; away_total = (total - away_spread) / 2
            # (parce que spread negatif = favori, donc retire son spread de la somme)
            out["book"]        = bkey
            out["game_total"]  = total
            out["home_spread"] = h_spread
            out["away_spread"] = a_spread
            out["home_total"]  = round((total - h_spread) / 2, 1)
            out["away_total"]  = round((total - a_spread) / 2, 1)
            return out
    return out


def _norm_name(s):
    """Normalisation pour matcher les noms d'equipes (Pelicans -> pelicans, etc.)."""
    return (s or "").lower().replace("_", " ").strip()


def _match_game(event, nba_games):
    """Retourne game_id stats.nba.com pour un event Odds API, sinon None."""
    home = _norm_name(event.get("home_team", ""))
    away = _norm_name(event.get("away_team", ""))
    for g in nba_games:
        # noms stats.nba.com : home = "Knicks", home_city = "New York"
        full_home = _norm_name(f"{g.get('home_city','')} {g.get('home','')}")
        full_away = _norm_name(f"{g.get('away_city','')} {g.get('away','')}")
        if home in full_home or full_home in home:
            if away in full_away or full_away in away:
                return g.get("game_id")
        # Match inverse (au cas ou)
        if home == _norm_name(g.get("home", "")) and away == _norm_name(g.get("away", "")):
            return g.get("game_id")
    return None


def _parse_event_props(event_data):
    """
    Extrait {player_name: {prop_key: {line, over, under, books: [...], all_lines: [...]}}}.

    Pour chaque prop, on collecte TOUS les bookmakers qui le proposent (us + eu).
    - `books` : liste {name, line, over, under} pour chaque book qui quote la headline line
    - `all_lines` : tous les alt-lines (multi-book aussi)
    - `line`/`over`/`under`/`book` : la "meilleure" ligne (cote la + favorable a l'utilisateur)
    """
    players = {}
    if not event_data: return players

    # Structure intermediaire : {player: {prop: {line: {book: {over,under}}}}}
    grid = {}
    for book in event_data.get("bookmakers", []):
        bkey = book.get("key", "")
        for mkt in book.get("markets", []):
            prop = MARKETS.get(mkt.get("key", ""))
            if not prop: continue
            for o in mkt.get("outcomes", []):
                pname = o.get("description") or o.get("name", "")
                side  = (o.get("name") or "").lower()
                line  = o.get("point")
                price = o.get("price")
                if not pname or line is None: continue
                pg = grid.setdefault(pname, {}).setdefault(prop, {}).setdefault(line, {})
                bk_entry = pg.setdefault(bkey, {})
                if side == "over":  bk_entry["over"]  = price
                elif side == "under": bk_entry["under"] = price

    # Reconstruit la structure finale
    for pname, props in grid.items():
        for prop, lines_dict in props.items():
            # Pour chaque ligne, recolte tous les books qui la proposent
            all_lines = []
            for line_val, books_dict in lines_dict.items():
                # Headline = pour cette ligne, le meilleur book par direction
                # On store toutes les variantes book pour le display
                for bkey, prices in books_dict.items():
                    all_lines.append({
                        "line":  line_val,
                        "over":  prices.get("over"),
                        "under": prices.get("under"),
                        "book":  bkey,
                    })
            if not all_lines: continue
            # Selectionne la ligne "headline" = la plus centrale (cote ~1.91)
            def _centrality(L):
                o = L.get("over") or 999; u = L.get("under") or 999
                return min(abs((o or 1.91) - 1.91), abs((u or 1.91) - 1.91))
            all_lines.sort(key=_centrality)
            headline = all_lines[0]
            # Liste des books qui proposent CETTE ligne headline (pour affichage)
            books_for_headline = [L for L in all_lines if L["line"] == headline["line"]]
            books_for_headline.sort(key=lambda L: PREFERRED_BOOKS.index(L["book"]) if L["book"] in PREFERRED_BOOKS else 99)

            pdata = players.setdefault(pname, {})
            pdata[prop] = {
                "line":      headline["line"],
                "over":      headline["over"],
                "under":     headline["under"],
                "book":      headline["book"],
                "books":     books_for_headline,   # tous les books pour la ligne headline
                "all_lines": all_lines,             # toutes les alt-lines (multi-book)
            }
    return players


def run():
    if not ODDS_API_KEY:
        print("[!] Pas de cle ODDS_API_KEY (config.py) - lignes heuristiques utilisees.")
        # Ecrit un fichier vide pour clarifier
        Path("data").mkdir(exist_ok=True)
        with open("data/nba_odds.json", "w", encoding="utf-8") as f:
            json.dump({}, f)
        return {}

    # Charge les matchs NBA pour mapper
    try:
        nba_games = json.load(open("data/nba_matches.json", encoding="utf-8"))
    except Exception:
        print("[X] data/nba_matches.json absent - lance nba_scraper.py d'abord.")
        return {}

    # ─── 1. GAME LINES via ESPN pickcenter (gratuit, illimite) ──────────────
    # On itere directement sur nba_matches.json (ESPN game_ids) - independant
    # du quota Odds API. Garantit qu'on a toujours spread/total meme si quota=0.
    print("=== Game lines via ESPN pickcenter ===")
    game_lines = {}
    for g in nba_games:
        gid = g.get("game_id")
        if not gid: continue
        gl = espn_game_lines(gid)
        if gl.get("game_total"):
            game_lines[str(gid)] = gl
            print(f"  {g.get('away')} @ {g.get('home')}  ->  Total {gl['game_total']}  spread {gl['home_spread']}  [{gl.get('book')}]")

    # ─── 2. PLAYER PROPS via The Odds API (premium, limite par quota) ──────
    print("\n=== Odds NBA player props (The Odds API) ===")
    events = list_events()
    print(f"  {len(events)} events NBA upcoming sur l'API")
    out = {}
    if events:
        for ev in events:
            gid = _match_game(ev, nba_games)
            if not gid:
                continue
            print(f"  {ev.get('away_team')} @ {ev.get('home_team')}  ->  game_id {gid}")
            props_data = event_props(ev.get("id"))
            players    = _parse_event_props(props_data)
            if players:
                out[str(gid)] = players
                n_props = sum(len(v) for v in players.values())
                print(f"    {len(players)} joueurs, {n_props} props")
            time.sleep(0.4)
    else:
        print("  [!] Aucun event Odds API (quota epuise ou indispo) - mode degrade")

    with open("data/nba_odds.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open("data/nba_game_lines.json", "w", encoding="utf-8") as f:
        json.dump(game_lines, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] {len(out)} matchs props + {len(game_lines)} game lines -> data/nba_odds.json, data/nba_game_lines.json")
    return out


if __name__ == "__main__":
    run()
