"""
nba_backtest.py — Rejoue une saison NBA match par match avec nba_sim (walk-forward).

Pour chaque match de la saison testee, le modele ne connait que les matchs
anterieurs (la saison precedente sert d'historique de depart). On compare :
- equipe : score attendu du modele seul, de Vegas, et du melange, au score reel ;
- joueurs : simulateur vs formule actuelle de nba_picks_engine.py (L5/L10/saison
  + loi normale), sur les memes joueurs (minutes projetees >= 20) et les memes
  lignes. Les lignes de props historiques n'existant pas, la ligne de reference
  est celle qu'un bookmaker « naif » poserait : forme recente arrondie a x.5.

Effectif disponible = joueurs presents sur la feuille de match, sauf absents
pour blessure/maladie (en production : rapport de blessures ESPN).

Usage :
    python nba_backtest.py                       # teste 2025-26 avec 2024-25 en historique
    python nba_backtest.py --params '{"w_vegas": 1.0}' --label vegas100
"""
import argparse, json, math, sys, time
from collections import defaultdict

import numpy as np

from nba_boxdb import load_season, DB_DIR
from nba_props_hist import load_props, props_main_lines
from nba_sim import NBAState, simulate

PROFILE_REFRESH_DAYS = 7   # moteur possession : profils play-by-play recalcules chaque semaine (matchs passes)


def poss_as_box(rp, nh):
    """Resultat de nba_poss_sim au format de nba_sim.simulate (cles home/away -> (n, joueurs))."""
    b = rp["box"]
    out = {"n_ot": rp["n_ot"]}
    for side, sl in (("home", slice(0, nh)), ("away", slice(nh, None))):
        out[side] = {"PTS": b["PTS"][:, sl], "REB": (b["OREB"] + b["DREB"])[:, sl], "AST": b["AST"][:, sl],
                     "3PM": b["3PM"][:, sl], "STL": b["STL"][:, sl], "BLK": b["BLK"][:, sl],
                     "MIN": np.where(b["MIN"][:, sl] >= 0.5, b["MIN"][:, sl], 0)}
    return out

PROP_STATS = {"PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",), "3PM": ("3PM",), "PRA": ("PTS", "REB", "AST")}
# Marches des vraies lignes (nba_props_hist) -> composantes simulees
REAL_STATS = dict(PROP_STATS, PR=("PTS", "REB"), PA=("PTS", "AST"), RA=("REB", "AST"), STL=("STL",), BLK=("BLK",))


def _box_val(p, stat):
    return sum(p[c] for c in REAL_STATS[stat])


def _rec_val(r, stat):
    v = {"PTS": 2 * r["2PM"] + 3 * r["3PM"] + r["FTM"], "REB": r["OREB"] + r["DREB"], "AST": r["AST"],
         "3PM": r["3PM"], "STL": r["STL"], "BLK": r["BLK"]}
    return sum(v[c] for c in REAL_STATS[stat])


def old_model(hist, season):
    """Formule de nba_picks_engine.py : 0.5 L5 + 0.3 L10 + 0.2 saison, sigma = stdev L20 (plancher 15 %)."""
    games = [r for r in hist if r["MIN"] >= 15][-20:]
    if len(games) < 5: return None
    cur = [r for r in hist if r["season"] == season and r["MIN"] >= 15] or games
    out = {}
    for stat in REAL_STATS:
        l20 = [_rec_val(r, stat) for r in games]
        l5, l10 = np.mean(l20[-5:]), np.mean(l20[-10:])
        sea = np.mean([_rec_val(r, stat) for r in cur])
        mu = 0.5 * l5 + 0.3 * l10 + 0.2 * sea
        sd = max(np.std(l20, ddof=1), 0.15 * mu, 0.5)
        out[stat] = (mu, sd)
    return out


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def roster_for(side):
    out = []
    for p in side.get("players") or []:
        reason = (p.get("reason") or "").upper()
        if p.get("dnp") and reason and "COACH" not in reason:
            continue   # blesse / malade / suspendu : connu avant le match
        out.append({"id": p["id"], "name": p["name"], "pos": p.get("pos", "")})
    return out


