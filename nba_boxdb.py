"""
nba_boxdb.py — Base historique compacte des matchs NBA (source : ESPN summary).

Sert de socle au simulateur (nba_sim.py) et a son backtest : le gamelog joueur
ESPN ne donne que les tirs reussis, le summary donne le box-score complet
(tentatives 2PT/3PT/LF, rebonds off/def), les absents (DNP + raison), les lignes
DraftKings d'avant-match et le play-by-play.

Produit (par saison, annee de fin : 2025-26 -> 2026) :
- data/nba_db/box_<annee>.json        : {game_id: {date, type, home, away, players, lines}}
- data/nba_db/plays_<annee>.jsonl.gz  : 1 ligne JSON par match, play-by-play compact
                                        (pour la future simulation possession par possession)

Incremental : les matchs deja en base ne sont pas re-telecharges.

Usage :
    python nba_boxdb.py              # saison en cours
    python nba_boxdb.py 2025 2026    # saisons 2024-25 et 2025-26
"""
import gzip, json, sys, threading, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

DB_DIR = Path("data/nba_db")
DB_DIR.mkdir(parents=True, exist_ok=True)

BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"}
WORKERS = 4
SEASON_TYPES = {2: "regular", 3: "playoffs", 5: "playin"}   # 1 = preseason, ignoree

# Colonnes du play-by-play compact (v2 : horloge en secondes, coordonnees du tir en pieds
# sur un demi-terrain - x 0..50, panier en x=25 pres de y=0 - et 3 participants :
# tireur/passeur ou contreur, perdeur/intercepteur, entrant/sortant, entre-deux...)
PLAY_COLS = ["period", "clock", "team", "type", "shooting", "scoring", "value",
             "attempted", "p1", "p2", "home", "away", "x", "y", "p3"]
PLAYS_VERSION = 2

_lock = threading.Lock()


def _get(url, timeout=30):
    for attempt in range(3):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout).read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def _split(s):
    """'5-9' -> (5, 9)."""
    try:
        a, b = str(s).split("-")
        return int(a), int(b)
    except Exception:
        return 0, 0


def _num(s):
    try: return float(str(s).replace("+", ""))
    except Exception: return 0.0


def season_year(d=None):
    """Annee de fin de la saison en cours (oct. 2026 -> 2027)."""
    d = d or date.today()
    return d.year + 1 if d.month >= 10 else d.year


def list_games(year):
    """Matchs termines d'une saison (regular + play-in + playoffs) via scoreboard jour par jour."""
    start = date(year - 1, 10, 1)
    end = min(date(year, 7, 1), date.today() - timedelta(days=1))
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    games = {}

    def _day(d):
        r = _get(f"{BASE}/scoreboard?dates={d:%Y%m%d}")
        out = []
        for ev in (r or {}).get("events") or []:
            st = (ev.get("season") or {}).get("type")
            if st not in SEASON_TYPES: continue
            if ((ev.get("status") or {}).get("type") or {}).get("state") != "post": continue
            comp = (ev.get("competitions") or [{}])[0]
            ids = [int((c.get("team") or {}).get("id", 99)) for c in comp.get("competitors") or []]
            if not ids or max(ids) > 30: continue   # All-Star, Rising Stars...
            out.append((str(ev["id"]), d.isoformat(), SEASON_TYPES[st]))
        return out

    with ThreadPoolExecutor(WORKERS) as ex:
        for res in ex.map(_day, days):
            for gid, d, st in res:
                games[gid] = (d, st)
    return games


