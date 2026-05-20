"""
foot_odds.py - Cotes joueurs foot via The Odds API.

Marches recuperes (US bookmakers - cotes tres proches de Betclic pour markets
binaires) :
  - player_goal_scorer_anytime  -> Buteur dans le match
  - player_assists              -> Passes decisives
  - player_shots_on_target      -> Tirs cadres

Optimisations quota :
  1. On ne fetch QUE les leagues qui ont des matchs dans data/matches.json
     aujourd'hui (lecture du scraper, evite d'iterer toutes les leagues).
  2. Freshness skip 8h sur data/foot_player_odds.json (cache par jour).
  3. event_props cache 24h (lignes stables une fois publiees).
  4. Reutilise la rotation des cles ODDS_API_KEY/_KEY2 via nba_odds._get().

Budget estime : 1-2 leagues actives/jour x 2-4 matchs x 3 markets =
~10-15 credits/jour = ~300-450/mois (sur 1000 dispo combine 2 cles).

Sortie : data/foot_player_odds.json
{
  "<match_id_internal>": {
    "<player_name>": {
      "anytime_scorer": {"yes_cote": 1.65, "no_cote": 2.25, "book": "fanduel", "books": [...]},
      "assists":        {"line": 0.5, "over": 2.10, "under": 1.65, ...},
      "shots_on_target":{"line": 0.5, "over": 1.45, "under": 2.45, ...},
    }
  }
}
"""
import json, time, urllib.parse
from pathlib import Path
from datetime import datetime

from nba_odds import (
    _get,            # rotation cles + cache disque
    _cache_path,
    _cache_get,
    ODDS_API_KEYS,
    ODDS_API_BASE,
    PREFERRED_BOOKS,
    _norm_name,
)


# Mapping internal league name -> Odds API sport key
LEAGUE_TO_SPORT = {
    "Premier League":    "soccer_epl",
    "La Liga":           "soccer_spain_la_liga",
    "Bundesliga":        "soccer_germany_bundesliga",
    "Serie A":           "soccer_italy_serie_a",
    "Ligue 1":           "soccer_france_ligue_one",
    "Champions League":  "soccer_uefa_champs_league",
    "Europa League":     "soccer_uefa_europa_league",
    "Conference League": "soccer_uefa_europa_conference_lge",
}

MARKETS = {
    "player_goal_scorer_anytime": "anytime_scorer",
    "player_assists":             "assists",
    "player_shots_on_target":     "shots_on_target",
}

REGIONS = "us"   # seules region qui expose les player props soccer

# Freshness pour skip le fetch si data fresh
ODDS_REFRESH_MIN_AGE_SEC = 8 * 3600

OUTPUT_PATH = Path("data/foot_player_odds.json")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _file_age_seconds(path):
    """Age en secondes du _fetched_at stocke dans un fichier JSON."""
    if not path.exists(): return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        ts = d.get("_fetched_at")
        if not ts: return None
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00").rstrip("+00:00"))
        return (datetime.now() - dt).total_seconds()
    except Exception:
        return None


def _strip_meta(data):
    return {k: v for k, v in (data or {}).items() if not k.startswith("_")}


def _list_events(sport_key):
    """Liste des events upcoming pour un sport. Cache 4h pour eviter le burn."""
    url = f"{ODDS_API_BASE}/sports/{sport_key}/events?apiKey={{APIKEY}}"
    cache_path = _cache_path(url)
    cached = _cache_get(cache_path, ttl=4 * 3600)
    if cached is not None:
        return cached["data"] or []
    if cache_path.exists():
        try: cache_path.unlink()
        except Exception: pass
    data, _hdr = _get(url, use_cache=True)
    return data or []


def _event_props(sport_key, event_id):
    """Recupere les markets player props pour un event. Cache 24h."""
    markets = ",".join(MARKETS.keys())
    books   = ",".join(PREFERRED_BOOKS)
    params = {
        "apiKey":     "{APIKEY}",
        "regions":    REGIONS,
        "markets":    markets,
        "bookmakers": books,
        "oddsFormat": "decimal",
    }
    url = f"{ODDS_API_BASE}/sports/{sport_key}/events/{event_id}/odds?" + urllib.parse.urlencode(params, safe="{}")
    data, _hdr = _get(url, use_cache=True)
    return data


def _match_event(odds_event, fixture):
    """Match un event Odds API a un fixture scraper (data/matches.json) via team names."""
    odds_home = _norm_name(odds_event.get("home_team", ""))
    odds_away = _norm_name(odds_event.get("away_team", ""))
    fix_home  = _norm_name(fixture.get("home", ""))
    fix_away  = _norm_name(fixture.get("away", ""))
    if not (odds_home and odds_away and fix_home and fix_away):
        return False
    # match si home et away contiennent l'autre (utile pour "AFC Bournemouth" vs "Bournemouth")
    home_ok = (odds_home in fix_home) or (fix_home in odds_home)
    away_ok = (odds_away in fix_away) or (fix_away in odds_away)
    return home_ok and away_ok


