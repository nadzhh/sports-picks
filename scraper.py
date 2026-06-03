"""
scraper.py — Recupere matchs J/J+1 via FotMob (stats saison 2025/26) + api-football (odds)

FotMob fournit:
- Fixtures complets par ligue
- Standings (classement actuel)
- Forme W/D/L (calculee depuis les dernieres rencontres)
- Stats equipe saison en cours (goals_pm, shots_pm, xG, etc.)

api-football fournit:
- Odds (bulk par date, 2 reqs/jour)

Produit data/matches.json compatible avec picks_engine.py.
"""
import json, os, re, shutil, sys
from datetime import date, timedelta, datetime
from fotmob_client import league as fm_league, stat as fm_stat, session_calls as fm_calls, reset_session as fm_reset
from api_client import get as af_get, quota_today, session_calls as af_calls, reset_session as af_reset
from config import FOTMOB_LEAGUES, INTERNAL_LEAGUE_IDS, APIFOOTBALL_LEAGUES, CUP_LEAGUES

os.makedirs("data", exist_ok=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

_TEAM_STRIP = [
    "stade", "club", "fc", "cf", "ac", "sc", "as", "deportivo",
    "real", "athletic", "atletico", "olympique", "rc",
]
_TEAM_ALIASES = {
    "psg":              "paris saint germain",
    "paris sg":         "paris saint germain",
    "saint germain":    "paris saint germain",
    "man city":         "manchester city",
    "man united":       "manchester united",
    "man utd":          "manchester united",
    "spurs":            "tottenham",
    "wolves":           "wolverhampton",
    "leverkusen":       "bayer leverkusen",
    "munchen":          "munich",
    "monchengladbach":  "borussia monchengladbach",
    "inter":            "inter milan",
    "milan":            "ac milan",
    "brestois":         "brest",
    "stade brestois":   "brest",
    "saint etienne":    "saint etienne",
    "asse":             "saint etienne",
}


def _norm(name):
    """Normalise un nom d'equipe pour matching cross-source."""
    n = (name or "").lower().strip()
    # Remove accents (basic)
    repl = str.maketrans("àáâãäçèéêëìíîïñòóôõöùúûüýÿ", "aaaaaceeeeiiiinooooouuuuyy")
    n = n.translate(repl)
    # Strip non-alpha
    n = re.sub(r"[^a-z0-9 \-]", "", n)
    # Replace hyphens with spaces
    n = n.replace("-", " ")
    # Strip numbers (Stade Brestois 29 -> Stade Brestois)
    n = re.sub(r"\b\d+\b", "", n)
    # Strip common team words
    tokens = [t for t in n.split() if t and t not in _TEAM_STRIP]
    n = " ".join(tokens).strip()
    # Aliases
    if n in _TEAM_ALIASES:
        n = _TEAM_ALIASES[n]
    return n


def _parse_score(score_str):
    """'2 - 1' -> (2, 1) ou None."""
    if not score_str:
        return None
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)", score_str)
    if not m: return None
    return int(m.group(1)), int(m.group(2))


def _extract_season_id(fm_league_data):
    """Extrait le numeric season_id depuis fetchAllUrl d'un stat URL."""
    pl = (fm_league_data or {}).get("stats", {}).get("players", [])
    if pl and pl[0].get("fetchAllUrl"):
        m = re.search(r"/season/(\d+)/", pl[0]["fetchAllUrl"])
        if m: return m.group(1)
    return None


# ─── Step 1: FIXTURES + STANDINGS depuis FotMob ──────────────────────────────

