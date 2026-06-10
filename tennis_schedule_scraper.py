"""
tennis_schedule_scraper.py - Fetch upcoming tennis matches from ESPN.

Couvre les tournois NON listés par The Odds API (Boss Open Stuttgart men's,
Libéma Open 's-Hertogenbosch ATP/WTA, Queen's Club men's, Ilkley, Halle, etc.).

Sortie : data/tennis_schedule.json — matchs des 3 prochains jours avec
seulement nom + tournoi + date. Pas d'odds (ESPN n'en a pas), pas de picks.

Affiche dans Pronos V1 avec mention "Cotes non disponibles pour ce tournoi".
"""
import json, sys, hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tennis_espn as e

DATA = Path("data")
DATA.mkdir(exist_ok=True)
OUT_PATH = DATA / "tennis_schedule.json"

# Detection surface a partir du nom de tournoi (grass season en ce moment)
SURFACE_BY_TOURNAMENT = [
    ("boss open",        "Grass", "Stuttgart (Boss Open)"),
    ("libéma",           "Grass", "'s-Hertogenbosch (Libéma)"),
    ("libema",           "Grass", "'s-Hertogenbosch (Libéma)"),
    ("hsbc championships","Grass","Queen's Club (London)"),
    ("queen's club",     "Grass", "Queen's Club (London)"),
    ("queens club",      "Grass", "Queen's Club (London)"),
    ("ilkley",           "Grass", "Ilkley"),
    ("halle",            "Grass", "Halle"),
    ("mallorca",         "Grass", "Mallorca"),
    ("'s-hertogenbosch", "Grass", "'s-Hertogenbosch"),
    ("eastbourne",       "Grass", "Eastbourne"),
    ("nottingham",       "Grass", "Nottingham"),
    ("birmingham",       "Grass", "Birmingham"),
    ("bad homburg",      "Grass", "Bad Homburg"),
    ("berlin",           "Grass", "Berlin"),
    ("wimbledon",        "Grass", "Wimbledon"),
    ("french open",      "Clay",  "Roland-Garros"),
    ("roland",           "Clay",  "Roland-Garros"),
    ("madrid",           "Clay",  "Madrid"),
    ("rome",             "Clay",  "Rome"),
    ("monte",            "Clay",  "Monte-Carlo"),
    ("hamburg",          "Clay",  "Hamburg"),
    ("munich",           "Clay",  "Munich"),
    ("barcelona",        "Clay",  "Barcelona"),
    ("strasbourg",       "Clay",  "Strasbourg"),
    ("us open",          "Hard",  "US Open"),
    ("aus open",         "Hard",  "Australian Open"),
    ("australian open",  "Hard",  "Australian Open"),
    ("indian wells",     "Hard",  "Indian Wells"),
    ("miami",            "Hard",  "Miami"),
    ("cincinnati",       "Hard",  "Cincinnati"),
    ("shanghai",         "Hard",  "Shanghai"),
    ("paris masters",    "Hard",  "Paris Masters"),
    ("dubai",            "Hard",  "Dubai"),
    ("doha",             "Hard",  "Doha"),
    ("rotterdam",        "Hard",  "Rotterdam"),
    ("basel",            "Hard",  "Basel"),
    ("vienna",           "Hard",  "Vienna"),
    ("tokyo",            "Hard",  "Tokyo"),
]


def _infer_surface_label(tournament_name):
    n = (tournament_name or "").lower()
    for kw, surf, label in SURFACE_BY_TOURNAMENT:
        if kw in n:
            return surf, label
    return "Hard", tournament_name


def _event_id(home, away, date):
    """ID stable hash basé sur (home, away, date)."""
    raw = f"{home}|{away}|{date}".encode("utf-8")
    return "esp_" + hashlib.md5(raw).hexdigest()[:14]


def _parse_upcoming(comp, tournament_name, grouping_slug):
    """Parse une competition ESPN upcoming."""
    if "doubles" in (grouping_slug or ""):
        return None
    status = comp.get("status") or {}
    if (status.get("type") or {}).get("completed"):
        return None
    competitors = comp.get("competitors") or []
    if len(competitors) != 2:
        return None
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if home is None or away is None:
        s = sorted(competitors, key=lambda c: c.get("order", 99))
        home, away = s[0], s[1]
    h_name = (home.get("athlete") or {}).get("displayName") or (home.get("athlete") or {}).get("fullName") or ""
    a_name = (away.get("athlete") or {}).get("displayName") or (away.get("athlete") or {}).get("fullName") or ""
    if not (h_name and a_name): return None
    # Skip TBD vs TBD
    if h_name.lower() in ("tbd","bye","?") or a_name.lower() in ("tbd","bye","?"):
        return None
    iso = comp.get("date") or ""
    try:
        start_dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        start_ts = int(start_dt.timestamp())
    except Exception:
        return None
    tour = "WTA" if (grouping_slug or "").startswith("womens") else "ATP"
    surface, label = _infer_surface_label(tournament_name)
    rnd = ((comp.get("round") or {}).get("displayName")) or ""
    return {
        "event_id": _event_id(h_name, a_name, iso),
        "tournament": label,
        "tournament_raw": tournament_name,
        "tour": tour,
        "surface": surface,
        "round": rnd,
        "start_ts": start_ts,
        "start_iso": start_dt.isoformat(),
        "home_name": h_name,
        "away_name": a_name,
        "home_country": (home.get("athlete") or {}).get("flag", {}).get("alt", "") if isinstance(home.get("athlete", {}).get("flag"), dict) else "",
        "away_country": (away.get("athlete") or {}).get("flag", {}).get("alt", "") if isinstance(away.get("athlete", {}).get("flag"), dict) else "",
        "source": "espn",
    }


def fetch_all(n_days=3):
    """Fetch tous les matchs upcoming des n_days prochains jours."""
    out = []
    seen = set()
    for delta in range(0, n_days + 1):
        d = (datetime.now() + timedelta(days=delta)).strftime("%Y-%m-%d")
        for league in ("atp", "wta"):
            data = e._fetch(league, d)
            if not data: continue
            for event in data.get("events", []):
                tournament_name = event.get("name") or event.get("shortName") or ""
                for grouping in event.get("groupings", []):
                    g = grouping.get("grouping") or {}
                    slug = g.get("slug", "")
                    for comp in grouping.get("competitions", []):
                        if (comp.get("date") or "")[:10] != d:
                            continue
                        parsed = _parse_upcoming(comp, tournament_name, slug)
                        if not parsed: continue
                        eid = parsed["event_id"]
                        if eid in seen: continue
                        seen.add(eid)
                        out.append(parsed)
    # Filtre : > now seulement
    now_ts = datetime.now(timezone.utc).timestamp()
    out = [m for m in out if m.get("start_ts", 0) > now_ts]
    out.sort(key=lambda m: m["start_ts"])
    return out


def main():
    print(f"Tennis schedule (ESPN) -> {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    matches = fetch_all(n_days=3)
    by_tournament = {}
    for m in matches:
        by_tournament.setdefault(m["tournament"], 0)
        by_tournament[m["tournament"]] += 1
    print(f"  [espn] {len(matches)} matchs sur {len(by_tournament)} tournoi(s)")
    for t, n in sorted(by_tournament.items(), key=lambda x: -x[1]):
        print(f"    · {t}: {n}")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_matches": len(matches),
        "matches": matches,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
