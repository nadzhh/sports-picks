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

    for lname, info in FOTMOB_LEAGUES.items():
        fm_id = info["id"]
        d = fm_league(fm_id)
        if not d:
            print(f"  [!] FotMob: pas de data pour {lname}")
            continue
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

        # Fixtures
        all_matches = d.get("fixtures", {}).get("allMatches", []) or []
        for m in all_matches:
            utc = (m.get("status", {}).get("utcTime") or "")[:10]
            if utc not in dates:
                continue
            if m.get("status", {}).get("finished"):
                continue
            matches.append((lname, fm_id, m))

    return matches, standings_by_league, league_data_cache


# ─── Step 2: FORME W/D/L (depuis les fixtures finis) ──────────────────────────

def _compute_form(league_data, team_id_str, n=5):
    """Retourne liste W/D/L des n derniers matchs FT de l'equipe."""
    all_matches = league_data.get("fixtures", {}).get("allMatches", []) or []
    played = []
    for m in all_matches:
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
        played.append((m.get("status", {}).get("utcTime", ""), res))
    played.sort(key=lambda x: x[0], reverse=True)
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


# ─── Step 4: BUILD ─────────────────────────────────────────────────────────────

def build_match(fm_match, lname, fm_lid, standings, league_data, odds_idx):
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

    # Forme + standings + h2h
    home_form = _compute_form(league_data, home_id, n=5)
    away_form = _compute_form(league_data, away_id, n=5)
    home_rank = standings.get(int(home_id) if home_id.isdigit() else home_id, {}).get("rank")
    away_rank = standings.get(int(home_id) if False else int(away_id) if away_id.isdigit() else away_id, {}).get("rank")
    hw, dr, aw = _compute_h2h(league_data, home_id, away_id)

    # Odds via api-football (matching par nom + date)
    date_iso = utc[:10]
    n_home, n_away = _norm(home_name), _norm(away_name)
    odd_payload = odds_idx.get((n_home, n_away, date_iso))
    # Fallback fuzzy : api-football utilise parfois des noms courts
    # ('bournemouth' au lieu de 'afcbournemouth', 'tottenham' au lieu de 'tottenhamhotspur')
    # On accepte le match par substring containment.
    if not odd_payload:
        for (k_home, k_away, k_date), v in odds_idx.items():
            if k_date != date_iso: continue
            home_ok = (k_home in n_home) or (n_home in k_home)
            away_ok = (k_away in n_away) or (n_away in k_away)
            if home_ok and away_ok:
                odd_payload = v
                break
    match_odds = convert_odds(odd_payload, home_name, away_name)

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
            "homeTeam": {"form": home_form, "position": home_rank, "avgRating": None},
            "awayTeam": {"form": away_form, "position": away_rank, "avgRating": None},
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
    all_matches = []
    for i, (lname, fm_lid, fm_m) in enumerate(fm_matches, 1):
        h = fm_m.get("home", {}).get("name", "?")
        a = fm_m.get("away", {}).get("name", "?")
        try:
            data = build_match(fm_m, lname, fm_lid, standings_by_league.get(fm_lid, {}), league_data_cache.get(fm_lid, {}), odds_idx)
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