def collect_fotmob_data(dates):
    """
    Pour chaque ligue:
      - charge fixtures complets + standings
      - filtre les fixtures J/J+1 non joues
      - prepare standings index team_id->rank
    Retourne (matches[], standings_by_league{}, league_data{}).
    """
    matches = []
    standings_by_league = {}
    league_data_cache = {}

    # ── Pre-filtre : ne charge QUE les ligues avec matchs J/J+1 ──────────────
    # Avant : on fetchait les 61 ligues meme si 90% sont en pause estivale
    # (dont les massives Friendlies/WC Qualif CAF avec 200-300 fixtures chacune).
    # Maintenant : un seul call /matches?date=X par jour (2 calls total) donne
    # la liste des ligues avec activite -> on filtre FOTMOB_LEAGUES sur ces IDs.
    # Gain : ~50 fetches FotMob evites quand peu de ligues actives.
    import urllib.request, gzip
    active_league_ids = set()
    for d in dates:
        url = f"https://www.fotmob.com/api/data/matches?date={d.replace('-','')}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                if raw[:2] == b"\x1f\x8b": raw = gzip.decompress(raw)
                payload = json.loads(raw)
            for l in payload.get("leagues", []) or []:
                lid = l.get("primaryId") or l.get("id")
                if lid and l.get("matches"): active_league_ids.add(lid)
        except Exception as e:
            print(f"  [!] FotMob matches-by-date {d}: {e}")
    # Si la pre-filtration a echoue, on retombe sur toutes les ligues (safe).
    if not active_league_ids:
        print("  [!] pre-filtration vide -> fallback toutes ligues")
        leagues_to_fetch = list(FOTMOB_LEAGUES.items())
    else:
        leagues_to_fetch = [(n, info) for n, info in FOTMOB_LEAGUES.items()
                            if info["id"] in active_league_ids]
        skipped = len(FOTMOB_LEAGUES) - len(leagues_to_fetch)
        print(f"  [i] Pre-filtration : {len(leagues_to_fetch)} ligues actives J/J+1 "
              f"(skip {skipped} en pause)")

    # ── Parallelisation FotMob fetch ─────────────────────────────────────────
    # 5 workers (au lieu de 10) pour eviter d'hammer FotMob et causer des timeouts.
    from concurrent.futures import ThreadPoolExecutor
    def _fetch_one(lname_info):
        lname, info = lname_info
        try:
            return (lname, info["id"], fm_league(info["id"]))
        except Exception as e:
            return (lname, info["id"], None)
    with ThreadPoolExecutor(max_workers=5) as ex:
        fetch_results = list(ex.map(_fetch_one, leagues_to_fetch))

    for lname, fm_id, d in fetch_results:
        if not d:
            print(f"  [!] FotMob: pas de data pour {lname}")
            continue
        info = FOTMOB_LEAGUES[lname]
        league_data_cache[fm_id] = d

        # Standings
        table_root = d.get("table", []) or []
        if table_root:
            try:
                table = table_root[0].get("data", {}).get("table", {}).get("all", []) or []
                # Peut etre groupe par stage (CL groupe) - on prend "all"
                if not table and table_root[0].get("data", {}).get("tables"):
                    tables = table_root[0]["data"]["tables"]
                    table = []
                    for t in tables:
                        table.extend(t.get("table", {}).get("all", []) or [])
                standings_by_league[fm_id] = {
                    t["id"]: {"rank": t.get("idx"), "pts": t.get("pts"),
                              "played": t.get("played"), "scoresStr": t.get("scoresStr")}
                    for t in table if t.get("id")
                }
            except Exception as e:
                print(f"  [!] standings {lname}: {e}")

        # Filtre des "mini-friendlies" : si la ligue est Friendlies (id 114),
        # on skip les matchs entre micro-selections (FIFA rank tres bas) :
        # Gibraltar, Iles Vierges, San Marino, Liechtenstein, Bhutan, Maldives,
        # Cambodia, Guam, etc. Garde les matchs ou au moins UNE equipe est
        # une selection notable.
        MINOR_NATIONS = {
            "Gibraltar", "British Virgin Islands", "Guam", "Maldives", "Pakistan",
            "Cambodia", "Bhutan", "Andorra", "Liechtenstein", "San Marino",
            "Faroe Islands", "Lesotho", "Burundi", "Equatorial Guinea",
            "Belize", "Aruba", "Curacao", "Suriname", "Anguilla",
            "Cayman Islands", "Turks and Caicos Islands", "Sint Maarten",
            "Lesotho", "Dominica", "Saint Lucia", "Saint Vincent and the Grenadines",
            "Grenada", "Saint Kitts and Nevis", "British Antarctic Territory",
            "American Samoa", "Cook Islands", "Tonga", "Vanuatu", "Samoa",
            "Tahiti", "Solomon Islands", "Brunei", "Timor-Leste", "Macau",
            "Northern Mariana Islands", "Mongolia",
        }
        is_friendly = (fm_id == 114)

        # Fixtures
        all_matches = d.get("fixtures", {}).get("allMatches", []) or []
        n_filtered = 0
        for m in all_matches:
            utc = (m.get("status", {}).get("utcTime") or "")[:10]
            if utc not in dates:
                continue
            if m.get("status", {}).get("finished"):
                continue
            # Filtre mini-amicaux
            if is_friendly:
                hn = (m.get("home") or {}).get("name", "")
                an = (m.get("away") or {}).get("name", "")
                if hn in MINOR_NATIONS and an in MINOR_NATIONS:
                    n_filtered += 1
                    continue  # skip Gibraltar vs Iles Vierges
            matches.append((lname, fm_id, m))
        if is_friendly and n_filtered:
            print(f"  [i] Filtre {n_filtered} mini-amicaux (Gibraltar/BVI/etc.)")

    return matches, standings_by_league, league_data_cache


