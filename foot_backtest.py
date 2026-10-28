"""
foot_backtest.py — Valide le simulateur foot contre les resultats et les cotes de cloture Pinnacle.

Walk-forward : chaque match est simule avec l'etat construit sur les seuls matchs precedents et sa
vraie composition (situation « composition officielle », ~1 h avant le coup d'envoi).
Cotes : football-data.co.uk (Top 5 ; pas de coupes d'Europe), cloture Pinnacle (PSC*, PC>2.5).

Usage : python foot_backtest.py [--from 2025-08-01] [--to 2026-06-30] [--params '{"lineup_pow": 0}']
                                [--sims 4000] [--label base] [--report label]
"""
import argparse, csv, difflib, io, json, math, sys, time, unicodedata, urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np

import foot_db
from foot_sim import FootState, simulate, _ord

FD = {47: "E0", 87: "SP1", 54: "D1", 55: "I1", 53: "F1"}
FD_DIR = Path("data/foot_db/odds")


def _season_code(season):
    a, b = season.split("/")
    return f"{a[2:]}{b[2:]}"


def load_odds(lid, season):
    """Lignes football-data (telechargees une fois, cache local)."""
    FD_DIR.mkdir(parents=True, exist_ok=True)
    p = FD_DIR / f"{FD[lid]}_{_season_code(season)}.csv"
    if not p.exists():
        url = f"https://www.football-data.co.uk/mmz4281/{_season_code(season)}/{FD[lid]}.csv"
        raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
        p.write_bytes(raw)
    txt = p.read_bytes().decode("utf-8-sig", "ignore")
    return [r for r in csv.DictReader(io.StringIO(txt)) if r.get("HomeTeam")]


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    for w in ("fc ", " fc", "afc ", " afc", "ac ", "as ", "ss ", "sc ", "1. ", "cf ", " cf", "club ", "calcio", "de futbol"):
        s = s.replace(w, " ")
    return " ".join(s.replace("-", " ").replace(".", " ").split())


def _imp(o):
    try: return 1.0 / float(o)
    except (TypeError, ValueError, ZeroDivisionError): return None


def odds_index(lid, season, names):
    """{(jour, home_id, away_id): cotes} : noms football-data apparies aux noms FotMob par similarite."""
    rows = load_odds(lid, season)
    fd_names = sorted({r["HomeTeam"] for r in rows} | {r["AwayTeam"] for r in rows})
    m = {}
    for tid, nm in names.items():
        best = max(fd_names, key=lambda f: difflib.SequenceMatcher(None, _norm(nm), _norm(f)).ratio()
                   + (0.3 if _norm(f) in _norm(nm) or _norm(nm) in _norm(f) else 0))
        m[best] = tid
    out = {}
    for r in rows:
        try:
            d, mo, y = r["Date"].split("/")
            y = int(y) + (2000 if len(y) == 2 else 0)
            day = __import__("datetime").date(y, int(mo), int(d)).toordinal()
        except Exception:
            continue
        h, a = m.get(r["HomeTeam"]), m.get(r["AwayTeam"])
        if not h or not a: continue
        ph, pd_, pa = (_imp(r.get(k)) for k in ("PSCH", "PSCD", "PSCA"))
        if not (ph and pd_ and pa):
            ph, pd_, pa = (_imp(r.get(k)) for k in ("AvgCH", "AvgCD", "AvgCA"))
        po, pu = _imp(r.get("PC>2.5")) or _imp(r.get("AvgC>2.5")), _imp(r.get("PC<2.5")) or _imp(r.get("AvgC<2.5"))
        rec = {}
        if ph and pd_ and pa:
            t = ph + pd_ + pa; rec["1x2"] = (ph / t, pd_ / t, pa / t)
        if po and pu:
            rec["o25"] = po / (po + pu)
        for dd in (0, -1, 1):
            out[(day + dd, h, a)] = rec
    return out


