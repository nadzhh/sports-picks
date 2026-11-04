"""
foot_comps.py — registre des competitions de la base foot (data/foot_competitions.json), decouvert sur FotMob.

Demande de Wilson (04/11/2026) : analyser correctement le Top 5, TOUS les matchs de coupe d'Europe (C1, C3, C4),
des coupes nationales et des selections. Il faut donc aussi l'historique des adversaires : le championnat de chaque
club engage en coupe d'Europe, celui des clubs qui affrontent le Top 5 en coupe nationale (2e divisions...), et les
competitions internationales.

Decouverte (API FotMob, cache disque, ~1 requete par club et par competition, 1 fois par semaine) :
  1. clubs = Top 5 (classements) + clubs de C1 / C3 / C4 des saisons suivies ;
  2. pour chaque club : championnat principal (primaryLeagueId) + competitions de son calendrier (hors amicaux) ;
  3. championnats des adversaires du Top 5 en coupe nationale (au moins MIN_CUP_MATCHES matchs) ;
  4. competitions des grandes selections (Ligue des nations, qualifications, Euro, Coupe du monde).
Chaque competition : nom, type (league / cup / euro / intl), pays, saisons FotMob qui couvrent 2025-26 et 2026-27.

    python foot_comps.py           # (re)decouvre et affiche le registre
"""
import json, sys, time
from collections import Counter, defaultdict
from pathlib import Path

import fotmob_client as F

PATH = Path("data/foot_competitions.json")   # versionne (publie par la veille) : la CI n'a pas a tout redecouvrir
TOP5 = {47: "Premier League", 87: "LaLiga", 54: "Bundesliga", 55: "Serie A", 53: "Ligue 1"}
EURO = {42: "Champions League", 73: "Europa League", 10216: "Conference League"}
SEASONS = ("2025/2026", "2026/2027")
FRIENDLY = {489}                                  # Club Friendlies
# Coupes nationales et Supercoupes du Top 5 (+ Supercoupe d'Europe) : ajoutees d'office (les clubs de l'elite n'entrent
# en FA Cup / Coupe de France qu'en decembre-janvier, absentes des calendriers en debut de saison)
TOP5_CUPS = {132: "FA Cup", 133: "EFL Cup", 247: "Community Shield", 138: "Copa del Rey", 139: "Supercopa",
             209: "DFB Pokal", 8924: "DFL-Supercup", 141: "Coppa Italia", 222: "Supercoppa", 134: "Coupe de France",
             207: "Trophee des Champions", 74: "UEFA Super Cup"}
NATIONS = (6723, 8491, 6720, 8570, 8204, 8361, 6708, 8263, 9537, 8256)   # FRA ENG ESP GER ITA POR NED BEL CRO SUI
MIN_CUP_MATCHES = 3   # matchs de coupe nationale contre le Top 5 pour ajouter le championnat de l'adversaire
TTL_TEAM, TTL_LEAGUE = 7 * 86400, 2 * 86400


def _league(lid, season=None):
    url = f"https://www.fotmob.com/api/data/leagues?id={lid}" + (f"&season={season.replace('/', '%2F')}" if season else "")
    return F.fetch(url, ttl=TTL_LEAGUE) or {}


def _team(tid):
    return F.fetch(f"https://www.fotmob.com/api/data/teams?id={tid}", ttl=TTL_TEAM) or {}


def seasons_for(lid):
    """Saisons FotMob d'une competition qui recouvrent 2025-26 / 2026-27 (« 2025/2026 » ou « 2025 », « 2026 »)."""
    av = _league(lid).get("allAvailableSeasons") or []
    want = set(SEASONS) | {"2025", "2026"}
    return [s for s in av if s in want]


def _teams_of(lid, season):
    fx = ((_league(lid, season).get("fixtures") or {}).get("allMatches")) or []
    return {str(m["home"]["id"]) for m in fx} | {str(m["away"]["id"]) for m in fx}, fx


