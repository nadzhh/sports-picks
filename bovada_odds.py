"""
bovada_odds.py — Cotes soccer via Bovada public API (gratuit, illimité).

Couverture vérifiée : WC + Premier League + La Liga + Bundesliga +
Serie A + Ligue 1 + MLS + Champions League. Markets dispos :
  - 3-Way Moneyline (1X2)
  - Total goals (Over/Under) toutes lignes : 0.5, 1.5, 2.5, 3.5, 4.5
  - Both Teams To Score
  - Double Chance (1X / X2 / 12)
  - Anytime Goal Scorer (player props)
  - To Score 2 or More Goals
  - To Assist a Goal / To Score or Assist
  - First Goal Scorer
  - Correct Score / Winning Margin
  - Corners / Cards / Shots

Format moneyline US ou decimal selon le market. Bovada renvoie souvent
decimal direct dans price.decimal.

Sortie : enrichit data/matches.json (markets) ET data/foot_player_odds.json
(player props compatible foot_odds existant).

Usage :
  python bovada_odds.py            → enrichit matches.json
  ou dans le pipeline après ESPN.
"""
import json, urllib.request, urllib.parse, urllib.error, hashlib, time, unicodedata, re
from pathlib import Path
from datetime import datetime, timezone

MATCHES_FILE       = Path("data/matches.json")
PLAYER_ODDS_FILE   = Path("data/foot_player_odds.json")
CACHE_DIR          = Path("data/cache_bovada")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.bovada.lv/sports/soccer",
}

# Mapping league name → Bovada path
LEAGUE_TO_BOVADA = {
    "World Cup":             "soccer/fifa-world-cup",
    "Champions League":      "soccer/uefa-champions-league",
    "Europa League":         "soccer/uefa-europa-league",
    "Conference League":     "soccer/uefa-europa-conference-league",
    "Premier League":        "soccer/england/premier-league",
    "La Liga":               "soccer/spain/la-liga",
    "Bundesliga":            "soccer/germany/bundesliga",
    "Serie A":               "soccer/italy/serie-a",
    "Ligue 1":               "soccer/france/ligue-1",
    "MLS":                   "soccer/mls",
    "Brasileirao":           "soccer/brazil/serie-a",
    "Eredivisie":            "soccer/netherlands/eredivisie",
    "Liga Portugal":         "soccer/portugal/primeira-liga",
    "Friendlies":            "soccer/friendlies",
    # ── Nordics (paths regroupés sous soccer/europe/<country>) ──
    "Finnish Veikkausliiga": "soccer/europe/finland",
    "Swedish Allsvenskan":   "soccer/europe/sweden",
    "Norwegian Eliteserien": "soccer/europe/norway",
    "Danish Superligaen":    "soccer/europe/denmark",
    "Icelandic Besta":       "soccer/europe/iceland",
    # ── Pays Baltes ──
    "Estonian Meistriliiga": "soccer/europe/estonia",
    "Latvian Virsliga":      "soccer/europe/latvia",
    "Lithuanian Toplyga":    "soccer/europe/lithuania",
    "Georgian Erovnuli":     "soccer/europe/georgia",
}


def _cache_path(url):
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"bov_{h}.json"


def _fetch(url, ttl=30 * 60):  # 30 min (vs 2h avant)
    path = _cache_path(url)
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        try: return json.loads(path.read_text(encoding="utf-8"))
        except: pass
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        r = urllib.request.urlopen(req, timeout=15)
        data = json.loads(r.read())
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    except urllib.error.HTTPError as e:
        print(f"  [bovada HTTP {e.code}] {url[:80]}")
        return None
    except Exception as e:
        print(f"  [bovada err] {url[:80]}: {type(e).__name__}: {e}")
        return None


def _norm(s):
    if not s: return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _list_events(competition_path):
    """Liste les events d'une compétition Bovada.

    TTL court (30 min) car Bovada n'expose les markets shots/SoT qu'à
    l'approche du match (~48h). Un cache trop long manque ces markets.
    """
    url = f"https://www.bovada.lv/services/sports/event/coupon/events/A/description/{competition_path}"
    data = _fetch(url, ttl=30 * 60)
    if not data: return []
    out = []
    for section in (data if isinstance(data, list) else []):
        for e in section.get("events", []):
            out.append(e)
    return out


def _to_decimal(price):
    """Convertit price Bovada en cote decimale."""
    if not price: return None
    d = price.get("decimal")
    if d:
        try: return round(float(d), 2)
        except: pass
    # Fallback : american
    am = price.get("american")
    if am:
        try:
            ml = float(am.replace("+", ""))
            if ml > 0: return round(1 + ml / 100, 2)
            if ml < 0: return round(1 + 100 / abs(ml), 2)
        except: pass
    return None


