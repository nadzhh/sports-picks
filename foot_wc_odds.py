"""
foot_wc_odds.py — Cotes match WC (1X2, totals, BTTS) via The Odds API.

api-football est notre source PRIMARY pour les cotes match foot, mais ne
couvre pas toujours la WC (ou compte suspendu). Ce module fournit un
FALLBACK via The Odds API (sport_key = soccer_fifa_world_cup).

Sortie : enrichit data/matches.json en place — pour chaque match WC sans
match_odds, on ajoute un payload "markets" compatible avec convert_odds.

Markets fetched :
  - h2h          → 1X2 (home_win, draw, away_win)
  - totals 2.5   → over_25 / under_25
  - btts         → btts_yes / btts_no

Budget : ~12-16 matchs WC/jour × 3 markets × 1 fetch/24h = ~5 credits/jour.
"""
import json, urllib.parse
from pathlib import Path
from datetime import datetime

try:
    from nba_odds import (
        _get, _cache_path, _cache_get,
        ODDS_API_KEYS_FOOT, ODDS_API_KEYS_NBA, ODDS_API_BASE,
        _norm_name,
    )
except ImportError:
    print("[X] nba_odds module introuvable")
    raise SystemExit(1)

WC_SPORT_KEY = "soccer_fifa_world_cup"
MARKETS      = "h2h,totals,btts"
REGIONS      = "us,uk,eu"  # large couverture
ODDS_FORMAT  = "decimal"

MATCHES_FILE = Path("data/matches.json")


def _list_wc_events():
    """Liste tous les events upcoming WC via The Odds API. Cache 4h."""
    url = f"{ODDS_API_BASE}/sports/{WC_SPORT_KEY}/events?apiKey={{APIKEY}}"
    cache_path = _cache_path(url)
    cached = _cache_get(cache_path, ttl=4 * 3600)
    if cached is not None:
        return cached.get("data") or []
    data, _hdr = _get(url, use_cache=True,
                      pool=ODDS_API_KEYS_FOOT, fallback_pool=ODDS_API_KEYS_NBA)
    return data or []


def _event_odds(event_id):
    """Fetch les markets h2h/totals/btts pour un event WC. Cache 24h."""
    params = {
        "apiKey":     "{APIKEY}",
        "regions":    REGIONS,
        "markets":    MARKETS,
        "oddsFormat": ODDS_FORMAT,
    }
    url = (f"{ODDS_API_BASE}/sports/{WC_SPORT_KEY}/events/{event_id}/odds?"
           + urllib.parse.urlencode(params, safe="{}"))
    data, _hdr = _get(url, use_cache=True,
                      pool=ODDS_API_KEYS_FOOT, fallback_pool=ODDS_API_KEYS_NBA)
    return data