# ─── Step 2: FORME W/D/L (depuis les fixtures finis) ──────────────────────────

def _build_team_match_index(league_data_cache):
    """Construit {team_id_str: [matches FT sorted desc by date]} TOUTES competitions
    confondues (Top 5 + UEFA + Coupes nationales). Permet d'avoir un L5 forme
    representatif quand un match de coupe est joue entre 2 matchs de championnat
    (ex: Nice a fait W en Coupe de France mais notre L5 ne le voyait pas)."""
    by_team = {}
    for fm_id, ldata in (league_data_cache or {}).items():
        if not ldata: continue
        all_matches = ldata.get("fixtures", {}).get("allMatches", []) or []
        for m in all_matches:
            if not m.get("status", {}).get("finished"): continue
            h_id = str(m.get("home", {}).get("id", ""))
            a_id = str(m.get("away", {}).get("id", ""))
            for tid in (h_id, a_id):
                if not tid: continue
                # Evite les doublons (meme match dans 2 leagues, peu probable mais possible)
                key = m.get("id") or (m.get("status", {}).get("utcTime","") + tid)
                bucket = by_team.setdefault(tid, [])
                if not any((x.get("id") == m.get("id")) for x in bucket if m.get("id")):
                    bucket.append(m)
    # Tri par date desc une seule fois
    for tid in by_team:
        by_team[tid].sort(key=lambda m: m.get("status", {}).get("utcTime", ""), reverse=True)
    return by_team


def _compute_form(league_data, team_id_str, n=5, team_match_index=None, with_opps=False):
    """Retourne liste W/D/L des n derniers matchs FT de l'equipe.

    Si team_match_index est fourni (= cross-competitions), on prend les matchs
    de TOUTES competitions confondues (championnat + coupes + UEFA). Sinon
    fallback sur league_data uniquement (legacy).

    Si with_opps=True, retourne aussi liste parallele [opp_id_str, ...] pour
    permettre la ponderation Strength-of-Schedule cote picks_engine.
    """
    if team_match_index is not None and team_id_str in team_match_index:
        matches = team_match_index[team_id_str]
    else:
        matches = league_data.get("fixtures", {}).get("allMatches", []) or []

    played = []
    for m in matches:
        if not m.get("status", {}).get("finished"):
            continue
        h_id = str(m.get("home", {}).get("id"))
        a_id = str(m.get("away", {}).get("id"))
        if team_id_str not in (h_id, a_id):
            continue
        score = _parse_score(m.get("status", {}).get("scoreStr"))
        if not score:
            continue
        gh, ga = score
        is_home = team_id_str == h_id
        gf = gh if is_home else ga
        gn = ga if is_home else gh
        res = "W" if gf > gn else ("D" if gf == gn else "L")
        opp_id = a_id if is_home else h_id
        played.append((m.get("status", {}).get("utcTime", ""), res, opp_id))
    played.sort(key=lambda x: x[0], reverse=True)
    if with_opps:
        return [p[1] for p in played[:n]], [p[2] for p in played[:n]]
    return [p[1] for p in played[:n]]


def _compute_h2h(league_data, home_id_str, away_id_str):
    """Retourne (hw, dr, aw) du point de vue de home_id."""
    all_matches = league_data.get("fixtures", {}).get("allMatches", []) or []
    hw = dr = aw = 0
    for m in all_matches:
        if not m.get("status", {}).get("finished"):
            continue
        h_id = str(m.get("home", {}).get("id"))
        a_id = str(m.get("away", {}).get("id"))
        if {h_id, a_id} != {home_id_str, away_id_str}:
            continue
        score = _parse_score(m.get("status", {}).get("scoreStr"))
        if not score:
            continue
        gh, ga = score
        if h_id == home_id_str:
            if gh > ga: hw += 1
            elif gh < ga: aw += 1
            else: dr += 1
        else:
            if gh > ga: aw += 1
            elif gh < ga: hw += 1
            else: dr += 1
    return hw, dr, aw


# ─── Step 3: ODDS depuis api-football (bulk par date) ────────────────────────

