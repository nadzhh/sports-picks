"""
tennis_browser.py - Fetch Sofascore tennis scores via Camoufox (stealth Firefox).

Solution long terme pour recuperer les scores des matchs tennis qui ne sont pas
exposes par The Odds API /scores. Sofascore bloque les requetes HTTP directes
via Cloudflare ; Camoufox passe le challenge en chargeant un vrai navigateur.

Pattern emprunte a fotmob_browser.py.

Usage :
  from tennis_browser import fetch_sofascore_events_sync
  events = fetch_sofascore_events_sync('2026-05-27')
  # -> list de {id, home, away, home_country, away_country, completed, winner,
  #            set_score, total_games, home_sets, away_sets, ...}
"""
import asyncio, json, time
from pathlib import Path

try:
    from camoufox.async_api import AsyncCamoufox
    CAMOUFOX_AVAILABLE = True
except ImportError as _e:
    print(f"  [tennis_browser] camoufox non importable : {_e}")
    CAMOUFOX_AVAILABLE = False
except Exception as _e:
    print(f"  [tennis_browser] camoufox erreur import : {_e}")
    CAMOUFOX_AVAILABLE = False

CACHE_DIR = Path("data/cache_sofascore_browser")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(date_str):
    return CACHE_DIR / f"sched_{date_str}.json"


def _cache_get(date_str, ttl_seconds):
    p = _cache_path(date_str)
    if not p.exists(): return None
    if time.time() - p.stat().st_mtime > ttl_seconds: return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_set(date_str, data):
    try:
        _cache_path(date_str).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def _parse_event(ev):
    """Transforme un event Sofascore en dict slim avec scores parses."""
    home = ev.get("homeTeam") or {}
    away = ev.get("awayTeam") or {}
    status = ev.get("status") or {}
    hs = ev.get("homeScore") or {}
    as_ = ev.get("awayScore") or {}

    # Sets par player : period1, period2, period3, ...
    home_sets, away_sets = [], []
    for i in range(1, 6):
        h_p = hs.get(f"period{i}")
        a_p = as_.get(f"period{i}")
        if h_p is None and a_p is None:
            continue
        try:
            home_sets.append(int(h_p))
            away_sets.append(int(a_p))
        except (TypeError, ValueError):
            continue

    total_home = sum(home_sets) if home_sets else None
    total_away = sum(away_sets) if away_sets else None
    total_games = (total_home + total_away) if (total_home is not None and total_away is not None) else None

    # Set score : 2-0 / 2-1 / 0-2 / 1-2 / 3-0 / 3-1 / 3-2 / 0-3 / 1-3 / 2-3
    if home_sets and away_sets:
        h_won = sum(1 for h, a in zip(home_sets, away_sets) if h > a)
        a_won = sum(1 for h, a in zip(home_sets, away_sets) if a > h)
        set_score = f"{h_won}-{a_won}"
    else:
        set_score = None

    # winner : 1 = home, 2 = away, 3 = draw (rarissime tennis)
    wc = ev.get("winnerCode")
    winner = None
    if wc == 1: winner = "home"
    elif wc == 2: winner = "away"

    # status code 100 = finished
    completed = (status.get("code") == 100) or (status.get("type") == "finished")

    return {
        "id":         ev.get("id"),
        "home":       home.get("name", ""),
        "away":       away.get("name", ""),
        "home_short": home.get("shortName", ""),
        "away_short": away.get("shortName", ""),
        "home_country": (home.get("country") or {}).get("alpha2") or "",
        "away_country": (away.get("country") or {}).get("alpha2") or "",
        "start_ts":   ev.get("startTimestamp"),
        "completed":  completed,
        "winner":     winner,
        "home_sets":  home_sets,
        "away_sets":  away_sets,
        "set_score":  set_score,
        "total_games": total_games,
        "tournament": ((ev.get("tournament") or {}).get("uniqueTournament") or {}).get("name",
                       (ev.get("tournament") or {}).get("name", "")),
        "category":   (((ev.get("tournament") or {}).get("uniqueTournament") or {}).get("category") or {}).get("name", ""),
    }


# Mode headless : sur Linux on utilise 'virtual' (Xvfb), recommande par la doc
# Camoufox pour reduire la detection en CI (https://camoufox.com/python/virtual-display/).
# Sur Windows/Mac on garde True (le mode virtual n'est dispo que sur Linux).
import sys as _sys
_HEADLESS_MODE = "virtual" if _sys.platform.startswith("linux") else True