def _team_stats(t):
    s = {x["name"]: x.get("displayValue") for x in t.get("statistics") or []}
    fgm, fga = _split(s.get("fieldGoalsMade-fieldGoalsAttempted"))
    tpm, tpa = _split(s.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
    ftm, fta = _split(s.get("freeThrowsMade-freeThrowsAttempted"))
    return {
        "FGM": fgm, "FGA": fga, "3PM": tpm, "3PA": tpa, "FTM": ftm, "FTA": fta,
        "OREB": int(_num(s.get("offensiveRebounds"))), "DREB": int(_num(s.get("defensiveRebounds"))),
        "AST": int(_num(s.get("assists"))), "TO": int(_num(s.get("totalTurnovers") or s.get("turnovers"))),
        "STL": int(_num(s.get("steals"))), "BLK": int(_num(s.get("blocks"))), "PF": int(_num(s.get("fouls"))),
        "lead_changes": int(_num(s.get("leadChanges"))),
    }


def _players(team_entry):
    out = []
    for grp in team_entry.get("statistics") or []:
        keys = grp.get("labels") or []
        for a in grp.get("athletes") or []:
            info = a.get("athlete") or {}
            row = {"id": str(info.get("id", "")), "name": info.get("displayName", ""),
                   "pos": (info.get("position") or {}).get("abbreviation", ""),
                   "starter": bool(a.get("starter"))}
            stats = a.get("stats") or []
            if a.get("didNotPlay") or not stats:
                row.update(dnp=True, reason=a.get("reason", ""))
                out.append(row)
                continue
            d = dict(zip(keys, stats))
            fgm, fga = _split(d.get("FG")); tpm, tpa = _split(d.get("3PT")); ftm, fta = _split(d.get("FT"))
            row.update(
                MIN=_num(d.get("MIN")), PTS=int(_num(d.get("PTS"))),
                FGM=fgm, FGA=fga, **{"3PM": tpm, "3PA": tpa}, FTM=ftm, FTA=fta,
                OREB=int(_num(d.get("OREB"))), DREB=int(_num(d.get("DREB"))), REB=int(_num(d.get("REB"))),
                AST=int(_num(d.get("AST"))), TO=int(_num(d.get("TO"))), STL=int(_num(d.get("STL"))),
                BLK=int(_num(d.get("BLK"))), PF=int(_num(d.get("PF"))), PM=int(_num(d.get("+/-"))),
            )
            out.append(row)
    return out


def _lines(summary, home_id):
    """Lignes d'avant-match du 1er bookmaker (DraftKings en general), point de vue domicile."""
    pcs = summary.get("pickcenter") or []
    if not pcs: return None
    pc = pcs[0]
    ho, ao = pc.get("homeTeamOdds") or {}, pc.get("awayTeamOdds") or {}
    spread = pc.get("spread")
    home_spread = None
    if spread is not None:
        home_spread = -abs(spread) if ho.get("favorite") else abs(spread)
    return {
        "book": (pc.get("provider") or {}).get("name", ""),
        "home_spread": home_spread, "total": pc.get("overUnder"),
        "home_ml": ho.get("moneyLine"), "away_ml": ao.get("moneyLine"),
        "over_odds": pc.get("overOdds"), "under_odds": pc.get("underOdds"),
    }


def _plays(summary, types):
    rows = []
    for p in summary.get("plays") or []:
        t = p.get("type") or {}
        tid = t.get("id")
        if tid is not None: types[str(tid)] = t.get("text", "")
        parts = [x.get("athlete", {}).get("id") for x in p.get("participants") or []]
        co = p.get("coordinate") or {}
        x, y = co.get("x"), co.get("y")
        if x is None or y is None or abs(x) > 1000 or abs(y) > 1000:
            x = y = None   # ESPN code « sans coordonnee » par -214748340
        rows.append([
            (p.get("period") or {}).get("number"), _clock_s((p.get("clock") or {}).get("displayValue")),
            (p.get("team") or {}).get("id"), tid, int(bool(p.get("shootingPlay"))),
            int(bool(p.get("scoringPlay"))), p.get("scoreValue", 0), p.get("pointsAttempted", 0),
            parts[0] if parts else None, parts[1] if len(parts) > 1 else None,
            p.get("homeScore"), p.get("awayScore"), x, y, parts[2] if len(parts) > 2 else None,
        ])
    return rows


def _clock_s(s):
    """'11:41' -> 701.0 ; '46.4' -> 46.4 (secondes restantes dans la periode)."""
    try:
        if ":" in str(s):
            m, sec = str(s).split(":")
            return int(m) * 60 + float(sec)
        return float(s)
    except Exception:
        return None


def rebuild_plays(year):
    """Recree plays_<annee>.jsonl.gz au format v2 pour tous les matchs deja en base."""
    box = load_season(year)
    path = DB_DIR / f"plays_{year}.jsonl.gz"
    tmp = DB_DIR / f"plays_{year}.v2.tmp.gz"
    types_path = DB_DIR / "play_types.json"
    types = json.loads(types_path.read_text(encoding="utf-8")) if types_path.exists() else {}
    gids = sorted(box, key=lambda g: box[g]["date"])
    print(f"=== Play-by-play v2 {year - 1}-{year % 100:02d} : {len(gids)} matchs")
    done, failed = 0, []

    def one(gid):
        s = _get(f"{BASE}/summary?event={gid}")
        return gid, (_plays(s, types) if s else None)
    with ThreadPoolExecutor(WORKERS) as ex, gzip.open(tmp, "wt", encoding="utf-8") as f:
        for gid, plays in ex.map(one, gids):
            if not plays:
                failed.append(gid); continue
            f.write(json.dumps({"id": gid, "v": PLAYS_VERSION, "cols": PLAY_COLS, "plays": plays},
                               separators=(",", ":")) + "\n")
            done += 1
            if done % 200 == 0: print(f"  {done}/{len(gids)}")
    tmp.replace(path)
    types_path.write_text(json.dumps(types, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"  [OK] {done} matchs, {len(failed)} en echec -> {path}")


def load_plays(year):
    """{game_id: [dict par action]} depuis plays_<annee>.jsonl.gz (format v2 uniquement)."""
    path = DB_DIR / f"plays_{year}.jsonl.gz"
    out = {}
    if not path.exists(): return out
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("v") != PLAYS_VERSION: continue
            cols = rec["cols"]
            out[rec["id"]] = [dict(zip(cols, r)) for r in rec["plays"]]
    return out


def fetch_game(gid, gdate, gtype, types):
    s = _get(f"{BASE}/summary?event={gid}")
    if not s: return None, None
    comp = ((s.get("header") or {}).get("competitions") or [{}])[0]
    if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
        return None, None
    sides = {}
    for c in comp.get("competitors") or []:
        t = c.get("team") or {}
        sides[c.get("homeAway")] = {
            "id": str(t.get("id", "")), "abbr": t.get("abbreviation", ""), "name": t.get("name", ""),
            "pts": int(_num(c.get("score"))),
            "quarters": [int(_num(q.get("displayValue"))) for q in c.get("linescores") or []],
        }
    if "home" not in sides or "away" not in sides: return None, None
    box = s.get("boxscore") or {}
    for t in box.get("teams") or []:
        tid = str((t.get("team") or {}).get("id", ""))
        for side in sides.values():
            if side["id"] == tid: side["stats"] = _team_stats(t)
    for pe in box.get("players") or []:
        tid = str((pe.get("team") or {}).get("id", ""))
        for side in sides.values():
            if side["id"] == tid: side["players"] = _players(pe)
    game = {"date": gdate, "type": gtype, "home": sides["home"], "away": sides["away"],
            "lines": _lines(s, sides["home"]["id"])}
    return game, _plays(s, types)


def update_season(year):
    box_path = DB_DIR / f"box_{year}.json"
    plays_path = DB_DIR / f"plays_{year}.jsonl.gz"
    types_path = DB_DIR / "play_types.json"
    db = json.loads(box_path.read_text(encoding="utf-8")) if box_path.exists() else {}
    types = json.loads(types_path.read_text(encoding="utf-8")) if types_path.exists() else {}

    print(f"=== Saison {year - 1}-{year % 100:02d} : liste des matchs ===")
    games = list_games(year)
    todo = [(gid, d, t) for gid, (d, t) in sorted(games.items(), key=lambda x: x[1][0]) if gid not in db]
    print(f"  {len(games)} matchs termines, {len(db)} deja en base, {len(todo)} a recuperer")
    if not todo: return db

    done, failed = 0, []

    def _save():
        box_path.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        types_path.write_text(json.dumps(types, ensure_ascii=False, indent=0), encoding="utf-8")

    with ThreadPoolExecutor(WORKERS) as ex, gzip.open(plays_path, "at", encoding="utf-8") as fplays:
        futs = {ex.submit(fetch_game, gid, d, t, types): gid for gid, d, t in todo}
        for f in as_completed(futs):
            gid = futs[f]
            try: game, plays = f.result()
            except Exception as e: game, plays = None, None; print(f"  [err] {gid}: {e}")
            with _lock:
                if game is None:
                    failed.append(gid); continue
                db[gid] = game
                fplays.write(json.dumps({"id": gid, "v": PLAYS_VERSION, "cols": PLAY_COLS, "plays": plays},
                                        separators=(",", ":")) + "\n")
                done += 1
                if done % 100 == 0:
                    _save(); print(f"  {done}/{len(todo)}")
    _save()
    print(f"  [OK] {done} ajoutes, {len(failed)} en echec -> {box_path} ({box_path.stat().st_size / 1e6:.1f} Mo)")
    return db


def load_season(year):
    p = DB_DIR / f"box_{year}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--rebuild-plays":
        for y in [int(a) for a in args[1:]] or [season_year()]:
            rebuild_plays(y)
    else:
        for y in [int(a) for a in args] or [season_year()]:
            update_season(y)