def _build_markets(event_data, home_name, away_name):
    """Convertit le payload Odds API en format `markets` compatible
    convert_odds (Sofascore-style). Renvoie liste de markets.

    Utilise la MÉDIANE des cotes par outcome pour éviter les outliers
    (ex: Marathonbet qui a parfois des cotes aberrantes). On filtre aussi
    les books dont la somme des probabilités implicites est aberrante
    (book buggé / cotes inversées).
    """
    if not event_data:
        return []
    # Collecte par outcome : liste de cotes (filtrées)
    h2h_by_outcome = {}     # outcome_name -> [(price, book), ...]
    totals_by_outcome = {}  # outcome_name -> [(price, book), ...]
    btts_by_outcome = {}    # outcome_name -> [(price, book), ...]

    for book in event_data.get("bookmakers", []):
        bkey = book.get("key", "")
        # Sanity check book : sum des probas h2h implicites doit être ~100-115%
        h2h_mkt = next((m for m in book.get("markets", []) if m.get("key") == "h2h"), None)
        if h2h_mkt:
            prices = [o.get("price") for o in h2h_mkt.get("outcomes", []) if o.get("price")]
            if prices and len(prices) >= 2:
                implied = sum(1 / p for p in prices)
                # Marges normales : 1.00 (no margin) à 1.15 (15% margin).
                # En dehors → book buggé, on skip TOUT ce book.
                if implied < 0.95 or implied > 1.25:
                    continue

        for mkt in book.get("markets", []):
            mkey = mkt.get("key", "")
            for o in mkt.get("outcomes", []):
                name = o.get("name", "")
                price = o.get("price")
                point = o.get("point")
                if not price: continue
                if mkey == "h2h":
                    h2h_by_outcome.setdefault(name, []).append((float(price), bkey))
                elif mkey == "totals" and point == 2.5:
                    totals_by_outcome.setdefault(name, []).append((float(price), bkey))
                elif mkey == "btts":
                    btts_by_outcome.setdefault(name, []).append((float(price), bkey))

    def _median_pick(arr):
        """Renvoie {cote, book} de la médiane (book = celui le plus proche)."""
        if not arr: return None
        sorted_arr = sorted(arr, key=lambda x: x[0])
        mid = sorted_arr[len(sorted_arr) // 2]
        return {"cote": mid[0], "book": mid[1]}

    out_h2h       = {k: _median_pick(v) for k, v in h2h_by_outcome.items() if v}
    out_totals_25 = {k: _median_pick(v) for k, v in totals_by_outcome.items() if v}
    out_btts      = {k: _median_pick(v) for k, v in btts_by_outcome.items() if v}

    markets = []
    # 1X2 (h2h)
    if out_h2h:
        choices = []
        # The Odds API names : home_team / away_team / "Draw"
        for outcome_name, side_key in [
            (home_name, "home"),
            ("Draw", "draw"),
            (away_name, "away"),
        ]:
            o = out_h2h.get(outcome_name)
            if not o: continue
            choices.append({
                "name":    outcome_name,
                "side":    side_key,
                "cote":    o["cote"],
                "book":    o["book"],
            })
        if choices:
            markets.append({"marketName": "Full time", "choices": choices})

    # Over/Under 2.5
    if out_totals_25:
        choices = []
        for outcome_name in ("Over", "Under"):
            o = out_totals_25.get(outcome_name)
            if not o: continue
            choices.append({
                "name": f"{outcome_name} 2.5",
                "cote": o["cote"], "book": o["book"],
            })
        if choices:
            markets.append({"marketName": "Goals Over/Under (2.5)", "choices": choices})

    # BTTS
    if out_btts:
        choices = []
        for outcome_name in ("Yes", "No"):
            o = out_btts.get(outcome_name)
            if not o: continue
            choices.append({
                "name": outcome_name,
                "cote": o["cote"], "book": o["book"],
            })
        if choices:
            markets.append({"marketName": "Both teams to score", "choices": choices})

    return markets


def run():
    if not MATCHES_FILE.exists():
        print("[!] data/matches.json introuvable")
        return
    matches = json.loads(MATCHES_FILE.read_text(encoding="utf-8"))
    wc_matches = [m for m in matches if "World" in (m.get("league") or "")]
    if not wc_matches:
        print("[!] Aucun match WC dans matches.json")
        return

    # Filtre : on ne fetch QUE les matchs sans cotes valides
    todo = [m for m in wc_matches if not (m.get("match_odds") or {}).get("markets")]
    if not todo:
        print(f"[OK] Tous les matchs WC ont déjà des cotes ({len(wc_matches)} matchs)")
        return

    print(f"=== Cotes WC The Odds API : {len(todo)}/{len(wc_matches)} matchs sans cotes ===")
    events = _list_wc_events()
    print(f"  -> {len(events)} events WC disponibles dans Odds API")

    n_filled = 0
    for m in todo:
        home = m.get("home", ""); away = m.get("away", "")
        h_norm = _norm_name(home); a_norm = _norm_name(away)
        # Match event Odds API par home/away normalisés
        ev = None
        for e in events:
            eh = _norm_name(e.get("home_team", ""))
            ea = _norm_name(e.get("away_team", ""))
            home_ok = (h_norm in eh) or (eh in h_norm)
            away_ok = (a_norm in ea) or (ea in a_norm)
            if home_ok and away_ok:
                ev = e; break
            # Swap
            home_swap = (h_norm in ea) or (ea in h_norm)
            away_swap = (a_norm in eh) or (eh in a_norm)
            if home_swap and away_swap:
                ev = e; break
        if not ev:
            print(f"  [skip] {home} vs {away} : pas d'event Odds API trouvé")
            continue
        event_data = _event_odds(ev.get("id"))
        markets = _build_markets(event_data, home, away)
        if not markets:
            print(f"  [skip] {home} vs {away} : event trouvé mais markets vides")
            continue
        m["match_odds"] = {"markets": markets, "_source": "the_odds_api_fallback"}
        n_filled += 1
        print(f"  [OK] {home} vs {away} : {len(markets)} markets")

    MATCHES_FILE.write_text(json.dumps(matches, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] {n_filled} matchs WC enrichis avec cotes Odds API")


if __name__ == "__main__":
    run()
