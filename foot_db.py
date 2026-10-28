"""
foot_db.py — Base historique des matchs de foot (FotMob) pour le simulateur foot.

Competitions : Top 5 europeen + Ligue des champions + Ligue Europa. Pour chaque match termine :
  - tirs : equipe, joueur, minute, position (x, y), xG, xG cadre, situation, type, issue ;
  - joueurs : titulaire / remplacant, poste, minutes jouees (entree / sortie + raison : blessure,
    choix tactique, carton rouge), note ;
  - indisponibles (blessure, suspension...) ; cartons ; score final et a la mi-temps.
Une page de match = https://www.fotmob.com/match/<id> (l'adresse /matches/<slug> designe la
confrontation et renvoie le dernier match entre les deux equipes, pas forcement le bon).

Produit : data/foot_db/<ligue>_<saison>.json  {match_id: enregistrement}   (incremental)
Usage   : python foot_db.py [--seasons 2024/2025 2025/2026 2026/2027] [--leagues 47 87] [--max 50]
"""
import argparse, gzip, json, re, sys, time, urllib.request
from pathlib import Path

import fotmob_client as F

DB_DIR = Path("data/foot_db")
LEAGUES = {47: "Premier League", 87: "LaLiga", 54: "Bundesliga", 55: "Serie A", 53: "Ligue 1",
           42: "Champions League", 73: "Europa League"}
SEASONS = ("2024/2025", "2025/2026", "2026/2027")
DELAY = 0.6     # politesse : au moins 0,6 s entre deux pages
# Copie compressee versionnee (saisons utiles en production) : la veille GitHub en part au lieu de
# retelecharger ~2 600 pages (6 h par tranches) ; elle n'ajoute ensuite que les nouveaux matchs.
SEED = Path("data/foot_db_seed.json.gz")
SEED_SEASONS = ("2025/2026", "2026/2027")


def _path(lid, season):
    return DB_DIR / f"{lid}_{season.replace('/', '-')}.json"


