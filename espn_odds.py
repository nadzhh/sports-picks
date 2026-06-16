"""
espn_odds.py — Fallback gratuit et illimité pour cotes soccer via ESPN.

Couvre : WC + Champions League + Premier League + La Liga + Bundesliga
+ Serie A + Ligue 1 + Europa/Conference + MLS. 100% gratuit, pas de clé,
pas de quota dur (rate limit gentil).

Format moneyline US → converti en decimal européen (Bet365-style).
Provider unique : DraftKings (le book ESPN par défaut).

Sortie : enrichit data/matches.json en place — pour chaque match foot
sans match_odds, on ajoute le payload "markets" compatible convert_odds.

Markets fournis :
  - Full time (1X2)              → home/draw/away moneyline
  - Goals Over/Under (2.5)       → over/under 2.5

Utilisation :
  python espn_odds.py            → enrichit matches.json
  ou (in code) : enrich_matches(matches_list)
"""
import json, urllib.request, urllib.parse, urllib.error, hashlib, time, unicodedata, re
from pathlib import Path
from datetime import datetime, timezone

MATCHES_FILE = Path("data/matches.json")
CACHE_DIR    = Path("data/cache_espn_odds")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

# Mapping league name (matches.json) → ESPN sport key
LEAGUE_TO_ESPN = {
    "World Cup":         "soccer/fifa.world",
    "Champions League":  "soccer/uefa.champions",
    "Europa League":     "soccer/uefa.europa",
    "Conference League": "soccer/uefa.europa.conf",
    "Premier League":    "soccer/eng.1",
    "La Liga":           "soccer/esp.1",
    "Bundesliga":        "soccer/ger.1",
    "Serie A":           "soccer/ita.1",
    "Ligue 1":           "soccer/fra.1",
    "MLS":               "soccer/usa.1",
    "Liga Portugal":     "soccer/por.1",
    "Eredivisie":        "soccer/ned.1",
    "Pro League":        "soccer/bel.1",
    "FA Cup":            "soccer/eng.fa",
    "Coupe de France":   "soccer/fra.fc",
    "Copa del Rey":      "soccer/esp.copa_del_rey",
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _cache_get(url, ttl):
    h = hashlib.md5(url.encode()).hexdigest()[:16]
    path = CACHE_DIR / f"espn_{h}.json"
    if not path.exists(): return None, path
    if time.time() - path.stat().st_mtime > ttl: return None, path
    try:
        return json.loads(path.read_text(encoding="utf-8")), path
    except Exception:
        return None, path


def _fetch(url, ttl=6 * 3600):
    cached, path = _cache_get(url, ttl)
    if cached is not None: return cached
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        r = urllib.request.urlopen(req, timeout=15)
        data = json.loads(r.read())
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data
    except urllib.error.HTTPError as e:
        print(f"  [espn HTTP {e.code}] {url[:80]}")
        return None
    except Exception as e:
        print(f"  [espn err] {url[:80]}: {type(e).__name__}: {e}")
        return None


def moneyline_to_decimal(ml):
    """Convertit cote moneyline US en decimal européen.
    +150 -> 2.50  |  -200 -> 1.50  |  0 / None -> None"""
    if ml is None: return None
    try:
        ml = float(ml)
    except Exception:
        return None
    if ml == 0: return None
    if ml > 0:
        return round(1 + ml / 100, 2)
    return round(1 + 100 / abs(ml), 2)


def _norm(s):
    if not s: return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ─── Fetch ESPN par ligue ────────────────────────────────────────────────────

def _list_events(espn_key, dates=None):
    """Liste les events d'un sport ESPN, optionally pour une liste de dates."""
    out = []
    if dates is None:
        url = f"https://site.api.espn.com/apis/site/v2/sports/{espn_key}/scoreboard"
        data = _fetch(url, ttl=2 * 3600)
        if data: out.extend(data.get("events") or [])
    else:
        for d in dates:
            url = f"https://site.api.espn.com/apis/site/v2/sports/{espn_key}/scoreboard?dates={d}"
            data = _fetch(url, ttl=4 * 3600)
            if data: out.extend(data.get("events") or [])
    return out


def _event_summary(espn_key, event_id):
    """Récupère le summary détaillé d'un event (avec pickcenter complet)."""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{espn_key}/summary?event={event_id}"
    return _fetch(url, ttl=4 * 3600)


def _extract_odds_from_pickcenter(pc, home_name, away_name):
    """Convertit un pickcenter ESPN en markets Sofascore-compatible."""
    if not pc: return None
    # Choisit le premier provider (généralement DraftKings)
    prov = pc.get("provider", {}).get("name", "draftkings").lower()

    home_ml = (pc.get("homeTeamOdds") or {}).get("moneyLine")
    away_ml = (pc.get("awayTeamOdds") or {}).get("moneyLine")
    draw_ml = (pc.get("drawOdds") or {}).get("moneyLine")
    over_ml = pc.get("overOdds")
    under_ml = pc.get("underOdds")
    over_under = pc.get("overUnder")

    home_cote = moneyline_to_decimal(home_ml)
    away_cote = moneyline_to_decimal(away_ml)
    draw_cote = moneyline_to_decimal(draw_ml)
    over_cote = moneyline_to_decimal(over_ml)
    under_cote = moneyline_to_decimal(under_ml)

    markets = []
    # Full time (1X2)
    ft_choices = []
    if home_cote: ft_choices.append({"name": home_name, "side": "home", "cote": home_cote, "book": prov})
    if draw_cote: ft_choices.append({"name": "Draw", "side": "draw", "cote": draw_cote, "book": prov})
    if away_cote: ft_choices.append({"name": away_name, "side": "away", "cote": away_cote, "book": prov})
    if ft_choices:
        markets.append({"marketName": "Full time", "choices": ft_choices})

    # Goals Over/Under
    if over_cote and under_cote and over_under:
        ou_line = round(float(over_under), 1)
        markets.append({
            "marketName": f"Goals Over/Under ({ou_line})",
            "choices": [
                {"name": f"Over {ou_line}",  "cote": over_cote,  "book": prov},
                {"name": f"Under {ou_line}", "cote": under_cote, "book": prov},
            ],
        })

    return markets if markets else None


# ─── Matching events ─────────────────────────────────────────────────────────

def _build_event_index(espn_key, dates):
    """Construit un index : (home_norm, away_norm) → (event_id, espn_key)."""
    events = _list_events(espn_key, dates=dates)
    idx = {}
    for ev in events:
        comps = (ev.get("competitions") or [{}])[0]
        competitors = comps.get("competitors") or []
        h = next((c for c in competitors if c.get("homeAway") == "home"), None)
        a = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not (h and a): continue
        h_name = (h.get("team") or {}).get("displayName") or ""
        a_name = (a.get("team") or {}).get("displayName") or ""
        if not (h_name and a_name): continue
        idx[(_norm(h_name), _norm(a_name))] = (ev["id"], espn_key, h_name, a_name)
    return idx


def _match_event(idx, fix_home, fix_away):
    """Trouve un event ESPN qui matche home/away depuis idx."""
    h, a = _norm(fix_home), _norm(fix_away)
    if (h, a) in idx: return idx[(h, a)]
    # Swap
    if (a, h) in idx: return idx[(a, h)]
    # Substring (utile pour "Olympique Lyonnais" vs "Lyon")
    for (kh, ka), v in idx.items():
        if (h in kh or kh in h) and (a in ka or ka in a): return v
        if (h in ka or ka in h) and (a in kh or kh in a): return v
    return None


# ─── Main : enrichit matches.json ────────────────────────────────────────────

def _dates_around_now():
    """Renvoie liste de dates YYYYMMDD (J-1, J, J+1, J+2)."""
    from datetime import timedelta
    now = datetime.now(timezone.utc).date()
    return [(now + timedelta(days=d)).strftime("%Y%m%d") for d in (-1, 0, 1, 2)]


def enrich_matches(matches, leagues=None):
    """Enrichit en place les matchs sans match_odds via ESPN.
    leagues : optionnellement filtre par nom de ligue."""
    dates = _dates_around_now()
    # Group by league pour fetch optimisé
    by_league = {}
    for m in matches:
        if (m.get("match_odds") or {}).get("markets"):
            continue  # Déjà des cotes (api-football, foot_wc_odds, etc.)
        lg = m.get("league") or ""
        if leagues and lg not in leagues: continue
        espn_key = LEAGUE_TO_ESPN.get(lg)
        if not espn_key: continue
        by_league.setdefault(espn_key, []).append(m)

    if not by_league:
        return 0

    n_filled = 0
    for espn_key, league_matches in by_league.items():
        idx = _build_event_index(espn_key, dates)
        if not idx:
            print(f"  [espn] aucun event pour {espn_key} sur {dates}")
            continue
        for m in league_matches:
            res = _match_event(idx, m.get("home", ""), m.get("away", ""))
            if not res:
                print(f"  [espn skip] {m.get('home')} vs {m.get('away')} : pas trouvé sur {espn_key}")
                continue
            eid, ek, ev_home, ev_away = res
            summary = _event_summary(ek, eid)
            if not summary: continue
            pc_list = summary.get("pickcenter") or []
            if not pc_list:
                # Fallback : depuis scoreboard event direct
                pass
            pc = pc_list[0] if pc_list else None
            markets = _extract_odds_from_pickcenter(pc, ev_home, ev_away) if pc else None
            if not markets: continue
            m["match_odds"] = {"markets": markets, "_source": "espn"}
            n_filled += 1
            print(f"  [espn OK] {m.get('home')} vs {m.get('away')} : {len(markets)} markets")
    return n_filled


def run():
    if not MATCHES_FILE.exists():
        print("[!] data/matches.json introuvable")
        return
    matches = json.loads(MATCHES_FILE.read_text(encoding="utf-8"))
    todo = [m for m in matches if not (m.get("match_odds") or {}).get("markets")]
    if not todo:
        print(f"[OK] Tous les matchs ont déjà des cotes ({len(matches)} matchs)")
        return
    print(f"=== ESPN odds : {len(todo)}/{len(matches)} matchs sans cotes ===")
    n = enrich_matches(matches)
    MATCHES_FILE.write_text(json.dumps(matches, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[OK] {n} matchs enrichis via ESPN")


if __name__ == "__main__":
    run()
