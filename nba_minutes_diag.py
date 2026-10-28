"""
nba_minutes_diag.py — D'ou viennent les erreurs de minutes du simulateur ?

Rejoue une saison match par match (etat du modele construit uniquement avec les matchs
precedents, effectif reel moins les blesses connus avant le match, comme en production) et
note, pour chaque joueur : minutes attendues par le simulateur, minutes jouees, et le contexte
du match tire du box-score et du play-by-play :
  - titulaire inattendu / titulaire habituel sur le banc ;
  - coequipiers de rotation absents par rapport au match precedent ;
  - ecart final, minutes jouees dans le « garbage time » (QT4 a 20 pts d'ecart ou plus) ;
  - fautes (5 ou 6), prolongation, sortie precoce (blessure en cours de match ?).

Sortie : data/nba_db/minutes_diag_<annee>.json + tableau de decomposition de l'erreur.
Usage : python nba_minutes_diag.py 2026 [--sims 400] [--max 300]
"""
import argparse, json, math, sys, time
from collections import defaultdict

import numpy as np

from nba_backtest import roster_for
from nba_boxdb import DB_DIR, load_plays, load_season
from nba_pbp_profiles import _game_seconds, T_SUB
from nba_sim import NBAState, simulate

GARBAGE = 20        # ecart a partir duquel le QT4 est du garbage time
ROT_MIN = 15.0      # joueur de rotation : 15+ min projetees