async def _fetch_async(date_str, timeout_s=30):
    """Fetch via Camoufox : load Sofascore tennis page + intercept XHR scheduled-events."""
    if not CAMOUFOX_AVAILABLE:
        print("  [tennis_browser] camoufox non installe - skip (utilise pip install camoufox)")
        return None
    print(f"  [tennis_browser] launching Camoufox for {date_str} (headless={_HEADLESS_MODE!r})...")
    captured = []
    try:
        async with AsyncCamoufox(headless=_HEADLESS_MODE) as browser:
            try:
                page = await browser.new_page()
            except Exception as e:
                print(f"  [tennis_browser] echec new_page : {e}")
                return None
            async def on_response(resp):
                url = resp.url
                if "scheduled-events" in url and date_str in url and "tennis" in url:
                    if resp.status == 200:
                        try:
                            d = await resp.json()
                            if d and d.get("events"):
                                captured.append(d)
                        except Exception:
                            pass
            page.on("response", on_response)
            # Warmup -> obtient les cookies CF
            try:
                await page.goto("https://www.sofascore.com/tennis",
                                wait_until="domcontentloaded", timeout=timeout_s * 1000)
                await asyncio.sleep(2)
            except Exception as e:
                print(f"  [tennis_browser] warmup err : {e}")
                return None
            # Navigation directe sur l'API : avec les cookies CF en place, requete passe
            api_url = f"https://www.sofascore.com/api/v1/sport/tennis/scheduled-events/{date_str}"
            try:
                await page.goto(api_url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                await asyncio.sleep(1.5)
            except Exception as e:
                print(f"  [tennis_browser] api goto err : {e}")
            # Fallback : lit le body JSON si l'XHR n'a pas ete intercepte
            if not captured:
                try:
                    body_text = await page.evaluate("document.body.innerText")
                    if body_text and body_text.lstrip().startswith("{"):
                        d = json.loads(body_text)
                        if d.get("events"):
                            captured.append(d)
                except Exception as e:
                    print(f"  [tennis_browser] body parse err : {e}")
    except Exception as e:
        print(f"  [tennis_browser] Camoufox crash : {e}")
        return None
    if not captured:
        print(f"  [tennis_browser] {date_str} : 0 events captures (page chargee mais pas de JSON)")
        return None
    return captured[0]


def fetch_sofascore_events_sync(date_str, ttl_seconds=None):
    """Fetch les events tennis pour une date (YYYY-MM-DD). Renvoie list de slim events.

    TTL par defaut : 30 min si date <= aujourd'hui, 30 jours si date passee."""
    from datetime import datetime
    if ttl_seconds is None:
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
            today  = datetime.now().date()
            ttl_seconds = 30 * 60 if target >= today else 30 * 24 * 3600
        except Exception:
            ttl_seconds = 30 * 60

    cached = _cache_get(date_str, ttl_seconds)
    if cached is not None:
        return cached

    raw = asyncio.run(_fetch_async(date_str))
    if not raw:
        # Fallback : cache stale si dispo
        stale = _cache_get(date_str, ttl_seconds=365 * 24 * 3600)
        if stale is not None:
            print(f"  [tennis_browser] {date_str} : fetch echec, fallback stale cache ({len(stale)} events)")
            return stale
        return []
    events = [_parse_event(e) for e in (raw.get("events") or [])]
    _cache_set(date_str, events)
    print(f"  [tennis_browser] {date_str} : {len(events)} events fetches via Camoufox")
    return events


if __name__ == "__main__":
    # Smoke test
    from datetime import datetime, timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Fetching tennis events for {yesterday} via Camoufox...")
    events = fetch_sofascore_events_sync(yesterday)
    print(f"-> {len(events)} events")
    finished = [e for e in events if e.get("completed")]
    print(f"-> {len(finished)} completed")
    # Affiche les 5 premiers ATP/WTA finis
    for e in finished[:5]:
        if e.get("category") not in ("ATP", "WTA"): continue
        print(f"  {e['home']} ({e.get('home_country','')}) vs {e['away']} ({e.get('away_country','')}) -> {e.get('set_score')} (total {e.get('total_games')})")
