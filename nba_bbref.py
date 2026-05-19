"""
nba_bbref.py — Scraper Basketball-Reference pour les stats que ESPN ne donne pas :
- Team pace, ORtg, DRtg     (-> pace_mult)
- Team opponent stats (DvP) (-> def_mult, def_argument)
- Player usage rate USG%    (-> usage_mult, futur)

BR sert ces tables dans des commentaires HTML (chargees en JS cote browser).
On les extrait via regex + parse les rows par data-stat attributes.

Limitations :
- BR rate-limite ~3-5 sec entre requetes (on respecte avec _throttle)
- Peut bloquer les IPs data center, fallback gracieux a {} si echec
- Saison = annee de fin du calendrier (ex: 2025-26 -> 2026)
"""
import hashlib, json, re, time, urllib.request, urllib.error
from pathlib import Path

CACHE_DIR = Path("data/cache_bbref")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

_last_call = 0
MIN_DELAY = 3.5  # BR rate-limit


def _throttle():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)
    _last_call = time.time()


def _cache_path(url):
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"bbref_{h}.html"


def _fetch_html(url, ttl=6 * 3600):
    """GET HTML avec cache disque."""
    path = _cache_path(url)
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try: return path.read_text(encoding="utf-8")
        except Exception: pass

    _throttle()
    req = urllib.request.Request(url, headers=HEADERS)
    last_err = None
    for attempt, timeout_s in enumerate([15, 25, 40]):
        try:
            r = urllib.request.urlopen(req, timeout=timeout_s)
            html = r.read().decode("utf-8", errors="ignore")
            path.write_text(html, encoding="utf-8")
            if attempt > 0:
                print(f"  [bbref OK retry {attempt+1}/3] {url[:60]}")
            return html
        except urllib.error.HTTPError as e:
            print(f"  [bbref HTTP {e.code}] {url[:60]}")
            return None
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            continue
        except Exception as e:
            print(f"  [bbref err] {url[:60]}: {type(e).__name__}: {e}")
            return None
    print(f"  [bbref GIVE UP] {url[:60]}  (derniere err: {last_err})")
    return None


def _extract_table_html(full_html, table_id):
    """
    Recupere le HTML de la table `table_id` meme si elle est dans un commentaire.
    Retourne le HTML inner de <table> ou "".
    """
    # 1. Tente direct
    m = re.search(rf'<table[^>]*id="{re.escape(table_id)}"[^>]*>(.*?)</table>',
                  full_html, re.DOTALL)
    if m: return m.group(1)
    # 2. Cherche dans les commentaires HTML
    for cm in re.finditer(r'<!--(.*?)-->', full_html, re.DOTALL):
        chunk = cm.group(1)
        if f'id="{table_id}"' not in chunk: continue
        m = re.search(rf'<table[^>]*id="{re.escape(table_id)}"[^>]*>(.*?)</table>',
                      chunk, re.DOTALL)
        if m: return m.group(1)
    return ""


def _parse_rows(table_html):
    """
    Parse les rows data du tbody. Format BR moderne :
      <tr ><th scope="row" data-stat="ranker">N</th><td data-stat="..."...>...</td>...</tr>
    Les en-tetes sont en <tr class="over_header"> ou <tr class="thead"> - on les skip.
    """
    rows = []
    # Limite au tbody pour eviter les en-tetes
    tbody_m = re.search(r'<tbody[^>]*>(.*)</tbody>', table_html, re.DOTALL)
    body = tbody_m.group(1) if tbody_m else table_html

    for row_match in re.finditer(r'<tr([^>]*)>(.*?)</tr>', body, re.DOTALL):
        tr_attrs = row_match.group(1)
        # Skip headers
        if "thead" in tr_attrs or "over_header" in tr_attrs:
            continue
        row_html = row_match.group(2)
        cells = {}
        for cell_match in re.finditer(
            r'<(?:t[hd])[^>]*data-stat="([^"]+)"[^>]*>(.*?)</(?:t[hd])>',
            row_html, re.DOTALL
        ):
            key = cell_match.group(1)
            # Strip HTML tags, decode basic entities
            val = re.sub(r'<[^>]+>', '', cell_match.group(2)).strip()
            val = val.replace("&amp;", "&").replace("&nbsp;", " ")
            cells[key] = val
        if cells:
            rows.append(cells)
    return rows


def _to_float(s):
    try: return float(s)
    except Exception: return 0.0


def _strip_team_name(name):
    """Vire les ETOILES de fin pour les playoffs teams (League leaders BR convention)."""
    return name.rstrip("*").strip()


# ─── API ─────────────────────────────────────────────────────────────────────

def team_advanced_map(season_year=2026, ttl=12 * 3600):
    """
    Retourne {team_name: {pace, off_rating, def_rating, net_rating, ppg, ...}}.
    Source : table advanced-team de leagues/NBA_YYYY.html
    """
    url = f"https://www.basketball-reference.com/leagues/NBA_{season_year}.html"
    html = _fetch_html(url, ttl=ttl)
    if not html: return {}
    table_html = _extract_table_html(html, "advanced-team")
    if not table_html: return {}
    out = {}
    for row in _parse_rows(table_html):
        team = _strip_team_name(row.get("team", "") or row.get("team_name", ""))
        if not team or team in ("League Average", ""): continue
        out[team] = {
            "team_name":  team,
            "pace":       _to_float(row.get("pace", 0)),
            "off_rating": _to_float(row.get("off_rtg", 0)),
            "def_rating": _to_float(row.get("def_rtg", 0)),
            "net_rating": _to_float(row.get("n_rtg", 0)),
            "ts_pct":     _to_float(row.get("ts_pct", 0)),
            "ppg":        0,  # rempli par per_game si besoin
        }
    return out