def fetch_odds_index(dates):
    """
    odds bulk par date - index par (home_norm, away_norm, date_iso).
    Le payload odds n'a pas les teams, on les retrouve via /fixtures?date=X.
    """
    idx = {}
    for d in dates:
        # 1) Mapping fixture_id -> (home, away)
        fixtures = af_get("fixtures", {"date": d}, "fixtures_date")
        fid_to_teams = {}
        if isinstance(fixtures, list):
            for f in fixtures:
                fid = f.get("fixture", {}).get("id")
                if not fid: continue
                fid_to_teams[fid] = (
                    f.get("teams", {}).get("home", {}).get("name"),
                    f.get("teams", {}).get("away", {}).get("name"),
                    (f.get("fixture", {}).get("date") or "")[:10],
                )

        # 2) Odds
        r = af_get("odds", {"date": d}, "odds")
        if not isinstance(r, list):
            continue
        for o in r:
            fid = o.get("fixture", {}).get("id")
            teams = fid_to_teams.get(fid)
            if not teams: continue
            h, a, date_iso = teams
            idx[(_norm(h), _norm(a), date_iso)] = o
    return idx


def convert_odds(odds_payload, home_name, away_name):
    """api-football odds -> format Sofascore markets."""
    if not odds_payload:
        return None
    bms = odds_payload.get("bookmakers", []) or []
    if not bms:
        return None

    BM_ORDER = ["Bet365", "10Bet", "1xBet", "Betano", "William Hill", "Pinnacle"]
    bms_sorted = sorted(bms, key=lambda b: BM_ORDER.index(b.get("name")) if b.get("name") in BM_ORDER else 99)

    markets = []

    def add(name, cg, choices):
        markets.append({"marketName": name, "choiceGroup": cg, "choices": choices})

    def find_bet(*names):
        for bm in bms_sorted:
            for b in bm.get("bets", []):
                if b.get("name") in names:
                    return b
        return None

    def to_frac(odd_str):
        try: return f"{float(odd_str) - 1:.4f}"
        except: return str(odd_str)

    # 1X2
    b = find_bet("Match Winner")
    if b:
        choices = []
        for v in b.get("values", []):
            lab = {"Home": "1", "Draw": "X", "Away": "2"}.get(v.get("value"), v.get("value"))
            choices.append({"name": lab, "fractionalValue": to_frac(v.get("odd"))})
        if choices: add("Full time", None, choices)

    # Double Chance
    b = find_bet("Double Chance")
    if b:
        choices = []
        for v in b.get("values", []):
            lab = {"Home/Draw": "1X", "Home/Away": "12", "Draw/Away": "X2"}.get(v.get("value"), v.get("value"))
            choices.append({"name": lab, "fractionalValue": to_frac(v.get("odd"))})
        if choices: add("Double chance", None, choices)

    # BTTS
    b = find_bet("Both Teams Score", "Both Teams To Score")
    if b:
        choices = [{"name": v.get("value"), "fractionalValue": to_frac(v.get("odd"))} for v in b.get("values", [])]
        if choices: add("Both teams to score", None, choices)

    # Goals Over/Under (multiple thresholds)
    b = find_bet("Goals Over/Under")
    if b:
        groups = {}
        for v in b.get("values", []):
            parts = v.get("value", "").split()
            if len(parts) == 2:
                side, thresh = parts
                groups.setdefault(thresh, []).append({"name": side, "fractionalValue": to_frac(v.get("odd"))})
        for thresh, ch in groups.items():
            if len(ch) >= 2:
                add("Match goals", thresh, ch)

    # First Team to Score
    b = find_bet("First to Score", "Team To Score First", "First Team To Score")
    if b:
        choices = []
        for v in b.get("values", []):
            lab = {"Home": home_name, "Away": away_name, "1": home_name, "2": away_name}.get(v.get("value"), v.get("value"))
            choices.append({"name": lab, "fractionalValue": to_frac(v.get("odd"))})
        if choices: add("First team to score", None, choices)

    # Corners
    b = find_bet("Corners Over Under", "Total Corners", "Corner Over Under")
    if b:
        groups = {}
        for v in b.get("values", []):
            parts = v.get("value", "").split()
            if len(parts) == 2:
                side, thresh = parts
                groups.setdefault(thresh, []).append({"name": side, "fractionalValue": to_frac(v.get("odd"))})
        if groups:
            best = min(groups.keys(), key=lambda x: abs(float(x) - 9.5) if x.replace(".", "").isdigit() else 999)
            if len(groups[best]) >= 2:
                add("Corners 2-Way", best, groups[best])

    # Cards
    b = find_bet("Cards Over/Under", "Total Cards")
    if b:
        groups = {}
        for v in b.get("values", []):
            parts = v.get("value", "").split()
            if len(parts) == 2:
                side, thresh = parts
                groups.setdefault(thresh, []).append({"name": side, "fractionalValue": to_frac(v.get("odd"))})
        if groups:
            best = min(groups.keys(), key=lambda x: abs(float(x) - 3.5) if x.replace(".", "").isdigit() else 999)
            if len(groups[best]) >= 2:
                add("Cards in match", best, groups[best])

    # Draw No Bet
    b = find_bet("Draw No Bet", "DNB")
    if b:
        choices = []
        for v in b.get("values", []):
            lab = {"Home": "1", "Away": "2"}.get(v.get("value"), v.get("value"))
            choices.append({"name": lab, "fractionalValue": to_frac(v.get("odd"))})
        if choices: add("Draw no bet", None, choices)

    # ── Total tirs (toutes lignes bookmaker disponibles) ──────────────────
    b = find_bet("Total Shots")
    if b:
        groups = {}
        for v in b.get("values", []):
            val = v.get("value", "")
            # Format "Over 24.5" / "Under 24.5"
            parts = val.split()
            if len(parts) == 2:
                side, thresh = parts
                groups.setdefault(thresh, []).append({
                    "name": side,
                    "fractionalValue": to_frac(v.get("odd")),
                })
        for thresh, choices in groups.items():
            if len(choices) >= 2:
                add("Total shots", thresh, choices)

    # ── Total tirs cadrés (toutes lignes) ──────────────────
    b = find_bet("Total ShotOnGoal", "Total Shots On Target")
    if b:
        groups = {}
        for v in b.get("values", []):
            val = v.get("value", "")
            parts = val.split()
            if len(parts) == 2:
                side, thresh = parts
                groups.setdefault(thresh, []).append({
                    "name": side,
                    "fractionalValue": to_frac(v.get("odd")),
                })
        for thresh, choices in groups.items():
            if len(choices) >= 2:
                add("Total shots on target", thresh, choices)

    # ── Tirs 1x2 (qui a le plus de tirs) ──────────────────
    b = find_bet("Shots.1x2", "Total Shots 1x2")
    if b:
        choices = []
        for v in b.get("values", []):
            lab = {"Home": "1", "Draw": "X", "Away": "2"}.get(v.get("value"), v.get("value"))
            choices.append({"name": lab, "fractionalValue": to_frac(v.get("odd"))})
        if choices:
            add("Shots 1X2", None, choices)

    # ── Tirs cadrés 1x2 ──────────────────
    b = find_bet("ShotOnTarget 1x2", "Total Shots On Target 1x2")
    if b:
        choices = []
        for v in b.get("values", []):
            lab = {"Home": "1", "Draw": "X", "Away": "2"}.get(v.get("value"), v.get("value"))
            choices.append({"name": lab, "fractionalValue": to_frac(v.get("odd"))})
        if choices:
            add("Shots on target 1X2", None, choices)

    # ── Tirs equipe a l'exterieur (Bet365/Betano: "Away Player Shots Total" en realite TEAM total)
    # Filtrer par line >= 7 pour eviter de melanger avec player props
    b = find_bet("Away Player Shots Total")
    if b:
        groups = {}
        for v in b.get("values", []):
            val = v.get("value", "")
            parts = val.split()
            if len(parts) == 2:
                side, thresh = parts
                try:
                    if float(thresh) < 7:  # skip player props, only team-level
                        continue
                except: continue
                groups.setdefault(thresh, []).append({
                    "name": side,
                    "fractionalValue": to_frac(v.get("odd")),
                })
        for thresh, choices in groups.items():
            if len(choices) >= 2:
                add("Away team total shots", thresh, choices)

    # ── Tirs equipe domicile (au cas ou disponible, certains bookmakers)
    b = find_bet("Home Player Shots Total", "Home Team Shots Total")
    if b:
        groups = {}
        for v in b.get("values", []):
            val = v.get("value", "")
            parts = val.split()
            if len(parts) == 2:
                side, thresh = parts
                try:
                    if float(thresh) < 7: continue
                except: continue
                groups.setdefault(thresh, []).append({
                    "name": side,
                    "fractionalValue": to_frac(v.get("odd")),
                })
        for thresh, choices in groups.items():
            if len(choices) >= 2:
                add("Home team total shots", thresh, choices)

    return {"markets": markets} if markets else None


