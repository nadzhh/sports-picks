"""
nba_compare_day.py — Controle a posteriori : simulations d'un jour vs resultats reels.

Reconstruit les deux moteurs « a la veille » (aucun match du jour ni posterieur dans
l'etat ni dans les profils play-by-play), simule chaque match de la date avec les
memes informations qu'avant le match (effectif disponible = feuille de match sans
les absents pour blessure, lignes DraftKings d'avant-match), puis compare :
- equipes : score attendu (moteur stat, moteur possession, Vegas) vs score reel ;
- joueurs : moyenne projetee vs stats reelles (PTS, REB, AST, 3PM, PRA, minutes) ;
- props DraftKings : probabilite simulee vs marche, et resultat reel.

Usage : python nba_compare_day.py 2026-10-26
"""
import math
import sys

import numpy as np

from nba_backtest import roster_for
from nba_boxdb import load_season, season_year
from nba_pbp_profiles import build_profiles
from nba_poss_sim import simulate as poss_simulate
from nba_props_hist import load_props, props_main_lines
from nba_sim import NBAState, simulate as box_simulate

STATS = {"PTS": ("PTS",), "REB": ("OREB", "DREB"), "AST": ("AST",), "3PM": ("3PM",), "PRA": ("PTS", "OREB", "DREB", "AST")}
PROP_STATS = {"PTS": ("PTS",), "REB": ("REB",), "AST": ("AST",), "3PM": ("3PM",), "PRA": ("PTS", "REB", "AST"),
              "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST")}


def state_before(day, params=None):
    y = season_year(__import__("datetime").date.fromisoformat(day))
    st = NBAState(params)
    games = [(gid, g, s) for s in (y - 1, y) for gid, g in load_season(s).items() if g["date"] < day]
    games.sort(key=lambda x: (x[1]["date"], x[0]))
    for gid, g, s in games:
        st.add_game(gid, g, s)
    st.season = y
    return st, y


def _actual(p, stat):
    v = {"PTS": p["PTS"], "REB": p["REB"], "AST": p["AST"], "3PM": p["3PM"]}
    return sum(v[c] for c in PROP_STATS[stat])


def _ll(p, y):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def compare(day, n_box=10000, n_poss=5000, params=None):
    st, y = state_before(day, params)
    prof = build_profiles([y - 1, y], before=day)
    box = {gid: g for gid, g in load_season(y).items() if g["date"] == day}
    props = load_props(y)
    teams, players, prop_rows = [], [], []
    for gid, g in sorted(box.items()):
        h, a = g["home"], g["away"]
        inp = st.project(h["id"], a["id"], roster_for(h), roster_for(a), g.get("lines"))
        rb = box_simulate(inp, n=n_box, seed=int(gid) % 2 ** 31)
        rp = poss_simulate(inp, prof, n=n_poss, seed=int(gid) % 2 ** 31)
        L = g.get("lines") or {}
        vegas = None
        if L.get("total") and L.get("home_spread") is not None:
            vegas = ((L["total"] - L["home_spread"]) / 2, (L["total"] + L["home_spread"]) / 2)
        bh, ba = rb["home"]["PTS"].sum(axis=1), rb["away"]["PTS"].sum(axis=1)
        ph, pa = rp["score"][:, 0], rp["score"][:, 1]
        teams.append({"game": f'{a["abbr"]} @ {h["abbr"]}', "real": (h["pts"], a["pts"]),
                      "box": (bh.mean(), ba.mean()), "poss": (ph.mean(), pa.mean()), "vegas": vegas,
                      "p_box": (bh > ba).mean(), "p_poss": (ph > pa).mean(), "home_won": h["pts"] > a["pts"]})
        main = props_main_lines(props.get(gid))
        nh = len(inp["home"]["players"])
        for s, side in (("home", h), ("away", a)):
            real = {p["id"]: p for p in side["players"] if not p.get("dnp") and p.get("MIN")}
            for i, pl in enumerate(inp[s]["players"]):
                j = i if s == "home" else nh + i
                act = real.get(pl["id"])
                on_b = rb[s]["MIN"][:, i] > 0
                on_p = rp["box"]["MIN"][:, j] > 0.5
                if not act or on_b.sum() < 200 or on_p.sum() < 100:
                    continue
                row = {"game": f'{a["abbr"]}@{h["abbr"]}', "name": pl["name"], "MIN_real": act["MIN"],
                       "MIN_box": float(rb[s]["MIN"][on_b, i].mean()), "MIN_poss": float(rp["box"]["MIN"][on_p, j].mean())}
                for stat, comps in STATS.items():
                    xb = sum(rb[s][c][:, i] for c in PROP_STATS[stat])          # moteur stat : REB direct
                    xp = sum(rp["box"][c][:, j].astype(float) for c in comps)   # moteur possession : OREB + DREB
                    row[stat] = (_actual(act, stat), float(xb[on_b].mean()), float(xp[on_p].mean()))
                players.append(row)
                for stat, comps in PROP_STATS.items():
                    m = main.get((pl["id"], stat))
                    if not m: continue
                    real_v = _actual(act, stat)
                    if real_v == m["line"]: continue
                    xb = sum(rb[s][c][:, i] for c in comps)[on_b]
                    pc = {"PTS": ("PTS",), "REB": ("OREB", "DREB"), "AST": ("AST",), "3PM": ("3PM",)}
                    xp = sum(rp["box"][cc][:, j].astype(float) for c in comps for cc in pc[c])[on_p]
                    prop_rows.append({"name": pl["name"], "stat": stat, "line": m["line"], "real": real_v,
                                      "over": real_v > m["line"], "p_mkt": m["p_over_novig"],
                                      "p_box": float((xb > m["line"]).mean()), "p_poss": float((xp > m["line"]).mean()),
                                      "price_o": m["over"], "price_u": m["under"]})
    return teams, players, prop_rows