def team_opponent_map(season_year=2026, ttl=12 * 3600):
    """
    Retourne {team_name: {opp_pts, opp_reb, opp_ast, opp_fg3m, rank_opp_X}}.
    Source : table per_game-opponent de leagues/NBA_YYYY.html
    Rank 1 = encaisse le PLUS (= faille la + grosse pour over).
    """
    url = f"https://www.basketball-reference.com/leagues/NBA_{season_year}.html"
    html = _fetch_html(url, ttl=ttl)
    if not html: return {}
    table_html = _extract_table_html(html, "per_game-opponent")
    if not table_html: return {}

    rows = []
    for row in _parse_rows(table_html):
        team = _strip_team_name(row.get("team", "") or row.get("team_name", ""))
        if not team or team == "League Average": continue
        rows.append({
            "team":     team,
            "opp_pts":  _to_float(row.get("opp_pts", 0)),
            "opp_reb":  _to_float(row.get("opp_trb", 0)),
            "opp_ast":  _to_float(row.get("opp_ast", 0)),
            "opp_fg3m": _to_float(row.get("opp_fg3", 0)),
        })

    # Calcule les rangs (1 = encaisse le +)
    def _rank(field):
        sorted_rows = sorted(rows, key=lambda r: r[field], reverse=True)
        return {r["team"]: idx + 1 for idx, r in enumerate(sorted_rows)}
    ranks = {
        "opp_pts":  _rank("opp_pts"),
        "opp_reb":  _rank("opp_reb"),
        "opp_ast":  _rank("opp_ast"),
        "opp_fg3m": _rank("opp_fg3m"),
    }

    out = {}
    for r in rows:
        t = r["team"]
        out[t] = {
            "team_name":     t,
            "opp_pts":       r["opp_pts"],
            "opp_reb":       r["opp_reb"],
            "opp_ast":       r["opp_ast"],
            "opp_fg3m":      r["opp_fg3m"],
            "rank_opp_pts":  ranks["opp_pts"][t],
            "rank_opp_reb":  ranks["opp_reb"][t],
            "rank_opp_ast":  ranks["opp_ast"][t],
            "rank_opp_fg3m": ranks["opp_fg3m"][t],
        }
    return out


def player_usage_map(season_year=2026, ttl=12 * 3600):
    """
    Retourne {player_name: {usg_pct, gp, mp_per_g, team}}.
    Source : table advanced_stats de leagues/NBA_YYYY_advanced.html
    """
    url = f"https://www.basketball-reference.com/leagues/NBA_{season_year}_advanced.html"
    html = _fetch_html(url, ttl=ttl)
    if not html: return {}
    table_html = _extract_table_html(html, "advanced_stats") or \
                 _extract_table_html(html, "advanced")
    if not table_html: return {}
    out = {}
    for row in _parse_rows(table_html):
        name = row.get("name_display", "") or row.get("player", "")
        if not name or name == "Player": continue
        team = row.get("team_name_abbr", "") or row.get("team_id", "")
        # Multi-team -> garde TOT (totaux) si dispo
        if name in out and team != "TOT": continue
        out[name] = {
            "usg_pct":  _to_float(row.get("usg_pct", 0)),
            "gp":       int(_to_float(row.get("games", 0))),
            "mp_per_g": _to_float(row.get("mp_per_g", 0) or row.get("mp", 0)),
            "team":     team,
            "per":      _to_float(row.get("per", 0)),
            "ts_pct":   _to_float(row.get("ts_pct", 0)),
        }
    return out


# ─── Mapping team ESPN -> BR ─────────────────────────────────────────────────
# ESPN renvoie "Cavaliers" / "Knicks", BR renvoie "Cleveland Cavaliers" / "New York Knicks"
# On normalise pour matcher dans nba_scraper.

BBREF_TO_ESPN_TEAM = {
    "Atlanta Hawks":         "Hawks",
    "Boston Celtics":        "Celtics",
    "Brooklyn Nets":         "Nets",
    "Charlotte Hornets":     "Hornets",
    "Chicago Bulls":         "Bulls",
    "Cleveland Cavaliers":   "Cavaliers",
    "Dallas Mavericks":      "Mavericks",
    "Denver Nuggets":        "Nuggets",
    "Detroit Pistons":       "Pistons",
    "Golden State Warriors": "Warriors",
    "Houston Rockets":       "Rockets",
    "Indiana Pacers":        "Pacers",
    "LA Clippers":           "Clippers",
    "Los Angeles Clippers":  "Clippers",
    "Los Angeles Lakers":    "Lakers",
    "Memphis Grizzlies":     "Grizzlies",
    "Miami Heat":            "Heat",
    "Milwaukee Bucks":       "Bucks",
    "Minnesota Timberwolves":"Timberwolves",
    "New Orleans Pelicans":  "Pelicans",
    "New York Knicks":       "Knicks",
    "Oklahoma City Thunder": "Thunder",
    "Orlando Magic":         "Magic",
    "Philadelphia 76ers":    "76ers",
    "Phoenix Suns":          "Suns",
    "Portland Trail Blazers":"Trail Blazers",
    "Sacramento Kings":      "Kings",
    "San Antonio Spurs":     "Spurs",
    "Toronto Raptors":       "Raptors",
    "Utah Jazz":             "Jazz",
    "Washington Wizards":    "Wizards",
}


def bbref_to_espn(team_name):
    """Convertit nom equipe BR -> nom equipe ESPN (court)."""
    return BBREF_TO_ESPN_TEAM.get(team_name, team_name)
