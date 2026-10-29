"""
foot_sim_run.py — Simule les matchs de foot a venir (Top 5 + coupes d'Europe) -> data/foot_sim.json.

Pour chaque match des 48 prochaines heures :
  - composition : officielle si publiee (~1 h avant), sinon probable (FotMob), sinon la notre (dernier
    onze de l'equipe moins les indisponibles) ; indisponibles et impact chiffre de chaque absence ;
  - 10 000 simulations minute par minute (foot_sim) : 1X2, double chance, buts, les deux marquent,
    scores exacts, mi-temps, buteurs, cartons rouges ;
  - « match type » rejoue par la video du site : le scenario le plus representatif et ses actions.
Pas de lien avec les paris ni la bankroll.

Usage : python foot_sim_run.py [--sims 10000] [--hours 48] [--no-update]
"""
import argparse, json, sys, time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import foot_db
from foot_sim import FootState, simulate, _ord, pos_group

OUT_PATH = Path("data/foot_sim.json")
CUR_SEASON = "2026/2027"
LOG_SIMS = 3000
ESPN_SLUG = {47: "eng.1", 87: "esp.1", 54: "ger.1", 55: "ita.1", 53: "fra.1", 42: "uefa.champions", 73: "uefa.europa"}
_ESPN_CACHE = {}