def _extract_team_markets(ev, home_name, away_name):
    """Extrait les markets équipe (1X2, BTTS, totals, DC) au format compatible."""
    markets = []

    # Index markets par description normalisée
    by_desc = {}  # (group_name, market_desc) → market
    for dg in ev.get("displayGroups", []):
        gname = dg.get("description", "")
        for m in dg.get("markets", []):
            md = m.get("description", "")
            by_desc.setdefault((gname, md), []).append(m)

    # ── 3-Way Moneyline (1X2) FT et 1H ──
    for m in by_desc.get(("Game Lines", "3-Way Moneyline"), []):
        outcomes = m.get("outcomes", [])
        if not outcomes: continue
        is_1h = any("- 1H" in (o.get("description") or "") for o in outcomes)
        mkt_name = "Half time" if is_1h else "Full time"
        # Skip si déjà ajouté (Bovada peut exposer 2× le même market)
        if any(mk.get("marketName") == mkt_name for mk in markets): continue
        choices = []
        for o in outcomes:
            d = o.get("description", "")
            # Strip "- 1H" pour 1H markets
            d_clean = d.replace(" - 1H", "").strip()
            cote = _to_decimal(o.get("price"))
            if not cote: continue
            if d_clean.lower() == "draw":
                side = "draw"; name = "Draw"
            elif _norm(d_clean) == _norm(home_name):
                side = "home"; name = home_name
            elif _norm(d_clean) == _norm(away_name):
                side = "away"; name = away_name
            else:
                continue
            choices.append({"name": name, "side": side, "cote": cote, "book": "bovada"})
        if choices:
            markets.append({"marketName": mkt_name, "choices": choices, "_source": "bovada"})

    # ── Both Teams To Score (FT, 1H, 2H) ──
    seen_btts = set()
    for m in by_desc.get(("Game Props", "Both Teams To Score"), []):
        outcomes = m.get("outcomes", [])
        if not outcomes: continue
        is_1h = any("- 1H" in (o.get("description") or "") for o in outcomes)
        is_2h = any("- 2H" in (o.get("description") or "") for o in outcomes)
        if is_1h: mkt_name = "Half time both teams to score"
        elif is_2h: continue  # skip 2H (rarely used)
        else: mkt_name = "Both teams to score"
        if mkt_name in seen_btts: continue
        seen_btts.add(mkt_name)
        choices = []
        for o in outcomes:
            d = (o.get("description") or "").strip()
            d_clean = d.replace(" - 1H", "").replace(" - 2H", "").strip()
            cote = _to_decimal(o.get("price"))
            if not cote: continue
            choices.append({"name": d_clean, "cote": cote, "book": "bovada"})
        if choices:
            markets.append({"marketName": mkt_name, "choices": choices, "_source": "bovada"})

    # ── Total Goals O/U toutes lignes FT + 1H (Game Lines "Total" + Alternate Lines) ──
    # FT totals
    totals_by_point_ft = {}  # point → {"Over": cote, "Under": cote}
    totals_by_point_1h = {}
    for src_key in (("Game Lines", "Total"), ("Alternate Lines", "Total Goals O/U")):
        for m in by_desc.get(src_key, []):
            for o in m.get("outcomes", []):
                d = (o.get("description") or "").strip()
                is_1h = "- 1H" in d
                is_2h = "- 2H" in d
                if is_2h: continue
                hc = (o.get("price") or {}).get("handicap")
                try: pt = float(hc) if hc else None
                except: pt = None
                if pt is None: continue
                # FT : lignes 0.5 à 4.5. 1H : 0.5 et 1.5 (les + utiles)
                if is_1h:
                    if pt not in (0.5, 1.5): continue
                    bucket = totals_by_point_1h
                else:
                    if pt not in (0.5, 1.5, 2.5, 3.5, 4.5): continue
                    bucket = totals_by_point_ft
                cote = _to_decimal(o.get("price"))
                if not cote: continue
                side = "Over" if "over" in d.lower() else ("Under" if "under" in d.lower() else None)
                if not side: continue
                bucket.setdefault(pt, {})[side] = cote

    for pt, sides in sorted(totals_by_point_ft.items()):
        choices = []
        for s in ("Over", "Under"):
            if s in sides:
                choices.append({"name": f"{s} {pt}", "cote": sides[s], "book": "bovada"})
        if choices:
            markets.append({"marketName": f"Goals Over/Under ({pt})", "choices": choices, "_source": "bovada"})

    for pt, sides in sorted(totals_by_point_1h.items()):
        choices = []
        for s in ("Over", "Under"):
            if s in sides:
                choices.append({"name": f"{s} {pt}", "cote": sides[s], "book": "bovada"})
        if choices:
            markets.append({"marketName": f"Half time goals Over/Under ({pt})", "choices": choices, "_source": "bovada"})

    # ── Double Chance ──
    for m in by_desc.get(("Game Props", "Double Chance"), []):
        outcomes = m.get("outcomes", [])
        if any("- 1H" in (o.get("description") or "") for o in outcomes):
            continue
        choices = []
        for o in outcomes:
            d = (o.get("description") or "").strip()
            cote = _to_decimal(o.get("price"))
            if not cote: continue
            # Mapping : "USA / Draw" → side 1X, "USA / Australia" → 12, "Australia / Draw" → X2
            dl = d.lower()
            h_low = home_name.lower(); a_low = away_name.lower()
            if h_low in dl and "draw" in dl:
                side = "1X"
            elif h_low in dl and a_low in dl:
                side = "12"
            elif a_low in dl and "draw" in dl:
                side = "X2"
            else:
                continue
            choices.append({"name": d, "side": side, "cote": cote, "book": "bovada"})
        if choices:
            markets.append({"marketName": "Double chance", "choices": choices, "_source": "bovada"})

    # ── Total Shots + Total Shots On-Target (Game Stats / Shots groups) ──
    # Bovada expose "Total Shots", "Total Shots On-Target" et leurs splits
    # par équipe. Format Sofascore-compatible : marketName + choiceGroup (line)
    seen_shots = set()  # dedup (internal_name, line) car Bovada expose
                        # parfois le même market dans 2 groupes (Shots + Game Stats)
    for shots_label, internal_name in [
        ("Total Shots",                "Total shots"),
        ("Total Shots - " + home_name, "Home team total shots"),
        ("Total Shots - " + away_name, "Away team total shots"),
        ("Total Shots On-Target",                "Total shots on target"),
        ("Total Shots On-Target - " + home_name, "Home team total shots on target"),
        ("Total Shots On-Target - " + away_name, "Away team total shots on target"),
    ]:
        for grp_name in ("Shots", "Game Stats"):
            for m in by_desc.get((grp_name, shots_label), []):
                outcomes = m.get("outcomes", [])
                if not outcomes: continue
                # 1H markets skip
                if any("- 1H" in (o.get("description") or "") for o in outcomes):
                    continue
                # Extract line + over/under cotes
                line = None
                over_cote = under_cote = None
                for o in outcomes:
                    d = (o.get("description") or "").strip()
                    cote = _to_decimal(o.get("price"))
                    if not cote: continue
                    # Try parse line depuis description "Over 23.5" / "Under 23.5"
                    import re as _re
                    m_line = _re.search(r"(\d+(?:\.\d+)?)", d)
                    if m_line:
                        try: line = float(m_line.group(1))
                        except: pass
                    # Side
                    if "over" in d.lower():
                        over_cote = cote
                    elif "under" in d.lower():
                        under_cote = cote
                if line and over_cote and under_cote:
                    key = (internal_name, line)
                    if key in seen_shots:
                        continue
                    seen_shots.add(key)
                    markets.append({
                        "marketName":   internal_name,
                        "choiceGroup":  str(line),
                        "choices": [
                            {"name": "Over",  "fractionalValue": f"{int((over_cote-1)*1000)}/1000",  "cote": over_cote, "book": "bovada"},
                            {"name": "Under", "fractionalValue": f"{int((under_cote-1)*1000)}/1000", "cote": under_cote, "book": "bovada"},
                        ],
                        "_source": "bovada",
                    })

    return markets