def load(lid, season):
    p = _path(lid, season)
    if not p.exists() and SEED.exists():
        seed_from_snapshot()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def seed_from_snapshot():
    """Recree les fichiers absents a partir de la copie versionnee (une fois)."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    data = json.loads(gzip.decompress(SEED.read_bytes()).decode("utf-8"))
    for key, db in data.items():
        p = DB_DIR / f"{key}.json"
        if not p.exists():
            p.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def write_snapshot(seasons=SEED_SEASONS):
    """Copie compressee des saisons utiles (a commiter apres une mise a jour locale complete)."""
    data = {}
    for lid in LEAGUES:
        for s_ in seasons:
            p = _path(lid, s_)
            if p.exists(): data[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    SEED.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9))
    return SEED.stat().st_size


def load_all(leagues=LEAGUES, seasons=SEASONS):
    out = {}
    for lid in leagues:
        for s in seasons:
            for mid, r in load(lid, s).items():
                out[mid] = r
    return out


def fixtures(lid, season):
    d = F.fetch(f"https://www.fotmob.com/api/data/leagues?id={lid}&season={season.replace('/', '%2F')}",
                ttl=6 * 3600) or {}
    return ((d.get("fixtures") or {}).get("allMatches")) or []


def _page(mid):
    time.sleep(DELAY)
    F._throttle()
    raw = urllib.request.urlopen(urllib.request.Request(f"https://www.fotmob.com/match/{mid}", headers=F.HEADERS),
                                 timeout=25).read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw.decode("utf-8", "ignore"), re.S)
    return json.loads(m.group(1))["props"]["pageProps"] if m else None


def _minutes(perf, starter, end):
    """(entree, sortie, raison de sortie) depuis performance.substitutionEvents."""
    t_in, t_out, why = (0 if starter else None), None, ""
    for e in (perf or {}).get("substitutionEvents") or []:
        if e.get("type") == "subIn": t_in = e.get("time")
        elif e.get("type") == "subOut": t_out, why = e.get("time"), e.get("reason") or ""
    if t_in is None: return None, None, ""
    return t_in, (t_out if t_out is not None else end), why


def extract(pp, lid, season):
    g, c = pp.get("general") or {}, pp.get("content") or {}
    head = pp.get("header") or {}
    teams = head.get("teams") or [{}, {}]
    hid, aid = str(teams[0].get("id")), str(teams[1].get("id"))
    ev = ((c.get("matchFacts") or {}).get("events") or {}).get("events") or []
    extra = any(e.get("type") == "Half" and (e.get("halfStrShort") or "").upper() in ("ET", "EP", "PEN") for e in ev) \
        or any((e.get("time") or 0) > 100 for e in ev if e.get("type") in ("Goal", "Card", "Substitution"))
    end = 120 if extra else 90
    rec = {
        "id": str(g.get("matchId")), "league": lid, "season": season, "date": g.get("matchTimeUTCDate"),
        "round": g.get("matchRound"),
        "home": {"id": hid, "name": teams[0].get("name")}, "away": {"id": aid, "name": teams[1].get("name")},
        "score": [teams[0].get("score"), teams[1].get("score")], "extra_time": extra,
    }
    # Mi-temps : buts marques avant la pause
    ht = [0, 0]
    for e in ev:
        if e.get("type") == "Goal" and not e.get("isPenaltyShootoutEvent") and (e.get("time") or 0) <= 45:
            ht[0 if e.get("isHome") else 1] += 1
    rec["ht"] = ht
    rec["cards"] = [[e.get("time"), e.get("overloadTime") or 0, 1 if e.get("isHome") else 0,
                     str((e.get("player") or {}).get("id") or ""), e.get("card")]
                    for e in ev if e.get("type") == "Card"]
    # Joueurs : minutes jouees et raison de sortie
    red = {c_[3]: c_[0] for c_ in rec["cards"] if c_[4] in ("Red", "YellowRed")}
    players, unav = [], {}
    lu = c.get("lineup") or {}
    for side, key in ((1, "homeTeam"), (0, "awayTeam")):
        t = lu.get(key) or {}
        for starter, lst in ((True, t.get("starters") or []), (False, t.get("subs") or [])):
            for p in lst:
                pid = str(p.get("id"))
                t_in, t_out, why = _minutes(p.get("performance"), starter, end)
                if t_in is not None and pid in red and red[pid] < t_out:
                    t_out, why = red[pid], "red"
                players.append([pid, p.get("name"), side, int(starter), p.get("positionId") or p.get("usualPlayingPositionId"),
                                t_in, t_out, why, (p.get("performance") or {}).get("rating")])
        unav["home" if side else "away"] = [[str(u.get("id")), u.get("name"), (u.get("unavailability") or {}).get("type"),
                                            (u.get("unavailability") or {}).get("expectedReturn")]
                                           for u in t.get("unavailable") or []]
        rec["formation_" + ("home" if side else "away")] = t.get("formation")
    rec["players"], rec["unavailable"] = players, unav
    # Tirs
    rec["shots"] = [[1 if str(s.get("teamId")) == hid else 0, str(s.get("playerId")), s.get("min"), s.get("minAdded") or 0,
                     s.get("x"), s.get("y"), round(s.get("expectedGoals") or 0, 4), round(s.get("expectedGoalsOnTarget") or 0, 4),
                     s.get("eventType"), s.get("situation"), s.get("shotType"), int(bool(s.get("isOnTarget"))),
                     int(bool(s.get("isBlocked"))), int(bool(s.get("isOwnGoal"))), s.get("period")]
                    for s in (c.get("shotmap") or {}).get("shots") or []]
    return rec


def update(leagues, seasons, max_games=None, budget=None):
    """budget : secondes maximum (la veille constitue la base en plusieurs passages). Renvoie le nombre
    de matchs ajoutes."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    total, t0 = 0, time.time()
    for lid in leagues:
        for season in seasons:
            db = load(lid, season)
            fx = [m for m in fixtures(lid, season) if (m.get("status") or {}).get("finished")
                  and not (m.get("status") or {}).get("cancelled") and not (m.get("status") or {}).get("awarded")]
            todo = [m for m in fx if str(m["id"]) not in db]
            if max_games: todo = todo[:max_games]
            print(f"=== {LEAGUES.get(lid, lid)} {season} : {len(fx)} matchs joues, {len(db)} en base, {len(todo)} a recuperer",
                  flush=True)
            for i, m in enumerate(todo, 1):
                if budget and time.time() - t0 > budget: break
                try:
                    pp = _page(m["id"])
                    if not pp or str((pp.get("general") or {}).get("matchId")) != str(m["id"]):
                        print(f"  [!] {m['id']} : page introuvable ou autre match", flush=True); continue
                    db[str(m["id"])] = extract(pp, lid, season)
                    total += 1
                except Exception as e:
                    print(f"  [err] {m['id']} : {type(e).__name__}: {e}", flush=True)
                if i % 25 == 0:
                    _path(lid, season).write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                    print(f"  {i}/{len(todo)}", flush=True)
            _path(lid, season).write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[OK] {total} matchs ajoutes", flush=True)
    return total


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", type=int, nargs="*", default=list(LEAGUES))
    ap.add_argument("--seasons", nargs="*", default=list(SEASONS))
    ap.add_argument("--max", type=int)
    ap.add_argument("--snapshot", action="store_true", help="ecrit la copie compressee versionnee")
    a = ap.parse_args()
    if a.snapshot:
        print(f"[OK] {SEED} : {write_snapshot() / 1e6:.1f} Mo")
    else:
        update(a.leagues, a.seasons, a.max)