def espn_event(g):
    """Identifiant ESPN du match (scores en direct dans le navigateur) : meme jour, noms d'equipes proches."""
    import difflib
    from nba_boxdb import _get
    slug = ESPN_SLUG.get(g["league"])
    day = g["ko"][:10].replace("-", "")
    key = (slug, day)
    if key not in _ESPN_CACHE:
        _ESPN_CACHE[key] = (_get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={day}") or {}).get("events") or []
    sim = lambda a, b: difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    best, score = None, 0.0
    for e in _ESPN_CACHE[key]:
        teams = {c.get("homeAway"): (c.get("team") or {}).get("displayName", "") for c in (e.get("competitions") or [{}])[0].get("competitors") or []}
        sc = sim(teams.get("home", ""), g["home"]["name"]) + sim(teams.get("away", ""), g["away"]["name"])
        if sc > score: best, score = e, sc
    return {"slug": slug, "id": str(best["id"])} if best and score >= 1.0 else None
LINEUP_TYPES = {"standard": "officielle", "simple": "officielle", "predicted": "probable",
                "laststarting11": "dernier onze"}


def build_state(params=None):
    st = FootState(params)
    for r in sorted(foot_db.load_all().values(), key=lambda r: (r["date"], r["id"])):
        st.add_match(r)
    return st


def upcoming(hours):
    now = datetime.now(timezone.utc)
    out = []
    for lid in foot_db.LEAGUES:
        for m in foot_db.fixtures(lid, CUR_SEASON):
            stt = m.get("status") or {}
            if stt.get("started") or stt.get("finished") or stt.get("cancelled"): continue
            t = stt.get("utcTime")
            if not t: continue
            ko = datetime.fromisoformat(t.replace("Z", "+00:00"))
            if timedelta(0) < ko - now <= timedelta(hours=hours):
                out.append({"id": str(m["id"]), "league": lid, "ko": t, "round": m.get("round"),
                            "home": {"id": str(m["home"]["id"]), "name": m["home"]["name"]},
                            "away": {"id": str(m["away"]["id"]), "name": m["away"]["name"]}})
    return sorted(out, key=lambda g: g["ko"])


def lineups(st, g, day):
    """Composition et indisponibles de chaque equipe : {side: {xi, bench, type, unavailable}}."""
    try:
        pp = foot_db._page(g["id"]) or {}
    except Exception:
        pp = {}
    lu = (pp.get("content") or {}).get("lineup") or {}
    ltype = (lu.get("lineupType") or "").lower()
    out = {}
    for side, key in (("home", "homeTeam"), ("away", "awayTeam")):
        t = lu.get(key) or {}
        unav = [{"id": str(u.get("id")), "name": u.get("name"), "type": (u.get("unavailability") or {}).get("type"),
                 "back": (u.get("unavailability") or {}).get("expectedReturn")} for u in t.get("unavailable") or []]
        xi = [str(p.get("id")) for p in t.get("starters") or []]
        bench = [str(p.get("id")) for p in t.get("subs") or []]
        # noms et postes depuis FotMob : joueurs encore absents de la base (recrues, promus)
        names = {str(p.get("id")): (p.get("name"), p.get("positionId") or p.get("usualPlayingPositionId"))
                 for p in (t.get("starters") or []) + (t.get("subs") or [])}
        # Video : numero de maillot et place dans le schema (horizontalLayout : x = profondeur depuis son but,
        # y = largeur, de 0 a 1)
        info = {str(p.get("id")): {"num": str(p.get("shirtNumber") or ""),
                                   "lay": [(p.get("horizontalLayout") or {}).get("x"), (p.get("horizontalLayout") or {}).get("y")]}
                for p in (t.get("starters") or []) + (t.get("subs") or [])}
        typ = (LINEUP_TYPES.get(ltype, "probable") if xi else "")
        if not xi:   # notre composition : dernier onze de l'equipe, indisponibles retires
            tid = g[side]["id"]
            games = [x for x in st.team_games.get(tid, []) if x[0] < day]
            out_ids = {u["id"] for u in unav}
            if games:
                last_xi, last_bench = games[-1][2]
                xi = [p for p in last_xi if p not in out_ids]
                pool = Counter(pid for gm in games[-6:] for pid in gm[2][0] + gm[2][1])
                bench = [p for p, _ in pool.most_common(30) if p not in xi and p not in out_ids][:12]
                typ = "estimée"
        out[side] = {"xi": xi, "bench": bench, "type": typ, "unavailable": unav, "names": names, "info": info,
                     "formation": t.get("formation")}
    return out


def absence_impact(st, tid, unav, day, players_here):
    """Part de l'attaque habituelle perdue par absent (sa part d'xG x ses minutes habituelles)."""
    ref = st.typical_attack(tid, day) or 1.0
    out = []
    for u in unav:
        pr = st.player_profile(u["id"], day, tid)
        if not pr["n"]: continue
        usual = pr["share"] * pr["min_start"] * pr["start_rate"] / 90
        if usual / ref >= 0.02:
            out.append({**u, "impact": round(usual / ref, 3), "pos": pr["pos"]})
    return sorted(out, key=lambda x: -x["impact"])


def typical_index(res, gid=None):
    """Le match montre : resultat (1 / N / 2) tire au sort selon les probabilites des simulations (comme au
    basket : l'outsider apparait gagnant aussi souvent qu'il gagne ; tirage fixe par match), puis un score
    representatif de ce resultat (frequent ET proche des buts attendus), puis le scenario dont l'xG est le
    plus proche de la moyenne (sans rouge de preference). Sans gid : le resultat le plus probable."""
    H, A = res["home_goals"], res["away_goals"]
    sign = np.sign(H - A)
    if gid is None:
        k = max((1, 0, -1), key=lambda v: np.mean(sign == v))
    else:
        u = (int(gid) * 2654435761 % 2 ** 32) / 2 ** 32
        p1, px = float(np.mean(sign == 1)), float(np.mean(sign == 0))
        k = 1 if u < p1 else (0 if u < p1 + px else -1)
    # Score representatif : frequence x proximite des buts attendus. Le score le plus frequent seul donnait
    # presque toujours 1-0 (le mode d'une loi de Poisson est bas) meme pour 2,5-3 buts attendus (28/10).
    eh, ea = H.mean(), A.mean()
    cnt = Counter(zip(H[sign == k].tolist(), A[sign == k].tolist()))
    best = max(cnt, key=lambda sc: cnt[sc] * np.exp(-((sc[0] - eh) ** 2 + (sc[1] - ea) ** 2)))
    h, a = H[:LOG_SIMS], A[:LOG_SIMS]
    cand = np.nonzero((h == best[0]) & (a == best[1]))[0]
    if not len(cand): cand = np.nonzero(np.sign(h - a) == k)[0]
    z = ((res["home_xg"][cand] - res["home_xg"].mean()) ** 2 + (res["away_xg"][cand] - res["away_xg"].mean()) ** 2
         + 0.3 * (res["home_red"][cand] + res["away_red"][cand]))
    return int(cand[int(np.argmin(z))])


def replay(res, inp, k, g, lu=None):
    """Actions du match type pour la video : tirs (position, xG, issue), buts, penalties, csc, rouges, remplacements,
    et les joueurs (numero, poste, place dans le schema) pour les montrer tous sur le terrain."""
    ev = []
    for minute, side, j, q, goal, kind in sorted(res["log"][k], key=lambda e: e[0]):
        pl = inp[side]["players"]
        who = pl[j]["name"] if 0 <= j < len(pl) else ""
        if kind == "jeu":
            x, y, xg, sit, typ, ontg, blk, outc = res["pool"][side][q]
            ev.append({"t": minute, "side": side, "kind": "but" if goal else ("arret" if ontg else ("contre" if blk else "rate")),
                       "who": who, "x": round(x, 1), "y": round(y, 1), "xg": round(xg, 3), "sit": sit, "type": typ})
        elif kind == "penalty":
            ev.append({"t": minute, "side": side, "kind": "but" if goal else "arret", "who": who, "x": 94.5, "y": 34.0,
                       "xg": 0.76, "sit": "Penalty", "type": ""})
        elif kind == "csc":
            ev.append({"t": minute, "side": side, "kind": "csc", "who": "", "x": 104, "y": 34, "xg": 0, "sit": "", "type": ""})
        elif kind == "rouge":
            ev.append({"t": minute, "side": side, "kind": "rouge", "who": ""})
    for side in ("home", "away"):
        t_in, t_out = res["subs"][side]
        pl = inp[side]["players"]
        outs = [(float(t_out[k, j]), j) for j in range(len(pl)) if np.isfinite(t_out[k, j])]
        ins = [(float(t_in[k, j]), j) for j in range(len(pl)) if np.isfinite(t_in[k, j]) and t_in[k, j] > 0]
        for (tm, jo), (_, ji) in zip(sorted(outs), sorted(ins)):
            ev.append({"t": int(tm), "side": side, "kind": "remplacement", "who": pl[ji]["name"], "out": pl[jo]["name"]})
    ev.sort(key=lambda e: e["t"])
    score = [0, 0]
    for e in ev:
        if e["kind"] in ("but", "csc"):
            score[0 if e["side"] == "home" else 1] += 1
        e["score"] = list(score)
    teams = {}
    for side in ("home", "away"):
        info = ((lu or {}).get(side) or {}).get("info") or {}
        pls = []
        for q in inp[side]["players"]:
            i = info.get(q["id"]) or {}
            lay = i.get("lay") or [None, None]
            pls.append({"name": q["name"], "num": i.get("num", ""), "pos": q["pos"], "st": bool(q["starter"]),
                        "lay": [round(lay[0], 3), round(lay[1], 3)] if q["starter"] and None not in lay else None})
        teams[side] = {"players": pls, "formation": ((lu or {}).get(side) or {}).get("formation")}
    xh, xa = float(res["home_xg"].mean()), float(res["away_xg"].mean())
    poss = round(min(max(0.5 + 0.3 * (xh - xa) / max(xh + xa, 0.1), 0.35), 0.65), 3)   # possession du domicile (approx.)
    return {"v": 2, "events": ev, "final": score, "teams": teams, "poss": poss}


def run_game(st, g, n_sims):
    day = _ord(g["ko"])
    lu = lineups(st, g, day)
    for side in ("home", "away"):
        for pid, (name, pos) in lu[side]["names"].items():
            if name and not (st.player_info.get(pid) or {}).get("name"):
                st.player_info[pid] = {"name": name, "pos": pos_group(pos)}
    inp = st.project(g["home"]["id"], g["away"]["id"], g["league"], lu["home"], lu["away"], day,
                     neutral=g["league"] in (42, 73) and "final" in str(g.get("round", "")).lower())
    res = simulate(inp, n=n_sims, seed=int(g["id"]) % (2 ** 31), shots_db=st.player_shots, log_sims=LOG_SIMS)
    hg, ag = res["home_goals"], res["away_goals"]
    tot = hg + ag
    cs = Counter(zip(hg.tolist(), ag.tolist()))
    try:
        espn = espn_event(g)
    except Exception:
        espn = None
    out = {"id": g["id"], "league": g["league"], "league_name": foot_db.LEAGUES[g["league"]], "ko": g["ko"], "espn": espn,
           "home": g["home"], "away": g["away"], "n_sims": n_sims,
           "sim_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "xg": [round(float(res["home_xg"].mean()), 2), round(float(res["away_xg"].mean()), 2)],
           "goals": [round(float(hg.mean()), 2), round(float(ag.mean()), 2)],
           "p": {"1": round(float(np.mean(hg > ag)), 4), "X": round(float(np.mean(hg == ag)), 4), "2": round(float(np.mean(hg < ag)), 4),
                 "btts": round(float(np.mean((hg > 0) & (ag > 0))), 4),
                 "over": {str(l): round(float(np.mean(tot > l)), 4) for l in (0.5, 1.5, 2.5, 3.5, 4.5)},
                 "ht": {"1": round(float(np.mean(res["home_ht"] > res["away_ht"])), 4), "X": round(float(np.mean(res["home_ht"] == res["away_ht"])), 4),
                        "2": round(float(np.mean(res["home_ht"] < res["away_ht"])), 4)},
                 "red": round(float(np.mean((res["home_red"] + res["away_red"]) > 0)), 4),
                 "clean": [round(float(np.mean(ag == 0)), 4), round(float(np.mean(hg == 0)), 4)]},
           "scores": [[int(h), int(a), round(c / n_sims, 4)] for (h, a), c in cs.most_common(12)],
           "goal_hist": [round(float(np.mean(tot == k)), 4) for k in range(7)] + [round(float(np.mean(tot >= 7)), 4)]}
    for side in ("home", "away"):
        pl = inp[side]["players"]
        scorers = [{"name": q["name"], "pos": q["pos"], "starter": q["starter"],
                    "p_goal": round(float(np.mean(res[side + "_pgoals"][:, j] > 0)), 4),
                    "shots": round(float(res[side + "_pshots"][:, j].mean()), 2),
                    "sot": round(float(res[side + "_psot"][:, j].mean()), 2)} for j, q in enumerate(pl)]
        out[side + "_info"] = {"lineup": lu[side]["type"], "lineup_mult": round(inp[side]["lineup_mult"], 3),
                               "npxg_base": round(inp[side]["npxg_base"], 2), "npxg": round(inp[side]["npxg"], 2),
                               "absences": absence_impact(st, g[side]["id"], lu[side]["unavailable"], day, pl),
                               "scorers": sorted([s for s in scorers if s["p_goal"] >= 0.03], key=lambda s: -s["p_goal"])[:8],
                               "xi": [q["name"] for q in pl if q["starter"]]}
    k = typical_index(res, g["id"])
    out["replay"] = replay(res, inp, k, g, lu)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=10000)
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--no-update", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    if not a.no_update:
        foot_db.update(list(foot_db.LEAGUES), [CUR_SEASON])
    st = build_state()
    games = upcoming(a.hours)
    print(f"=== Simulation foot : {len(games)} matchs dans les {a.hours} h (etat en {time.time() - t0:.0f}s) ===", flush=True)
    out = []
    for g in games:
        try:
            r = run_game(st, g, a.sims)
        except Exception as e:
            print(f"  [err] {g['id']} {g['home']['name']} - {g['away']['name']} : {type(e).__name__}: {e}"); continue
        print(f"  {r['home']['name'][:18]:>18s} {r['xg'][0]:.2f} - {r['xg'][1]:.2f} {r['away']['name'][:18]:<18s} "
              f"1X2 {r['p']['1']:.2f}/{r['p']['X']:.2f}/{r['p']['2']:.2f}  +2,5 {r['p']['over']['2.5']:.2f}  "
              f"compo {r['home_info']['lineup'] or '?'}/{r['away_info']['lineup'] or '?'}  type {r['replay']['final']}", flush=True)
        out.append(r)
    OUT_PATH.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "games": out},
                                   ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {OUT_PATH} ({len(out)} matchs, {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