# ─── ESPN Soccer Odds Fallback (free, all leagues) ────────────────────────────

# Mapping notre nom de ligue -> ESPN soccer league id
ESPN_SOCCER_LEAGUES = {
    "Premier League":      "eng.1",
    "La Liga":             "esp.1",
    "Bundesliga":          "ger.1",
    "Serie A":             "ita.1",
    "Ligue 1":             "fra.1",
    "Champions League":    "uefa.champions",
    "Europa League":       "uefa.europa",
    "Conference League":   "uefa.europa.conf",
}


def _espn_soccer_scoreboard(league_key, ttl=4 * 3600):
    """Cache 4h scoreboard ESPN par ligue."""
    import urllib.request as _ur, urllib.error as _ue, gzip as _gz, hashlib as _h
    from pathlib import Path as _P
    cache_dir = _P("data/cache_espn_soccer")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{league_key.replace('.','_')}.json"
    import time as _t
    if cache.exists() and (_t.time() - cache.stat().st_mtime) < ttl:
        try: return json.loads(cache.read_text(encoding="utf-8"))
        except Exception: pass
    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league_key}/scoreboard"
    try:
        r = _ur.urlopen(_ur.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15)
        raw = r.read()
        if raw[:2] == b"\x1f\x8b": raw = _gz.decompress(raw)
        data = json.loads(raw)
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    except Exception as e:
        print(f"  [espn-soccer err {league_key}] {e}")
        return None