def discover(verbose=True):
    comps, clubs = {}, set()

    def add(lid, name, kind, country=""):
        lid = int(lid)
        if lid in FRIENDLY: return
        c = comps.setdefault(lid, {"name": name, "kind": kind, "country": country})
        if kind in ("euro", "cup") and c["kind"] == "league": c["kind"] = kind

    for lid, nm in TOP5.items(): add(lid, nm, "league")
    for lid, nm in EURO.items(): add(lid, nm, "euro", "INT")
    for lid, nm in TOP5_CUPS.items(): add(lid, nm, "cup")
    for lid in list(TOP5) + list(EURO):
        for s in SEASONS:
            t, _ = _teams_of(lid, s)
            clubs |= t
    if verbose: print(f"{len(clubs)} clubs (Top 5 + coupes d'Europe)", flush=True)
    top5_clubs = set()
    for lid in TOP5:
        for s in SEASONS: top5_clubs |= _teams_of(lid, s)[0]
    cup_opp = Counter()
    for i, tid in enumerate(sorted(clubs), 1):
        d = _team(tid)
        det = d.get("details") or {}
        if det.get("primaryLeagueId"):
            add(det["primaryLeagueId"], det.get("primaryLeagueName") or "", "league", det.get("country") or "")
        for m in ((d.get("fixtures") or {}).get("allFixtures") or {}).get("fixtures") or []:
            tr = m.get("tournament") or {}
            if tr.get("leagueId") and tr["leagueId"] not in comps and tr["leagueId"] not in FRIENDLY:
                add(tr["leagueId"], tr.get("name") or "", "cup", det.get("country") or "")
            # adversaires du Top 5 en coupe NATIONALE (pas les tournois d'avant-saison : MLS, J.League... sinon)
            if tid in top5_clubs and tr.get("leagueId") and int(tr["leagueId"]) in TOP5_CUPS:
                opp = (m.get("opponent") or {}).get("id")
                if opp and str(opp) not in top5_clubs: cup_opp[str(opp)] += 1
        if verbose and i % 25 == 0: print(f"  {i}/{len(clubs)} clubs", flush=True)
    # championnats des adversaires de coupe nationale (2e divisions...) : au moins MIN_CUP_MATCHES matchs par championnat
    by_league = Counter()
    names = {}
    for opp, n in cup_opp.items():
        det = _team(opp).get("details") or {}
        if det.get("primaryLeagueId"):
            by_league[int(det["primaryLeagueId"])] += n
            names[int(det["primaryLeagueId"])] = (det.get("primaryLeagueName") or "", det.get("country") or "")
    for lid, n in by_league.items():
        if n >= MIN_CUP_MATCHES and lid not in comps:
            add(lid, names[lid][0], "league", names[lid][1])
    # selections nationales : competitions officielles de leur calendrier
    for nid in NATIONS:
        for m in ((_team(nid).get("fixtures") or {}).get("allFixtures") or {}).get("fixtures") or []:
            tr = m.get("tournament") or {}
            nm = tr.get("name") or ""
            if tr.get("leagueId") and "friendl" not in nm.lower() and "amical" not in nm.lower():
                add(tr["leagueId"], nm, "intl", "INT")
    # saisons disponibles
    for lid, c in comps.items():
        c["seasons"] = seasons_for(lid)
    comps = {lid: c for lid, c in comps.items() if c["seasons"]}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps({"at": time.time(), "comps": {str(k): v for k, v in comps.items()}}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return comps


def load(max_age_days=7, refresh=False):
    """Registre {id: {...}} ; redecouvert s'il a plus de max_age_days (ou s'il manque)."""
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        if not refresh and time.time() - d["at"] < max_age_days * 86400:
            return {int(k): v for k, v in d["comps"].items()}
    except (OSError, ValueError, KeyError):
        pass
    try:
        return discover(verbose=False)
    except Exception as e:
        print(f"  [!] registre des competitions : {type(e).__name__}: {e}")
        return {**{k: {"name": v, "kind": "league", "country": "", "seasons": list(SEASONS)} for k, v in TOP5.items()},
                **{k: {"name": v, "kind": "euro", "country": "INT", "seasons": list(SEASONS)} for k, v in EURO.items()}}


def cups(comps=None):
    """Competitions qui melangent des championnats differents (coupes d'Europe, coupes nationales, Supercoupes)."""
    comps = comps or load()
    return {lid for lid, c in comps.items() if c["kind"] in ("euro", "cup")}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    c = discover()
    by = defaultdict(list)
    for lid, x in sorted(c.items(), key=lambda kv: (kv[1]["kind"], kv[1]["country"], kv[1]["name"])):
        by[x["kind"]].append(f"{lid} {x['name']} ({x['country']}) {'/'.join(x['seasons'])}")
    for k, L in by.items():
        print(f"\n== {k} : {len(L)}")
        for l in L: print("  " + l)
    print(f"\n{len(c)} compétitions en {time.time() - t0:.0f} s")