def _ml_prob(ml):
    if ml is None: return None
    ml = float(ml)
    return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)


def run(test_year, params, n_sims, label, start=None, max_games=None, engine="box"):
    prior = load_season(test_year - 1)
    test = load_season(test_year)
    if not test:
        print(f"[!] data/nba_db/box_{test_year}.json absent : lancer nba_boxdb.py {test_year}"); return
    games = [(gid, g, test_year - 1) for gid, g in prior.items()] + [(gid, g, test_year) for gid, g in test.items()]
    games.sort(key=lambda x: (x[1]["date"], x[0]))

    props = load_props(test_year)
    st = NBAState(params)
    if engine == "poss":
        from datetime import date as _date
        from nba_pbp_profiles import build_profiles, load_data
        from nba_poss_sim import simulate as poss_simulate
        pdata = load_data([test_year - 1, test_year])
        prof, prof_day = None, None
    team_rows, player_rows, minutes_rows, real_rows = [], [], [], []
    t0 = time.time()
    by_date = defaultdict(list)
    for gid, g, season in games: by_date[g["date"]].append((gid, g, season))
    n_done = 0
    for d in sorted(by_date):
        day = by_date[d]
        for gid, g, season in day:
            if season != test_year or (start and d < start) or (max_games and n_done >= max_games): continue
            if not st.player_hist or "stats" not in g["home"]: continue
            st.season = season   # le premier match de la saison bascule l'etat
            h, a = g["home"], g["away"]
            inp = st.project(h["id"], a["id"], roster_for(h), roster_for(a), g.get("lines"))
            if engine == "poss":
                if prof is None or (_date.fromisoformat(d) - _date.fromisoformat(prof_day)).days >= PROFILE_REFRESH_DAYS:
                    prof, prof_day = build_profiles([test_year - 1, test_year], before=d, data=pdata), d
                res = poss_as_box(poss_simulate(inp, prof, n=n_sims, seed=int(gid) % (2 ** 31)), len(inp["home"]["players"]))
            else:
                res = simulate(inp, n=n_sims, seed=int(gid) % (2 ** 31))
            hp, ap = res["home"]["PTS"].sum(axis=1), res["away"]["PTS"].sum(axis=1)
            ln = g.get("lines") or {}
            team_rows.append({
                "gid": gid, "date": d, "type": g["type"],
                "act_total": h["pts"] + a["pts"], "act_margin": h["pts"] - a["pts"],
                "sim_total": float((hp + ap).mean()), "sim_margin": float((hp - ap).mean()),
                "sim_total_sd": float((hp + ap).std()), "sim_margin_sd": float((hp - ap).std()),
                "model_total": sum(inp["model"]), "model_margin": inp["model"][0] - inp["model"][1],
                "vegas_total": ln.get("total"), "vegas_margin": -ln["home_spread"] if ln.get("home_spread") is not None else None,
                "p_home": float((hp > ap).mean()), "ml_home": _ml_prob(ln.get("home_ml")), "ml_away": _ml_prob(ln.get("away_ml")),
                "p_ot_sim": float((res["n_ot"] > 0).mean()), "ot": len(h.get("quarters") or []) > 4,
            })
            for s, side in (("home", h), ("away", a)):
                actual = {p["id"]: p for p in side.get("players") or [] if not p.get("dnp") and p.get("MIN")}
                main = props_main_lines(props.get(gid))
                for i, pl in enumerate(inp[s]["players"]):
                    act = actual.get(pl["id"])
                    on = res[s]["MIN"][:, i] > 0
                    minutes_rows.append((pl["m_mu"], float(res[s]["MIN"][on, i].mean()) if on.any() else 0.0,
                                         act["MIN"] if act else 0.0))
                    if not act or on.sum() < 100: continue   # DNP : pari annule
                    old = old_model([r for r in st.player_hist.get(pl["id"], []) if not r.get("dnp")], season)
                    # Vraies lignes du bookmaker (toutes minutes projetees confondues)
                    for stat, comps in REAL_STATS.items():
                        m = main.get((pl["id"], stat))
                        if not m: continue
                        x = sum(res[s][c][:, i] for c in comps)[on]
                        r = {"stat": stat, "date": d, "line": m["line"], "over": m["over"], "under": m["under"],
                             "p_mkt": m["p_over_novig"], "p_sim": float((x > m["line"]).mean()),
                             "p_push": float((x == m["line"]).mean()), "actual": _box_val(act, stat),
                             "m_mu": pl["m_mu"], "n_season": pl["n_season"]}
                        if old:
                            mu, sd = old[stat]
                            r["p_old"] = 1 - _phi((m["line"] - mu) / sd)
                        real_rows.append(r)
                    if pl["m_mu"] < 20: continue
                    for stat, comps in PROP_STATS.items():
                        x = sum(res[s][c][:, i] for c in comps)[on]
                        av = _box_val(act, stat)
                        row = {"stat": stat, "actual": av, "sim_mean": float(x.mean()),
                               "pit": float((x < av).mean() + 0.5 * (x == av).mean()),
                               "n_season": pl["n_season"], "date": d}
                        if old:
                            mu, sd = old[stat]
                            line = math.floor(mu) + 0.5
                            row.update(old_mean=mu, line=line, old_p=1 - _phi((line - mu) / sd),
                                       sim_p=float((x > line).mean()))
                        player_rows.append(row)
            n_done += 1
            if n_done % 100 == 0:
                print(f"  {n_done} matchs ({time.time() - t0:.0f}s)")
        for gid, g, season in day:
            st.add_game(gid, g, season)

    out = {"label": label, "params": st.p, "teams": team_rows, "players": player_rows, "minutes": minutes_rows,
           "real": real_rows}
    (DB_DIR / f"backtest_{label}.json").write_text(json.dumps(out), encoding="utf-8")
    report(out)
    return out