def _ml_to_decimal(ml):
    """Convertit American moneyline -> decimal cote. +150 -> 2.50, -200 -> 1.50."""
    if ml is None: return None
    try: ml = int(ml)
    except Exception: return None
    if ml > 0: return round(1 + ml / 100, 2)
    if ml < 0: return round(1 + 100 / abs(ml), 2)
    return None


def _build_espn_soccer_odds(home_name, away_name, league_name):
    """
    Fallback ESPN pour les matchs hors couverture api-football.
    Retourne markets au format compatible (Full time 1X2 + Match goals).
    """
    league_key = ESPN_SOCCER_LEAGUES.get(league_name)
    if not league_key: return None
    data = _espn_soccer_scoreboard(league_key)
    if not data: return None
    events = data.get("events", []) or []
    target = None
    for ev in events:
        comp = (ev.get("competitions") or [{}])[0]
        teams = comp.get("competitors", [])
        h_name = next((c.get("team", {}).get("displayName", "") for c in teams if c.get("homeAway") == "home"), "")
        a_name = next((c.get("team", {}).get("displayName", "") for c in teams if c.get("homeAway") == "away"), "")
        # Match fuzzy
        if (_norm(home_name) in _norm(h_name) or _norm(h_name) in _norm(home_name)) and \
           (_norm(away_name) in _norm(a_name) or _norm(a_name) in _norm(away_name)):
            target = comp; break
    if not target: return None

    odds_list = target.get("odds", []) or []
    if not odds_list: return None
    odds = odds_list[0]   # premier provider (typiquement DraftKings)

    markets = []
    def to_frac(decimal_cote):
        try: return f"{float(decimal_cote) - 1:.4f}"
        except Exception: return None

    def _ml_path(odds_dict, side):
        """Recupere moneyline['side']['close']['odds'] avec fallback 'open'."""
        d = (odds_dict.get("moneyline") or {}).get(side) or {}
        v = (d.get("close") or {}).get("odds") or (d.get("open") or {}).get("odds")
        return v

    # 1X2 via moneyline.{home,away,draw}.close.odds (format ESPN soccer)
    home_ml = _ml_path(odds, "home")
    away_ml = _ml_path(odds, "away")
    draw_ml = _ml_path(odds, "draw")
    # Fallback : ancien format homeTeamOdds.moneyLine
    if home_ml is None: home_ml = (odds.get("homeTeamOdds") or {}).get("moneyLine")
    if away_ml is None: away_ml = (odds.get("awayTeamOdds") or {}).get("moneyLine")
    if draw_ml is None: draw_ml = (odds.get("drawOdds") or {}).get("moneyLine")
    c1 = _ml_to_decimal(home_ml)
    c2 = _ml_to_decimal(away_ml)
    cx = _ml_to_decimal(draw_ml)
    if c1 and c2 and cx:
        markets.append({
            "marketName": "Full time", "choiceGroup": None,
            "choices": [
                {"name": "1", "fractionalValue": to_frac(c1)},
                {"name": "X", "fractionalValue": to_frac(cx)},
                {"name": "2", "fractionalValue": to_frac(c2)},
            ]
        })
        # Double chance derivee : prob 1X = 1/c1 + 1/cx (no-vig), cote = 1/prob
        # Simplifie : 1X = (c1*cx)/(c1+cx), X2 = (c2*cx)/(c2+cx), 12 = (c1*c2)/(c1+c2)
        c_1x = round((c1 * cx) / (c1 + cx), 2)
        c_12 = round((c1 * c2) / (c1 + c2), 2)
        c_x2 = round((cx * c2) / (cx + c2), 2)
        markets.append({
            "marketName": "Double chance", "choiceGroup": None,
            "choices": [
                {"name": "1X", "fractionalValue": to_frac(c_1x)},
                {"name": "12", "fractionalValue": to_frac(c_12)},
                {"name": "X2", "fractionalValue": to_frac(c_x2)},
            ]
        })

    # Over/Under buts via total.{over,under}.close.odds
    ou_line = odds.get("overUnder")
    total_dict = odds.get("total") or {}
    over_ml = ((total_dict.get("over") or {}).get("close") or {}).get("odds") \
              or ((total_dict.get("over") or {}).get("open") or {}).get("odds")
    under_ml = ((total_dict.get("under") or {}).get("close") or {}).get("odds") \
              or ((total_dict.get("under") or {}).get("open") or {}).get("odds")
    # Si pas dispo, on derive a partir de overUnder (ligne) sans cote -> on skip
    if ou_line and over_ml and under_ml:
        c_o = _ml_to_decimal(over_ml)
        c_u = _ml_to_decimal(under_ml)
        if c_o and c_u:
            markets.append({
                "marketName": "Match goals", "choiceGroup": str(ou_line),
                "choices": [
                    {"name": "Over",  "fractionalValue": to_frac(c_o)},
                    {"name": "Under", "fractionalValue": to_frac(c_u)},
                ]
            })

    return {"markets": markets, "_source": "espn"} if markets else None