def _extract_player_props(ev):
    """Extrait les player props (anytime scorer, 2+ goals, assist) compatible
    avec data/foot_player_odds.json."""
    players = {}  # {player_name: {market_key: {over, book, ...}}}

    def _add(pname, market_key, cote, book="bovada"):
        if not pname or not cote: return
        # Strip team suffix " (USA)" / " (AUS)" etc.
        pname_clean = re.sub(r"\s*\([A-Z]{2,4}\)\s*$", "", pname).strip()
        if not pname_clean: return
        m = players.setdefault(pname_clean, {})
        cur = m.get(market_key) or {}
        # Garde la meilleure cote (la plus élevée pour OVER/YES)
        if not cur or cote > cur.get("over", 0):
            m[market_key] = {"over": cote, "book": book}

    for dg in ev.get("displayGroups", []):
        if dg.get("description") not in ("Goalscorer", "Assists"):
            continue
        for m in dg.get("markets", []):
            mdesc = m.get("description", "")
            for o in m.get("outcomes", []):
                pname = o.get("description", "")
                cote = _to_decimal(o.get("price"))
                if not cote: continue
                if mdesc == "Anytime Goal Scorer":
                    _add(pname, "anytime_scorer", cote)
                elif mdesc == "To Score 2 or More Goals":
                    _add(pname, "scorer_2_plus", cote)
                elif mdesc == "To Assist a Goal":
                    _add(pname, "assists", cote)
                elif mdesc == "To Score or Assist a Goal":
                    _add(pname, "score_or_assist", cote)

    return players