def _garbage_minutes(plays, box_game):
    """Minutes jouees par joueur en QT4 / prolongation alors que l'ecart est >= GARBAGE."""
    from nba_pbp_profiles import _on_court_minutes
    occ = _on_court_minutes(plays, box_game)
    # ecart a chaque minute du QT4 (dernier score connu)
    margin = np.full(48, np.nan)
    for p in plays:
        if p.get("period") != 4: continue
        t = _game_seconds(4, p.get("clock"))
        if t is None or p.get("home") is None: continue
        margin[min(int(t // 60), 47)] = abs(p["home"] - p["away"])
    last = 0.0
    for i in range(36, 48):
        if np.isnan(margin[i]): margin[i] = last
        last = margin[i]
    gmask = np.zeros(48, bool)
    gmask[36:] = margin[36:] >= GARBAGE
    return {pid: float((o & gmask).sum()) for pid, o in occ.items()}


def run(year, n_sims, max_games=None, params=None):
    prior, test = load_season(year - 1), load_season(year)
    plays = load_plays(year)
    games = [(gid, g, year - 1) for gid, g in prior.items()] + [(gid, g, year) for gid, g in test.items()]
    games.sort(key=lambda x: (x[1]["date"], x[0]))
    st = NBAState(params)
    last_rot = {}     # equipe -> {pid: minutes} du match precedent
    rows, n_done, t0 = [], 0, time.time()
    by_date = defaultdict(list)
    for x in games: by_date[x[1]["date"]].append(x)
    for d in sorted(by_date):
        for gid, g, season in by_date[d]:
            if season != year or not st.player_hist or "stats" not in g["home"]: continue
            if max_games and n_done >= max_games: break
            st.season = season
            h, a = g["home"], g["away"]
            inp = st.project(h["id"], a["id"], roster_for(h), roster_for(a), g.get("lines"))
            res = simulate(inp, n=n_sims, seed=int(gid) % (2 ** 31))
            margin = abs(h["pts"] - a["pts"])
            ot = len(h.get("quarters") or []) > 4
            gb = _garbage_minutes(plays[gid], g) if gid in plays else {}
            for s, side, opp in (("home", h, a), ("away", a, h)):
                act = {p["id"]: p for p in side.get("players") or []}
                prev = last_rot.get(side["id"], {})
                here = {pl["id"] for pl in inp[s]["players"]}
                absent_rot = sum(1 for pid, m in prev.items() if m >= ROT_MIN and pid not in here)
                for i, pl in enumerate(inp[s]["players"]):
                    p = act.get(pl["id"]) or {}
                    am = float(p.get("MIN") or 0) if not p.get("dnp") else 0.0
                    rows.append({
                        "gid": gid, "date": d, "team": side["id"], "opp": opp["id"], "pid": pl["id"], "name": pl["name"],
                        "pos": pl.get("pos", ""), "n_games": pl["n_games"],
                        "exp": float(res[s]["MIN"][:, i].mean()), "m_mu": pl["m_mu"], "p_play": pl["p_play"],
                        "sr": pl["starter_rate"], "act": am, "starter": bool(p.get("starter")),
                        "dnp_coach": bool(p.get("dnp")) and "COACH" in (p.get("reason") or "").upper(),
                        "pf": int(p.get("PF") or 0), "margin": margin, "ot": ot, "garbage": gb.get(pl["id"], 0.0),
                        "absent_rot": absent_rot, "win": (side["pts"] > opp["pts"]),
                    })
            n_done += 1
            if n_done % 200 == 0: print(f"  {n_done} matchs ({time.time() - t0:.0f}s)", flush=True)
        for gid, g, season in by_date[d]:
            st.add_game(gid, g, season)
            for side in ("home", "away"):
                if g[side].get("players"):
                    last_rot[g[side]["id"]] = {p["id"]: float(p.get("MIN") or 0) for p in g[side]["players"]
                                               if not p.get("dnp")}
    return rows


def report(rows):
    def mae(rs): return sum(abs(r["act"] - r["exp"]) for r in rs) / max(len(rs), 1)
    def bias(rs): return sum(r["act"] - r["exp"] for r in rs) / max(len(rs), 1)
    rel = [r for r in rows if max(r["exp"], r["act"]) >= 10]
    tot = sum(abs(r["act"] - r["exp"]) for r in rel)
    print(f"\nJoueurs pertinents (prevus ou joues 10+ min) : {len(rel)} · erreur moyenne {mae(rel):.2f} min · biais {bias(rel):+.2f}")
    cats = [
        ("Titulaire inattendu (sr < 0,5, a commence)", lambda r: r["starter"] and r["sr"] < 0.5),
        ("Titulaire habituel sur le banc (sr >= 0,5)", lambda r: not r["starter"] and r["sr"] >= 0.5 and r["act"] > 0),
        ("Choix de l'entraineur : n'a pas joue", lambda r: r["act"] == 0),
        ("Prolongation", lambda r: r["ot"]),
        ("Ecart final >= 20 pts", lambda r: r["margin"] >= 20),
        ("Garbage time joue >= 4 min", lambda r: r["garbage"] >= 4),
        ("5-6 fautes", lambda r: r["pf"] >= 5),
        ("Sortie precoce (< 50 % prevu, a joue)", lambda r: 0 < r["act"] < 0.5 * r["exp"]),
        ("1+ joueur de rotation absent vs match precedent", lambda r: r["absent_rot"] >= 1),
        ("2+ joueurs de rotation absents", lambda r: r["absent_rot"] >= 2),
        ("Debut de saison (< 10 matchs du joueur)", lambda r: r["n_games"] < 10),
    ]
    print(f"{'Contexte':52s} {'part':>6s} {'n':>6s} {'MAE':>6s} {'biais':>7s} {'% err':>6s}")
    for lab, f in cats:
        sub = [r for r in rel if f(r)]
        if not sub: continue
        print(f"{lab:52s} {100 * len(sub) / len(rel):5.1f}% {len(sub):6d} {mae(sub):6.2f} {bias(sub):+7.2f} "
              f"{100 * sum(abs(r['act'] - r['exp']) for r in sub) / tot:5.1f}%")
    rest = [r for r in rel if not any(f(r) for _, f in cats)]
    print(f"{'Aucun de ces contextes':52s} {100 * len(rest) / len(rel):5.1f}% {len(rest):6d} {mae(rest):6.2f} {bias(rest):+7.2f} "
          f"{100 * sum(abs(r['act'] - r['exp']) for r in rest) / tot:5.1f}%")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("year", type=int)
    ap.add_argument("--sims", type=int, default=400)
    ap.add_argument("--max", type=int)
    ap.add_argument("--set", action="append", default=[], help="parametre nba_sim, ex. min_long_w=0.3")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    params = {k: float(v) for k, v in (x.split("=") for x in a.set)}
    rows = run(a.year, a.sims, a.max, params or None)
    (DB_DIR / f"minutes_diag_{a.year}{a.tag}.json").write_text(json.dumps(rows), encoding="utf-8")
    report(rows)