# ─── Step 4: BUILD ─────────────────────────────────────────────────────────────

def build_match(fm_match, lname, fm_lid, standings, league_data, odds_idx, team_match_index=None, global_rank_idx=None):
    fid = fm_match.get("id")
    h = fm_match.get("home", {})
    a = fm_match.get("away", {})
    home_name = h.get("name", "?")
    away_name = a.get("name", "?")
    home_id = str(h.get("id", ""))
    away_id = str(a.get("id", ""))

    utc = fm_match.get("status", {}).get("utcTime", "")
    start_ts = None
    if utc:
        try:
            start_ts = int(datetime.fromisoformat(utc.replace("Z", "+00:00")).timestamp())
        except Exception:
            pass

    internal_lid = INTERNAL_LEAGUE_IDS.get(lname, 0)

    # Forme : utilise l'index cross-competitions si dispo (recommande), sinon
    # fallback sur la league seule (legacy / si l'index n'a pas ete construit).
    home_form, home_opps = _compute_form(league_data, home_id, n=5, team_match_index=team_match_index, with_opps=True)
    away_form, away_opps = _compute_form(league_data, away_id, n=5, team_match_index=team_match_index, with_opps=True)
    home_rank = standings.get(int(home_id) if home_id.isdigit() else home_id, {}).get("rank")
    away_rank = standings.get(int(home_id) if False else int(away_id) if away_id.isdigit() else away_id, {}).get("rank")
    hw, dr, aw = _compute_h2h(league_data, home_id, away_id)

    # Odds via api-football (matching par nom + date) - PRIMARY
    date_iso = utc[:10]
    n_home, n_away = _norm(home_name), _norm(away_name)
    odd_payload = odds_idx.get((n_home, n_away, date_iso))
    if not odd_payload:
        for (k_home, k_away, k_date), v in odds_idx.items():
            if k_date != date_iso: continue
            home_ok = (k_home in n_home) or (n_home in k_home)
            away_ok = (k_away in n_away) or (n_away in k_away)
            if home_ok and away_ok:
                odd_payload = v
                break
    match_odds = convert_odds(odd_payload, home_name, away_name)

    # FALLBACK ESPN : si api-football n'a rien (Europa/CL/etc.), on essaie ESPN
    # ESPN couvre toutes les competitions soccer avec markets 1X2 + DC + O/U buts
    if not match_odds:
        espn_odds = _build_espn_soccer_odds(home_name, away_name, lname)
        if espn_odds:
            match_odds = espn_odds
            print(f"      [ESPN fallback] {len(espn_odds.get('markets',[]))} markets recuperes pour {home_name} vs {away_name}")

    return {
        "id": fid,
        "home": home_name, "away": away_name,
        "home_id": home_id, "away_id": away_id,
        "_fotmob_lid": fm_lid,
        "_page_url": fm_match.get("pageUrl") or "",
        "start_ts": start_ts,
        "league_id": internal_lid,
        "league": lname,
        "h2h": {"teamDuel": {"homeWins": hw, "draws": dr, "awayWins": aw}},
        "pre_match_form": {
            "homeTeam": {"form": home_form, "form_opps": home_opps,
                         "form_opp_ranks": [(global_rank_idx or {}).get(o) for o in home_opps],
                         "position": home_rank, "avgRating": None},
            "awayTeam": {"form": away_form, "form_opps": away_opps,
                         "form_opp_ranks": [(global_rank_idx or {}).get(o) for o in away_opps],
                         "position": away_rank, "avgRating": None},
        },
        "match_odds": match_odds,
        "lineups_home": None,
        "lineups_away": None,
        "team_streaks": None,
    }