def _match_event_to_fixture(events, home_name, away_name, start_ts=None):
    """Trouve l'event Bovada qui matche un fixture matches.json."""
    h_norm = _norm(home_name); a_norm = _norm(away_name)
    for ev in events:
        desc = ev.get("description", "")  # "USA vs Australia"
        # Split par " vs " ou "@"
        for sep in (" vs ", " @ "):
            if sep in desc:
                parts = desc.split(sep)
                if len(parts) == 2:
                    h, a = _norm(parts[0]), _norm(parts[1])
                    if (h_norm in h or h in h_norm) and (a_norm in a or a in a_norm):
                        return ev
                    # Swap
                    if (h_norm in a or a in h_norm) and (a_norm in h or h in a_norm):
                        return ev
    return None


def run():
    if not MATCHES_FILE.exists():
        print("[!] data/matches.json introuvable")
        return
    matches = json.loads(MATCHES_FILE.read_text(encoding="utf-8"))

    # Group matchs par ligue pour fetch optimisé
    by_league = {}
    for m in matches:
        lg = m.get("league") or ""
        bov_path = LEAGUE_TO_BOVADA.get(lg)
        if not bov_path: continue
        by_league.setdefault(bov_path, []).append(m)

    if not by_league:
        print("[!] Aucune ligue Bovada à fetch")
        return

    print(f"=== Bovada odds : {len(by_league)} ligues à fetch ===")
    # Player odds existants (merge avec ce qu'on fetch)
    player_odds_all = {}
    if PLAYER_ODDS_FILE.exists():
        try:
            player_odds_all = json.loads(PLAYER_ODDS_FILE.read_text(encoding="utf-8"))
        except: player_odds_all = {}

    n_team = 0; n_player = 0
    for bov_path, fixtures in by_league.items():
        events = _list_events(bov_path)
        print(f"  [{bov_path}] {len(events)} events Bovada")
        for fx in fixtures:
            # Si match déjà enrichi (ESPN ou autre), on skip team markets mais peut compléter player props
            ev = _match_event_to_fixture(events, fx.get("home", ""), fx.get("away", ""))
            if not ev:
                continue
            # Team markets : remplace seulement si pas déjà présents
            existing_markets = ((fx.get("match_odds") or {}).get("markets") or [])
            if not existing_markets:
                team_markets = _extract_team_markets(ev, fx.get("home", ""), fx.get("away", ""))
                if team_markets:
                    fx["match_odds"] = {"markets": team_markets, "_source": "bovada"}
                    n_team += 1
            else:
                # Complète avec les marchés Bovada absents (ex: Over 1.5 et 3.5 quand on a seulement 2.5 de ESPN)
                existing_names = {mk.get("marketName") for mk in existing_markets}
                bov_markets = _extract_team_markets(ev, fx.get("home", ""), fx.get("away", ""))
                added = [bm for bm in bov_markets if bm.get("marketName") not in existing_names]
                if added:
                    fx["match_odds"]["markets"].extend(added)

            # Player props (toujours fetch Bovada, c'est souvent la seule source dispo)
            mid = str(fx.get("id") or "")
            player_data = _extract_player_props(ev)
            if player_data and mid:
                # Merge avec existing player_odds_all[mid]
                cur = player_odds_all.get(mid) or {}
                for p_name, markets in player_data.items():
                    cur_p = cur.get(p_name) or {}
                    for mk, mdata in markets.items():
                        cur_p[mk] = mdata
                    cur[p_name] = cur_p
                player_odds_all[mid] = cur
                n_player += len(player_data)

    MATCHES_FILE.write_text(json.dumps(matches, ensure_ascii=False, indent=2), encoding="utf-8")
    if player_odds_all:
        player_odds_all["_fetched_at"] = datetime.now(timezone.utc).isoformat()
        PLAYER_ODDS_FILE.write_text(json.dumps(player_odds_all, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] {n_team} matchs enrichis (team markets) · {n_player} player props ajoutés")


if __name__ == "__main__":
    run()