def report(day, teams, players, props):
    print(f"\n=== {day} : {len(teams)} matchs, {len(players)} joueurs, {len(props)} props DraftKings ===")
    print("\n-- Equipes (score reel vs attendu ; ext - dom) --")
    print(f"  {'match':12s} {'reel':>9s} {'stat':>11s} {'possession':>11s} {'Vegas':>11s}  {'P(dom) stat/poss':>17s}")
    err = {"box": [], "poss": [], "vegas": []}
    for t in teams:
        r = t["real"]
        fmt = lambda x: f"{x[1]:5.1f}-{x[0]:<5.1f}" if x else "     -     "
        print(f"  {t['game']:12s} {r[1]:4d}-{r[0]:<4d} {fmt(t['box'])} {fmt(t['poss'])} {fmt(t['vegas'])}  "
              f"{100 * t['p_box']:5.1f}/{100 * t['p_poss']:4.1f} % {'✓' if (t['p_box'] > .5) == t['home_won'] else '✗'}")
        for k in err:
            if t[k]:
                err[k].append(abs(t[k][0] - r[0])); err[k].append(abs(t[k][1] - r[1]))
    print("  erreur moyenne par equipe : " + " · ".join(f"{k} {np.mean(v):.1f} pts" for k, v in err.items() if v))
    print("\n-- Joueurs (erreur absolue moyenne ; biais = projete - reel) --")
    print(f"  {'stat':4s} {'stat MAE':>9s} {'biais':>7s} {'poss MAE':>9s} {'biais':>7s}")
    for stat in ("MIN",) + tuple(STATS):
        if stat == "MIN":
            real = np.array([p["MIN_real"] for p in players]); b = np.array([p["MIN_box"] for p in players]); q = np.array([p["MIN_poss"] for p in players])
        else:
            real = np.array([p[stat][0] for p in players]); b = np.array([p[stat][1] for p in players]); q = np.array([p[stat][2] for p in players])
        print(f"  {stat:4s} {np.mean(np.abs(b - real)):9.2f} {np.mean(b - real):+7.2f} {np.mean(np.abs(q - real)):9.2f} {np.mean(q - real):+7.2f}")
    print("\n-- Plus gros ecarts (PTS, moteur stat) --")
    for p in sorted(players, key=lambda p: -abs(p["PTS"][1] - p["PTS"][0]))[:10]:
        print(f"  {p['game']:9s} {p['name'][:24]:24s} reel {p['PTS'][0]:3.0f} pts en {p['MIN_real']:2.0f} min | "
              f"projete {p['PTS'][1]:4.1f} en {p['MIN_box']:4.1f} min (possession {p['PTS'][2]:4.1f} en {p['MIN_poss']:4.1f})")
    if props:
        y = np.array([p["over"] for p in props], float)
        for key, lab in (("p_mkt", "marché"), ("p_box", "moteur stat"), ("p_poss", "moteur possession")):
            pr = np.array([p[key] for p in props])
            print(f"  log-loss {lab:18s} {np.mean([_ll(a, b) for a, b in zip(pr, y)]):.4f} | Brier {np.mean((pr - y) ** 2):.4f}")
        print(f"  overs passes : {100 * y.mean():.1f} % | P(over) moyenne marche {100 * np.mean([p['p_mkt'] for p in props]):.1f} %"
              f" | stat {100 * np.mean([p['p_box'] for p in props]):.1f} % | possession {100 * np.mean([p['p_poss'] for p in props]):.1f} %")
        for key, lab in (("p_box", "stat"), ("p_poss", "possession")):
            picks = [p for p in props if 0.04 <= abs(p[key] - p["p_mkt"]) < 0.20]
            wins = [(p[key] > p["p_mkt"]) == p["over"] for p in picks]
            print(f"  ecarts 4-20 pts ({lab}) : {sum(wins)}/{len(picks)} justes ({100 * np.mean(wins):.0f} %)" if picks else "")


if __name__ == "__main__":
    import json
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-10-26"
    t, p, pr = compare(day, params=json.loads(sys.argv[2]) if len(sys.argv) > 2 else None)
    report(day, t, p, pr)