def _dec(am):
    return 1 + am / 100.0 if am > 0 else 1 + 100.0 / -am


def bets(rows, key="p_sim", alpha=1.0, min_edge=0.05):
    """Paris simules au vrai prix : cote du cote choisi si p * cote - 1 >= min_edge.
    p = marche + alpha * (modele - marche) : alpha < 1 retrecit le modele vers le marche."""
    out = []
    for r in rows:
        if key not in r or r["actual"] == r["line"]: continue
        p = r["p_mkt"] + alpha * (r[key] - r["p_mkt"])
        ev_o, ev_u = p * _dec(r["over"]) - 1, (1 - p) * _dec(r["under"]) - 1
        if max(ev_o, ev_u) < min_edge: continue
        side_over = ev_o >= ev_u
        win = (r["actual"] > r["line"]) == side_over
        out.append((win, _dec(r["over"] if side_over else r["under"]), r))
    return out


def _roi(b):
    if not b: return "-"
    profit = sum((dec - 1) if win else -1 for win, dec, _ in b)
    return f"{100 * np.mean([w for w, _, _ in b]):4.1f}% {100 * profit / len(b):+6.1f}% /{len(b):5d}"


def report_real(R):
    R = [r for r in R if r["actual"] != r["line"]]
    if not R:
        print("\n(pas de vraies lignes : lancer nba_props_hist.py)"); return
    print(f"\n=== Contre les VRAIES lignes du bookmaker : {len(R)} props ===")
    ll = lambda p, y: -np.mean(y * np.log(np.clip(p, 1e-4, 1 - 1e-4)) + (1 - y) * np.log(np.clip(1 - p, 1e-4, 1 - 1e-4)))
    print(f"  {'stat':5s} {'n':>6s} {'logloss marche':>15s} {'simul.':>8s} {'ancien':>8s} {'melange 30%':>12s}")
    for stat in ["ALL"] + list(REAL_STATS):
        rows = [r for r in R if (stat == "ALL" or r["stat"] == stat) and "p_old" in r]
        if len(rows) < 50: continue
        y = np.array([r["actual"] > r["line"] for r in rows], float)
        pm, ps, po = (np.array([r[k] for r in rows]) for k in ("p_mkt", "p_sim", "p_old"))
        print(f"  {stat:5s} {len(rows):6d} {ll(pm, y):15.4f} {ll(ps, y):8.4f} {ll(po, y):8.4f} {ll(pm + 0.3 * (ps - pm), y):12.4f}")
    rows = [r for r in R if "p_old" in r]
    y = np.array([r["actual"] > r["line"] for r in rows], float)
    pm, ps = np.array([r["p_mkt"] for r in rows]), np.array([r["p_sim"] for r in rows])
    best = min(np.linspace(0, 1, 21), key=lambda a: ll(pm + a * (ps - pm), y))
    print(f"  biais P(over) simulateur - marche : {np.mean(ps - pm):+.3f} | overs reels {y.mean():.3f} | P(over) marche {pm.mean():.3f}")
    print(f"  poids optimal du simulateur dans le melange avec le marche : {best:.2f}"
          f" (0 = le simulateur n'apporte rien au marche)")
    print("\n  Paris simules au vrai prix (reussite, ROI, nombre) :")
    print(f"  {'seuil EV':>9s} {'simulateur brut':>24s} {'simul. retreci 50%':>24s} {'ancienne formule':>24s}")
    for t in (0.0, 0.03, 0.05, 0.10, 0.15):
        print(f"  {100 * t:8.0f}% {_roi(bets(rows, 'p_sim', 1.0, t)):>24s} {_roi(bets(rows, 'p_sim', 0.5, t)):>24s}"
              f" {_roi(bets(rows, 'p_old', 1.0, t)):>24s}")
    print("\n  Par marche (simulateur retreci 50 %, EV >= 5 %) :")
    for stat in REAL_STATS:
        b = bets([r for r in rows if r["stat"] == stat], "p_sim", 0.5, 0.05)
        if b: print(f"    {stat:5s} {_roi(b)}")
    half = sorted({r["date"] for r in rows})[len({r['date'] for r in rows}) // 2]
    for name, part in (("1re moitie", [r for r in rows if r["date"] < half]), ("2e moitie", [r for r in rows if r["date"] >= half])):
        print(f"  {name} de saison : {_roi(bets(part, 'p_sim', 0.5, 0.05))}")


def report(out):
    if out.get("real"): report_real(out["real"])
    T, Pl, M = out["teams"], out["players"], out["minutes"]
    print(f"\n=== Backtest {out['label']} : {len(T)} matchs, {len(Pl) // len(PROP_STATS)} lignes joueur ===")
    v = [r for r in T if r["vegas_total"] and r["vegas_margin"] is not None]
    mae = lambda rows, key, act: np.mean([abs(r[key] - r[act]) for r in rows])
    print("\n-- Equipe (erreur absolue moyenne, points) --")
    print(f"  {'':12s} {'total':>7s} {'ecart':>7s}")
    for name, kt, km in (("modele seul", "model_total", "model_margin"), ("Vegas", "vegas_total", "vegas_margin"),
                         ("simulation", "sim_total", "sim_margin")):
        print(f"  {name:12s} {mae(v, kt, 'act_total'):7.2f} {mae(v, km, 'act_margin'):7.2f}")
    res_tot = np.std([r["act_total"] - r["sim_total"] for r in v])
    res_mar = np.std([r["act_margin"] - r["sim_margin"] for r in v])
    print(f"  dispersion simulee total {np.mean([r['sim_total_sd'] for r in v]):.1f} vs reelle {res_tot:.1f}"
          f" | ecart {np.mean([r['sim_margin_sd'] for r in v]):.1f} vs {res_mar:.1f}")
    print(f"  prolongations simulees {100 * np.mean([r['p_ot_sim'] for r in v]):.1f} % vs reelles {100 * np.mean([r['ot'] for r in v]):.1f} %")
    ll = lambda p, y: -(y * math.log(max(p, 1e-6)) + (1 - y) * math.log(max(1 - p, 1e-6)))
    vm = [r for r in v if r["ml_home"] and r["ml_away"]]
    ll_sim = np.mean([ll(r["p_home"], r["act_margin"] > 0) for r in vm])
    ll_vg = np.mean([ll(r["ml_home"] / (r["ml_home"] + r["ml_away"]), r["act_margin"] > 0) for r in vm])
    print(f"  log-loss vainqueur : simulation {ll_sim:.4f} | Vegas sans marge {ll_vg:.4f}")

    mm = np.array(M)
    played = mm[mm[:, 2] > 0]
    print(f"\n-- Minutes (joueurs ayant joue) : erreur moyenne projection {np.mean(np.abs(played[:, 1] - played[:, 2])):.2f} min"
          f" (biais {np.mean(played[:, 1] - played[:, 2]):+.2f})")

    print("\n-- Joueurs (minutes projetees >= 20) --")
    print(f"  {'stat':5s} {'n':>5s} {'MAE sim':>8s} {'MAE old':>8s} {'Brier sim':>10s} {'Brier old':>10s}"
          f" {'picks sim':>14s} {'picks old':>14s}")
    for stat in PROP_STATS:
        rows = [r for r in Pl if r["stat"] == stat and "line" in r]
        if not rows: continue
        y = np.array([r["actual"] > r["line"] for r in rows], dtype=float)
        sp, op = np.array([r["sim_p"] for r in rows]), np.array([r["old_p"] for r in rows])

        def picks(p):
            # pari si P >= 58 % d'un cote (seuil de rentabilite ~52,4 % a 1.91)
            sel = (p >= 0.58) | (p <= 0.42)
            if not sel.any(): return "-"
            win = np.where(p[sel] >= 0.58, y[sel], 1 - y[sel])
            return f"{100 * win.mean():4.1f}% /{sel.sum():5d}"
        print(f"  {stat:5s} {len(rows):5d} {mae(rows, 'sim_mean', 'actual'):8.2f} {mae(rows, 'old_mean', 'actual'):8.2f}"
              f" {np.mean((sp - y) ** 2):10.4f} {np.mean((op - y) ** 2):10.4f} {picks(sp):>14s} {picks(op):>14s}")

    print("\n-- Calibration du simulateur (PIT : chaque decile doit contenir ~10 %) --")
    for stat in PROP_STATS:
        pit = np.array([r["pit"] for r in Pl if r["stat"] == stat])
        h = np.histogram(pit, bins=10, range=(0, 1))[0] / max(len(pit), 1) * 100
        print(f"  {stat:5s} " + " ".join(f"{x:4.1f}" for x in h))
    print("\n-- Fiabilite des probabilites (P(over) simulee par tranche -> frequence reelle) --")
    for stat in ("PTS", "REB", "AST", "PRA"):
        rows = [r for r in Pl if r["stat"] == stat and "line" in r]
        sp = np.array([r["sim_p"] for r in rows]); y = np.array([r["actual"] > r["line"] for r in rows])
        cells = []
        for lo in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
            sel = (sp >= lo) & (sp < lo + 0.1)
            if sel.sum() >= 30: cells.append(f"{int(lo * 100)}-{int(lo * 100) + 10}%:{100 * y[sel].mean():.0f}%")
        print(f"  {stat:5s} " + "  ".join(cells))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--params", default="{}")
    ap.add_argument("--sims", type=int, default=4000)
    ap.add_argument("--label", default="base")
    ap.add_argument("--engine", default="box", choices=["box", "poss"], help="moteur statistique ou possession par possession")
    ap.add_argument("--start")
    ap.add_argument("--max-games", type=int)
    ap.add_argument("--report", help="relit un backtest existant sans le relancer")
    a = ap.parse_args()
    if a.report:
        report(json.loads((DB_DIR / f"backtest_{a.report}.json").read_text(encoding="utf-8")))
    else:
        run(a.year, json.loads(a.params), a.sims, a.label, a.start, a.max_games, a.engine)
