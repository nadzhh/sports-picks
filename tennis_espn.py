"""
tennis_espn.py - Fetch tennis match results from ESPN public API.

Aucune cle, aucun Cloudflare, marche depuis GitHub Actions (IP datacenter).
Couvre ATP + WTA Grand Slams + tournois principaux.

Endpoint :
  https://site.api.espn.com/apis/site/v2/sports/tennis/{league}/scoreboard?dates=YYYYMMDD

Renvoie le tournoi avec toutes ses competitions (matches). On filtre ensuite
par date pour ne garder que les matches du jour cible.

Structure JSON ESPN (simplifiee) :
  events[0].groupings[].competitions[]
  competition.competitors[].athlete.displayName    (full name)
  competition.competitors[].linescores[].value     (sets won, ex [6, 4])
  competition.competitors[].winner                  (bool)
  competition.competitors[].homeAway                ('home'|'away')
  competition.status.type.completed                 (bool)
  competition.date                                  (ISO)
  competition.round.displayName                     ('Round 1', 'Final', ...)
  grouping.grouping.text                            ('Mens Singles', 'Womens Singles', ...)
"""
import json, time, unicodedata, urllib.request
from pathlib import Path

CACHE_DIR = Path("data/cache_espn_tennis")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def _norm(s):
    if not s: return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.strip().lower()


def _cache_path(league, date_str):
    return CACHE_DIR / f"{league}_{date_str}.json"


def _cache_get(path, ttl_seconds):
    if not path.exists(): return None
    if time.time() - path.stat().st_mtime > ttl_seconds: return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _fetch(league, date_str, ttl_seconds=1800):
    """Hit ESPN API. Renvoie payload brut ou None.

    league : 'atp' ou 'wta'.
    date_str : YYYY-MM-DD (sera converti en YYYYMMDD pour l'URL).
    """
    cache = _cache_path(league, date_str)
    cached = _cache_get(cache, ttl_seconds)
    if cached is not None:
        return cached
    yyyymmdd = date_str.replace("-", "")
    url = f"https://site.api.espn.com/apis/site/v2/sports/tennis/{league}/scoreboard?dates={yyyymmdd}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"  [espn {league} {date_str}] err: {e}")
        return None
    try:
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return data


def _parse_competition(comp, tournament_name, grouping_slug, grouping_text):
    """Convertit une competition ESPN en dict event slim (compat tennis_browser)."""
    status = comp.get("status") or {}
    completed = (status.get("type") or {}).get("completed", False)
    if not completed:
        return None
    competitors = comp.get("competitors") or []
    if len(competitors) != 2:
        return None
    # Identifier home/away (ESPN met homeAway, sinon ordre)
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if home is None or away is None:
        # Fallback : prend ordre 1/2
        s = sorted(competitors, key=lambda c: c.get("order", 99))
        home, away = s[0], s[1]
    h_ath = home.get("athlete") or {}
    a_ath = away.get("athlete") or {}
    h_name = h_ath.get("displayName") or h_ath.get("fullName") or ""
    a_name = a_ath.get("displayName") or a_ath.get("fullName") or ""
    if not h_name or not a_name:
        return None
    h_sets = [int(s.get("value", 0) or 0) for s in (home.get("linescores") or [])]
    a_sets = [int(s.get("value", 0) or 0) for s in (away.get("linescores") or [])]
    total_h = sum(h_sets)
    total_a = sum(a_sets)
    total_games = total_h + total_a if (h_sets and a_sets) else None
    # Set count : qui a gagne combien de sets
    h_won = sum(1 for h, a in zip(h_sets, a_sets) if h > a)
    a_won = sum(1 for h, a in zip(h_sets, a_sets) if a > h)
    set_score = f"{h_won}-{a_won}" if (h_sets and a_sets) else None
    winner = "home" if home.get("winner") else ("away" if away.get("winner") else None)
    surface_hint = None  # ESPN ne donne pas la surface directement
    return {
        "id":         comp.get("id"),
        "date":       (comp.get("date") or "")[:10],
        "home":       h_name,
        "away":       a_name,
        "home_country": (h_ath.get("flag") or {}).get("alt", ""),
        "away_country": (a_ath.get("flag") or {}).get("alt", ""),
        "completed":  True,
        "winner":     winner,
        "home_sets":  h_sets,
        "away_sets":  a_sets,
        "set_score":  set_score,
        "total_games": total_games,
        "tournament": tournament_name or "",
        "category":   "WTA" if (grouping_slug or "").startswith("womens") else ("ATP" if (grouping_slug or "").startswith("mens") else ""),
        "round":      (comp.get("round") or {}).get("displayName", ""),
        "grouping":   grouping_text or "",
        "is_doubles": "doubles" in (grouping_slug or ""),
        "source":     "espn",
    }


def fetch_events_for_date(date_str):
    """Fetch tous les events tennis (ATP + WTA) termines a une date.

    Filtre par date.date == date_str (ESPN renvoie tout le tournoi, on garde
    que ce qui s'est joue ce jour-la).

    Renvoie list de events slim (compat avec tennis_browser.fetch_sofascore_events_sync).
    """
    out = []
    seen_ids = set()
    for league in ("atp", "wta"):
        data = _fetch(league, date_str)
        if not data: continue
        for event in data.get("events", []):
            tournament_name = event.get("name") or event.get("shortName") or ""
            for grouping in event.get("groupings", []):
                g_obj = grouping.get("grouping") or {}
                grouping_slug = g_obj.get("slug", "")
                grouping_text = g_obj.get("displayName") or g_obj.get("text", "")
                for comp in grouping.get("competitions", []):
                    if (comp.get("date") or "")[:10] != date_str:
                        continue
                    parsed = _parse_competition(comp, tournament_name, grouping_slug, grouping_text)
                    if not parsed: continue
                    eid = parsed.get("id")
                    if eid in seen_ids: continue
                    seen_ids.add(eid)
                    out.append(parsed)
    return out


def fetch_events_for_dates(dates):
    """Batch fetch pour plusieurs dates. {date_str: [events]}."""
    return {d: fetch_events_for_date(d) for d in dates}


if __name__ == "__main__":
    # Smoke test sur hier
    from datetime import datetime, timedelta
    for delta in (-1, 0):
        d = (datetime.now() + timedelta(days=delta)).strftime("%Y-%m-%d")
        evs = fetch_events_for_date(d)
        completed = [e for e in evs if e.get("completed")]
        print(f"{d} : {len(evs)} events, {len(completed)} completed")
        for e in completed[:3]:
            print(f"  [{e['category']}] {e['home']} ({e.get('home_country','')}) vs {e['away']} ({e.get('away_country','')}) -> {e['set_score']} (games {e['total_games']}) round={e['round']}")