def _ll(p, y):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def run(start, end, params, n_sims, label):
    M = sorted(foot_db.load_all().values(), key=lambda r: (r["date"], r["id"]))
    names = defaultdict(dict)
    for r in M:
        for s in ("home", "away"):
            names[(r["league"], r["season"])][r[s]["id"]] = r[s]["name"]
    odds = {}
    for (lid, season), nm in names.items():
        if lid in FD:
            try: odds.update(odds_index(lid, season, nm))
            except Exception as e: print(f"  [!] cotes {lid} {season} : {e}")
    st = FootState(params)
    rows, t0 = [], time.time()
    d0, d1 = _ord(start), _ord(end)
    for r in M:
        day = _ord(r["date"])
        if d0 <= day <= d1 and len(st.team_recs) > 400 and r["players"]:
            sq = {side: {"xi": [p[0] for p in r["players"] if p[2] == side and p[3]],
                         "bench": [p[0] for p in r["players"] if p[2] == side and not p[3]]} for side in (1, 0)}
            inp = st.project(r["home"]["id"], r["away"]["id"], r["league"], sq[1], sq[0], day,
                             neutral=bool(r["league"] in (42, 73) and "final" in str(r.get("round", "")).lower()))
            res = simulate(inp, n=n_sims, seed=int(r["id"]) % (2 ** 31), shots_db=st.player_shots)
            hg, ag = res["home_goals"], res["away_goals"]
            o = odds.get((day, r["home"]["id"], r["away"]["id"]), {})
            rows.append({"id": r["id"], "date": r["date"][:10], "league": r["league"], "score": r["score"],
                         "p1": float(np.mean(hg > ag)), "px": float(np.mean(hg == ag)), "p2": float(np.mean(hg < ag)),
                         "po25": float(np.mean(hg + ag > 2.5)), "pbtts": float(np.mean((hg > 0) & (ag > 0))),
                         "mg": float(hg.mean() + ag.mean()), "lam": [inp["home"]["npxg"], inp["away"]["npxg"]],
                         "lm": [inp["home"]["lineup_mult"], inp["away"]["lineup_mult"]],
                         "xg": [sum(s[6] for s in r["shots"] if s[0] == 1), sum(s[6] for s in r["shots"] if s[0] == 0)],
                         "mkt": o})
            if len(rows) % 200 == 0: print(f"  {len(rows)} matchs ({time.time() - t0:.0f}s)", flush=True)
        st.add_match(r)
    out = {"label": label, "params": st.p, "rows": rows}
    Path(f"data/foot_db/backtest_{label}.json").write_text(json.dumps(out), encoding="utf-8")
    report(out)


def report(out):
    R = out["rows"]
    print(f"\n=== {out['label']} : {len(R)} matchs ===")
    y1 = lambda r: 0 if r["score"][0] > r["score"][1] else (1 if r["score"][0] == r["score"][1] else 2)
    ll = [-math.log(max([r["p1"], r["px"], r["p2"]][y1(r)], 1e-4)) for r in R]
    print(f"buts/match : simu {np.mean([r['mg'] for r in R]):.3f} reel {np.mean([sum(r['score']) for r in R]):.3f} | "
          f"xG reel {np.mean([sum(r['xg']) for r in R]):.3f} | composition x{np.median([m for r in R for m in r['lm']]):.3f} (mediane)")
    print(f"1X2 log-loss simu {np.mean(ll):.4f} | domicile simu {np.mean([r['p1'] for r in R]):.3f} reel {np.mean([y1(r) == 0 for r in R]):.3f}"
          f" | nul simu {np.mean([r['px'] for r in R]):.3f} reel {np.mean([y1(r) == 1 for r in R]):.3f}")
    K = [r for r in R if r["mkt"].get("1x2")]
    if K:
        ls = np.mean([-math.log(max([r["p1"], r["px"], r["p2"]][y1(r)], 1e-4)) for r in K])
        lm = np.mean([-math.log(max(r["mkt"]["1x2"][y1(r)], 1e-4)) for r in K])
        best = None
        for wgt in np.arange(0, 1.01, 0.1):
            l = np.mean([-math.log(max(wgt * [r["p1"], r["px"], r["p2"]][y1(r)] + (1 - wgt) * r["mkt"]["1x2"][y1(r)], 1e-4)) for r in K])
            if best is None or l < best[1]: best = (wgt, l)
        print(f"  avec cotes Pinnacle ({len(K)} matchs) : 1X2 simu {ls:.4f} marche {lm:.4f} | melange optimal {best[0]:.1f} simu -> {best[1]:.4f}")
    K = [r for r in R if r["mkt"].get("o25") is not None]
    if K:
        y = [sum(r["score"]) > 2.5 for r in K]
        print(f"  +2,5 buts ({len(K)}) : simu {np.mean([_ll(r['po25'], yy) for r, yy in zip(K, y)]):.4f} "
              f"marche {np.mean([_ll(r['mkt']['o25'], yy) for r, yy in zip(K, y)]):.4f} | taux simu {np.mean([r['po25'] for r in K]):.3f} "
              f"marche {np.mean([r['mkt']['o25'] for r in K]):.3f} reel {np.mean(y):.3f}")
    yb = [r["score"][0] > 0 and r["score"][1] > 0 for r in R]
    print(f"BTTS : log-loss {np.mean([_ll(r['pbtts'], yy) for r, yy in zip(R, yb)]):.4f} | simu {np.mean([r['pbtts'] for r in R]):.3f} reel {np.mean(yb):.3f}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2025-08-01")
    ap.add_argument("--to", dest="end", default="2026-06-30")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--sims", type=int, default=4000)
    ap.add_argument("--label", default="base")
    ap.add_argument("--report")
    a = ap.parse_args()
    if a.report:
        report(json.loads(Path(f"data/foot_db/backtest_{a.report}.json").read_text(encoding="utf-8")))
    else:
        run(a.start, a.end, json.loads(a.params), a.sims, a.label)