def _parse_event_props(event_data):
    """Extrait {player: {market_key_internal: {data}}} pour un event."""
    players = {}
    if not event_data: return players

    for book in event_data.get("bookmakers", []):
        bkey = book.get("key", "")
        for mkt in book.get("markets", []):
            market_internal = MARKETS.get(mkt.get("key", ""))
            if not market_internal: continue
            for o in mkt.get("outcomes", []):
                # Pour anytime_scorer : name="Yes"/"No", description=player_name
                # Pour assists/shots : name="Over"/"Under", description=player_name, point=line
                pname = o.get("description") or o.get("name", "")
                side  = (o.get("name") or "").lower()
                price = o.get("price")
                point = o.get("point")
                if not pname: continue
                pdata = players.setdefault(pname, {}).setdefault(market_internal, {
                    "books": []
                })
                entry = {"book": bkey, "cote": price}
                if point is not None: entry["line"] = point
                if side in ("yes", "over"):
                    entry["side"] = "over"
                elif side in ("no", "under"):
                    entry["side"] = "under"
                pdata["books"].append(entry)

    # Finalise : pour chaque player x market, choisis la "meilleure" cote
    # (la plus elevee pour OVER/YES = la plus payante pour le user)
    for player, markets in players.items():
        for mkey, mdata in markets.items():
            books = mdata.get("books", [])
            over_entries  = [b for b in books if b.get("side") == "over"]
            under_entries = [b for b in books if b.get("side") == "under"]
            if over_entries:
                over_entries.sort(key=lambda b: b["cote"], reverse=True)
                best_over = over_entries[0]
                mdata["over"] = best_over["cote"]
                mdata["book"] = best_over["book"]
                if "line" in best_over:
                    mdata["line"] = best_over["line"]
            if under_entries:
                under_entries.sort(key=lambda b: b["cote"], reverse=True)
                mdata["under"] = under_entries[0]["cote"]
    return players


# ─── Main ────────────────────────────────────────────────────────────────────

def run(force=False):
    OUTPUT_PATH.parent.mkdir(exist_ok=True)

    if not ODDS_API_KEYS:
        print("[!] Aucune cle ODDS_API. foot_odds desactive.")
        OUTPUT_PATH.write_text("{}", encoding="utf-8")
        return {}

    # Freshness skip
    if not force:
        age = _file_age_seconds(OUTPUT_PATH)
        if age is not None and age < ODDS_REFRESH_MIN_AGE_SEC:
            print(f"=== Foot odds : skip (data a {int(age/60)} min, <8h) ===")
            try:
                return _strip_meta(json.loads(OUTPUT_PATH.read_text(encoding="utf-8")))
            except Exception:
                pass

    # Lit matches scrapes -> determine quelles leagues sont actives
    try:
        fixtures = json.load(open("data/matches.json", encoding="utf-8"))
    except Exception:
        print("[X] data/matches.json absent - foot_odds skip")
        return {}

    # Group fixtures by league
    by_league = {}
    for f in fixtures:
        lg = f.get("league") or f.get("league_name") or ""
        by_league.setdefault(lg, []).append(f)

    active_leagues = [lg for lg in by_league.keys() if lg in LEAGUE_TO_SPORT]
    print(f"=== Foot odds : {len(active_leagues)} ligue(s) active(s) avec matchs aujourd'hui ===")

    out = {}
    n_calls = 0
    for lg in active_leagues:
        sport_key = LEAGUE_TO_SPORT[lg]
        print(f"  [{lg}] sport_key={sport_key}")
        events = _list_events(sport_key)
        n_calls += 1
        if not events:
            print(f"    aucun event upcoming")
            continue
        for fix in by_league[lg]:
            matched = next((ev for ev in events if _match_event(ev, fix)), None)
            if not matched:
                print(f"    [{fix.get('home')} vs {fix.get('away')}] pas matche dans Odds API")
                continue
            print(f"    {fix.get('home')} vs {fix.get('away')}  ->  event_id {matched.get('id')}")
            props = _event_props(sport_key, matched.get("id"))
            n_calls += 1
            players = _parse_event_props(props)
            if players:
                out[str(fix.get("id"))] = players
                print(f"      {len(players)} joueurs avec props")
            time.sleep(0.4)

    # Sauvegarde avec timestamp
    out_with_meta = dict(out)
    out_with_meta["_fetched_at"] = datetime.now().isoformat(timespec="seconds")
    OUTPUT_PATH.write_text(json.dumps(out_with_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] {len(out)} matchs avec props (~{n_calls} calls API)")
    return out


if __name__ == "__main__":
    run()