def main():
    fm_reset(); af_reset()
    print(f"=== sport-picks scraper === (FotMob + api-football odds)")
    print(f"    Quota api-football dispo: {100 - quota_today()}/100")

    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    dates = [today, tomorrow]

    # Step 1: FotMob
    print(f"\n[1/3] FotMob: fixtures + standings (8 ligues)...")
    fm_matches, standings_by_league, league_data_cache = collect_fotmob_data(dates)
    print(f"  -> {len(fm_matches)} matchs J/J+1")

    if not fm_matches:
        print("\n[!] Aucun match. matches.json preserve.")
        sys.exit(1)

    # Step 2: odds api-football
    print(f"\n[2/3] api-football: odds bulk {dates}...")
    odds_idx = fetch_odds_index(dates)
    print(f"  -> {len(odds_idx)} entrees odds indexees")

    # Step 3: build
    print(f"\n[3/3] Construction des matchs...")
    # Index toutes competitions confondues : permet de calculer une forme L5
    # representative meme quand une equipe joue Coupe/Europa entre 2 matchs de
    # championnat (cas Nice : 17/05 Metz D, 10/05 Auxerre L, 02/05 Lens D,
    # 26/04 Marseille D, 22/04 Strasbourg W en Coupe de France -> W manquait
    # avant si on ne regardait que la Ligue 1).
    team_match_idx = _build_team_match_index(league_data_cache)
    n_indexed = sum(len(v) for v in team_match_idx.values())
    print(f"  -> Index forme cross-competitions : {len(team_match_idx)} equipes, {n_indexed} matchs FT")

    # Index global team_id -> rank (toutes ligues confondues) pour SoS
    global_rank_idx = {}
    for fm_lid, st in standings_by_league.items():
        for tid, info in st.items():
            r = info.get("rank")
            if r is not None:
                global_rank_idx[str(tid)] = r

    all_matches = []
    for i, (lname, fm_lid, fm_m) in enumerate(fm_matches, 1):
        h = fm_m.get("home", {}).get("name", "?")
        a = fm_m.get("away", {}).get("name", "?")
        try:
            data = build_match(fm_m, lname, fm_lid, standings_by_league.get(fm_lid, {}),
                                league_data_cache.get(fm_lid, {}), odds_idx,
                                team_match_index=team_match_idx,
                                global_rank_idx=global_rank_idx)
            all_matches.append(data)
            has_odds = "+odds" if data["match_odds"] else "(no odds)"
            print(f"  [{i}/{len(fm_matches)}] {lname}: {h} vs {a} {has_odds}")
        except Exception as e:
            print(f"  [X] {h} vs {a}: {e}")

    # Backup + ecriture
    if os.path.exists("data/matches.json") and os.path.getsize("data/matches.json") > 100:
        shutil.copy("data/matches.json", "data/matches.backup.json")
    with open("data/matches.json", "w", encoding="utf-8") as f:
        json.dump(all_matches, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] {len(all_matches)} matchs -> data/matches.json")
    print(f"     FotMob calls: {fm_calls()} | api-football calls: {af_calls()} (total jour: {quota_today()}/100)")


if __name__ == "__main__":
    main()
