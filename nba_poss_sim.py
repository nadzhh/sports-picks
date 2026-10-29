"""
nba_poss_sim.py — Simulation NBA possession par possession (N matchs en parallele, numpy).

Reprend les entrees calibrees de nba_sim (NBAState.project : taux joueurs retrecis,
minutes, absents, notes equipe, cible de points ancree sur Vegas) et les complete
avec les profils play-by-play (nba_pbp_profiles : zones de tir, reussite par zone,
passes, rotations minute par minute, defense par zone).

Chaque possession : qui est sur le terrain (programme de rotation par simulation),
duree (contexte : transition apres interception / rebond defensif, seconde chance),
issue : perte de balle (interception ?), faute avec lancers, ou tir (tireur selon
l'usage, zone selon son profil, reussite selon sa zone x defense adverse, contre,
and-one, passe decisive, rebond offensif/defensif). Fautes d'equipe et bonus,
fautes volontaires en fin de match, prolongations.

Toutes les actions de toutes les simulations sont journalisees pour pouvoir rejouer
n'importe quel match a l'identique (nba_replay.py en tire le « match type »).
"""
import math

import numpy as np

from nba_pbp_profiles import ZONES
from nba_sim import _draw_minutes, _side_arrays

Z = len(ZONES)
ZI = {z: i for i, z in enumerate(ZONES)}
IS3 = np.array([z in ("C3", "AB3") for z in ZONES])

# Issues d'une etape (une possession ou une seconde chance)
K_TO, K_FT, K_SHOT, K_IFOUL, K_NONE = 0, 1, 2, 3, 4
CTX_DEAD, CTX_DREB, CTX_STEAL, CTX_OREB = 0, 1, 2, 3
DUR_MEAN = np.array([15.5, 12.5, 7.5, 8.0])   # secondes par contexte (avant calage sur le rythme)
P = {
    "steal_share": 0.52,      # part des pertes de balle sur interception
    "ns_foul": 0.10,          # faute defensive non-tir par possession (0,075 -> 16-18 fautes/equipe, reel ~20)
    "off_foul": 0.35,         # part des pertes de balle sans interception qui sont des fautes offensives
    "ft3_share": 0.07,        # part des fautes sur tir a 3 pts
    "orb_ft": 0.13,           # rebond offensif apres lancer rate
    "trans_bonus": 1.10,      # reussite en transition apres interception
    "heave_pct": 0.04,
    "foul_coach": 0.8,        # part des simulations ou l'entraineur sort un joueur charge de fautes
    "score_k": 0.006,         # effet d'ecart : -0,6 % d'adresse par point d'avance au-dela de 10 (2e mi-temps)
    "score_cap": 0.12,
    "garbage_margin": 20,     # QT4, 5 dernieres minutes : remplacants si l'ecart atteint 20 pts
    "pace_sd": 0.02,         # rythme commun aux deux equipes, propre a chaque match
    "to_factor": 0.9,
}


def _choose(rng, w):
    """Indice tire selon les poids w (N, k) -> (N,)."""
    c = np.cumsum(w, axis=1)
    tot = c[:, -1:]
    u = rng.random((w.shape[0], 1)) * np.where(tot > 0, tot, 1)
    idx = (u > c).sum(axis=1)
    return np.minimum(idx, w.shape[1] - 1)


class Team:
    """Caracteristiques d'une equipe pour le moteur."""

    def __init__(self, side_inp, profiles, opp_id, league_orb):
        ps = side_inp["players"]
        self.ids = [p["id"] for p in ps]
        self.names = [p["name"] for p in ps]
        self.n = len(ps)
        A = _side_arrays(side_inp)
        self.A = A
        lg_pct = profiles["league"]
        lg_mix = np.array([profiles["league_mix"][z] for z in ZONES])
        mix, pct, ast = np.zeros((self.n, Z)), np.zeros((self.n, Z)), np.zeros((self.n, Z))
        occ = np.zeros((self.n, 48))
        for i, p in enumerate(ps):
            pr = profiles["players"].get(p["id"])
            share3 = p["r3"] / max(p["r2"] + p["r3"], 1e-6)
            base = lg_mix.copy()
            base[IS3] *= share3 / max(base[IS3].sum(), 1e-6)
            base[~IS3] *= (1 - share3) / max(base[~IS3].sum(), 1e-6)
            if pr:
                w = min(pr["fga"] / 40.0, 1.0)   # confiance dans le profil de zones
                m = np.array([pr["zones"][z]["w"] for z in ZONES])
                mix[i] = w * m + (1 - w) * base
                pct[i] = [pr["zones"][z]["pct"] for z in ZONES]
                ast[i] = [pr["zones"][z]["ast"] for z in ZONES]
                if pr.get("occ") is not None and pr["n_games"] >= 3:
                    occ[i] = pr["occ"]
            else:
                mix[i] = base
                pct[i] = [lg_pct[z] for z in ZONES]
                ast[i] = [0.55, 0.45, 0.4, 0.85, 0.7]
            if not occ[i].any():
                occ[i] = profiles["starter_tpl"] if p.get("starter_rate", 0) >= 0.5 else profiles["bench_tpl"]
            # Coherence avec la reussite box-score (2PT/3PT retrecis) du joueur
            m2 = mix[i][~IS3] / max(mix[i][~IS3].sum(), 1e-6)
            m3 = mix[i][IS3] / max(mix[i][IS3].sum(), 1e-6)
            pct[i][~IS3] *= p["p2"] / max(np.dot(m2, pct[i][~IS3]), 1e-6)
            pct[i][IS3] *= p["p3"] / max(np.dot(m3, pct[i][IS3]), 1e-6)
        self.mix = mix / mix.sum(axis=1, keepdims=True)
        self.pct = np.clip(pct, 0.02, 0.9)
        self.ast = np.clip(ast, 0.05, 0.98)
        self.occ = np.clip(occ, 0.02, 1.0)
        self.def_zone = np.array([profiles["team_def"].get(opp_id, {}).get(z, 1.0) for z in ZONES])
        vol = side_inp["vol"]
        self.p_to = vol["vto"]
        self.p_fta = vol["vft"]
        self.orb = side_inp["orb"]
        self.scale = 1.0


def rotation(rng, team, n, stay_bonus=0.25):
    """Programme de rotation (n, 48, 5) : 5 joueurs par minute et par simulation.
    Minutes cibles tirees comme dans nba_sim (absences, p_play). Chaque minute, les 5
    joueurs les plus « en retard » entrent : urgence = minutes restant a jouer /
    occupation habituelle restante x occupation habituelle a cette minute (profil
    play-by-play), avec un bonus pour qui est deja sur le terrain (pas de va-et-vient)."""
    M = _draw_minutes(rng, team.A, n, 240.0, np.ones(n))                  # (n, P)
    occ = team.occ * rng.uniform(0.85, 1.15, size=(n, team.n, 1))         # variete entre simulations
    tail = np.cumsum(occ[:, :, ::-1], axis=2)[:, :, ::-1]                 # occupation restante a partir de t
    R = M.copy()
    on = np.zeros((n, team.n), bool)
    lineup = np.zeros((n, 48, 5), int)
    for t in range(48):
        urg = R / np.maximum(tail[:, :, t], 1e-6) * occ[:, :, t]
        urg = urg + stay_bonus * on * (R > 0.5)
        urg[R <= 0.25] = -1
        top = np.argpartition(-urg, 4, axis=1)[:, :5]
        lineup[:, t] = top
        on[:] = False
        np.put_along_axis(on, top, True, axis=1)
        R -= on
    return np.sort(lineup, axis=2), M


def simulate(inp, profiles, n=5000, seed=0, log=True, calibrate=True):
    rng = np.random.default_rng(seed)
    lg_orb = inp.get("dreb_credit", 0.95)
    teams = [Team(inp["home"], profiles, inp["away"]["id"], lg_orb), Team(inp["away"], profiles, inp["home"]["id"], lg_orb)]
    for t in teams:
        t.usage_pow = inp["params"].get("usage_pow", 1.0)
        t.ast_pow = inp["params"].get("ast_pow", 1.0)
    targets = np.array([inp["pts_h"], inp["pts_a"]])
    pace = inp["pace"]
    dur_scale = 1.0
    if calibrate:
        # Calage en deux temps : les durees de possession sur le rythme cible, puis
        # l'adresse de chaque equipe sur ses points cibles (ancres sur Vegas)
        for _ in range(2):
            r = _run(rng, teams, profiles, 500, dur_scale, log=False)
            dur_scale *= (r["n_poss"].mean() / 2.0) / pace
        for _ in range(3):
            r = _run(rng, teams, profiles, 600, dur_scale, log=False)
            pts = r["score"].mean(axis=0)
            for k in range(2):
                teams[k].scale *= (targets[k] / max(pts[k], 1)) ** 0.9
    rng = np.random.default_rng(seed + 1)
    res = _run(rng, teams, profiles, n, dur_scale, log=log)
    res["teams"] = teams
    res["dur_scale"] = dur_scale
    return res


def _run(rng, teams, profiles, n, dur_scale, log=True):
    H, Aw = teams
    nh, na = H.n, Aw.n
    (lu0, M0), (lu1, M1) = rotation(rng, H, n), rotation(rng, Aw, n)
    lineups = [lu0, lu1 + nh]                                            # indices globaux
    # Remplacants possibles (fautes) : joueurs avec des minutes dans cette simulation, poids = leur
    # occupation habituelle a chaque minute
    avail = np.concatenate([M0, M1], axis=1) > 0.5                       # (n, Pt)
    occ_all = np.concatenate([H.occ, Aw.occ])                            # (Pt, 48)
    coach = rng.random((n, 2)) < P["foul_coach"]
    Pt = nh + na
    # « Garbage time » : les 5 joueurs aux plus petites minutes (non nulles) de chaque simulation
    garbage = []
    for k, lu in enumerate(lineups):
        mins = np.zeros((n, Pt))
        for m in range(48):
            np.add.at(mins, (np.repeat(np.arange(n), 5), lu[:, m, :].ravel()), 1.0)
        own = (np.arange(Pt) >= nh) if k else (np.arange(Pt) < nh)
        key = np.where(own[None, :] & (mins > 0), mins, np.inf)
        key = np.where(np.isinf(key) & own[None, :], 1e6, key)
        garbage.append(np.sort(np.argsort(key, axis=1)[:, :5], axis=1))
    pace_mult = np.exp(rng.normal(0, P["pace_sd"], n))

    def g(attr):
        return np.concatenate([getattr(H, "A")[attr], getattr(Aw, "A")[attr]])
    # Concentration de l'usage (meme parametre que nba_sim) : sans elle, les stars recoivent
    # trop peu de tirs et de lancers, et les fins de banc trop
    gu, ga = H.usage_pow, H.ast_pow
    r_shot = (g("r2") + g("r3")) ** gu
    r_to, r_ft, r_stl, r_blk, r_pf = g("rto"), g("rft") ** gu, g("rstl"), g("rblk"), g("rpf")
    s_or, s_dr, s_ast, pft = g("s_or"), g("s_dr"), g("s_ast") ** ga, g("pft")
    mix = np.concatenate([H.mix, Aw.mix])
    pct = np.concatenate([H.pct, Aw.pct])
    astp = np.concatenate([H.ast, Aw.ast])
    team_of = np.r_[np.zeros(nh, int), np.ones(na, int)]
    def_zone = np.stack([Aw.def_zone, H.def_zone])     # facteur subi par l'attaque k
    scale = np.array([H.scale, Aw.scale])
    p_to = np.array([H.p_to, Aw.p_to])
    p_fta = np.array([H.p_fta, Aw.p_fta])
    orb = np.array([H.orb, Aw.orb])
    and1 = np.array([profiles["and_one"][z] for z in ZONES])
    blk_base = np.array([profiles["block_rate"][z] for z in ZONES])
    mean_blk = max(r_blk.mean() * 5, 1e-6)

    idx = np.arange(n)
    t = np.zeros(n)                          # secondes ecoulees
    period = np.ones(n, int)
    p_end = np.full(n, 720.0)
    tip = (rng.random(n) < 0.5).astype(int)  # equipe qui gagne l'entre-deux
    off = tip.copy()
    ctx = np.zeros(n, int)
    score = np.zeros((n, 2), int)
    tfouls = np.zeros((n, 2), int)
    done = np.zeros(n, bool)
    n_poss = np.zeros(n, int)
    box = {k: np.zeros((n, Pt), np.int16) for k in
           ("PTS", "FGM", "FGA", "3PM", "3PA", "FTM", "FTA", "OREB", "DREB", "AST", "STL", "BLK", "TO", "PF")}
    logs = []
    ot_lineup_min = 47
    MIN = np.zeros((n, Pt), np.float32)

    def add(key, who, val, mask):
        np.add.at(box[key], (idx[mask], who[mask]), val if np.isscalar(val) else val[mask])

    for _step in range(600):
        act = ~done
        if not act.any(): break
        minute = np.minimum((t // 60).astype(int), ot_lineup_min)
        lu_h = lineups[0][idx, minute]                       # (n, 5)
        lu_a = lineups[1][idx, minute]
        blowout = (period == 4) & (p_end - t <= 300) & (np.abs(score[:, 0] - score[:, 1]) >= P["garbage_margin"])
        lu_h = np.where(blowout[:, None], garbage[0], lu_h)
        lu_a = np.where(blowout[:, None], garbage[1], lu_a)
        # Fautes : 6 = exclu ; charge de fautes (2 au 1er QT, 3 au 2e, 4 au 3e, 5 avant les 5 dernieres
        # minutes) = sur le banc si l'entraineur est prudent
        pf = box["PF"]
        rem0 = p_end - t
        lim = np.select([period == 1, period == 2, period == 3, (period == 4) & (rem0 > 300)], [2, 3, 4, 5], 6)
        for k, lu in ((0, lu_h), (1, lu_a)):
            pfl = pf[idx[:, None], lu]
            bad = (pfl >= 6) | ((pfl >= lim[:, None]) & coach[:, k:k + 1] & ~blowout[:, None])
            if not bad.any(): continue
            own = (np.arange(Pt) >= nh) if k else (np.arange(Pt) < nh)
            ok = avail & own[None, :] & (pf < np.where(coach[:, k], lim, 6)[:, None]) & (pf < 6)
            w_all = occ_all[:, np.minimum(minute, 47)].T + 0.02                  # (n, Pt)
            for sl in range(5):
                need = bad[:, sl]
                if not need.any(): continue
                rows = idx[need]
                w = np.where(ok[rows], w_all[rows], 0.0)
                w[np.arange(len(rows))[:, None], lu[rows]] = 0.0                # deja sur le terrain
                has = w.sum(axis=1) > 0
                if not has.any(): continue
                pick = _choose(rng, w[has])
                lu[rows[has], sl] = pick
        lu_h, lu_a = np.sort(lu_h, axis=1), np.sort(lu_a, axis=1)
        lu_off = np.where(off[:, None] == 0, lu_h, lu_a)
        lu_def = np.where(off[:, None] == 0, lu_a, lu_h)
        rem = p_end - t
        margin_off = score[idx, off] - score[idx, 1 - off]
        # Fin de match : l'equipe menee (de 1 a 8 pts) fait faute pour arreter l'horloge
        late = (period >= 4) & (rem <= 50)
        ifoul = act & late & (margin_off >= 1) & (margin_off <= 8) & (rng.random(n) < 0.85)
        # Duree de la possession
        d = rng.gamma(4.0, DUR_MEAN[ctx] / 4.0) * dur_scale * pace_mult
        d = np.where(ifoul, rng.uniform(2, 6, n), np.maximum(d, 2.0))
        last = act & (d >= rem)                                   # derniere possession de la periode
        d = np.where(last, np.maximum(rem - rng.uniform(0, 1.5, n), 0.0), d)
        none = act & (rem <= 1.5)                                 # periode terminee sans action
        d = np.where(none, rem + 1e-3, d)
        live = act & ~none

        u = rng.random(n)
        kind = np.full(n, K_NONE)
        kind[live] = K_SHOT
        pto = p_to[off] * P["to_factor"]
        is_to = live & ~ifoul & (u < pto)
        is_ft = live & ~ifoul & ~is_to & (u < pto + p_fta[off] * 0.30)
        kind[is_to] = K_TO
        kind[is_ft] = K_FT
        kind[ifoul] = K_IFOUL

        w_on = lambda arr, lu: arr[lu]
        # Perte de balle
        to_who = lu_off[idx, _choose(rng, w_on(r_to, lu_off))]
        stl = is_to & (rng.random(n) < P["steal_share"])
        stl_who = lu_def[idx, _choose(rng, w_on(r_stl, lu_def) + 1e-4)]
        add("TO", to_who, 1, is_to)
        add("STL", stl_who, 1, stl)
        offf = is_to & ~stl & (rng.random(n) < P["off_foul"] * np.clip(r_pf[to_who] / 0.04, 0.5, 2.0))   # passage en force
        add("PF", to_who, 1, offf)

        # Faute non-tir (peut mettre en bonus)
        nsf = live & (kind == K_SHOT) & (rng.random(n) < P["ns_foul"])
        fouler = lu_def[idx, _choose(rng, w_on(r_pf, lu_def) + 0.004)]   # selon le vrai taux de fautes
        add("PF", fouler, 1, nsf)
        tfouls[idx[nsf], 1 - off[nsf]] += 1
        bonus_lim = np.where((rem <= 120) & (period >= 1), 2, 5)
        nsf_bonus = nsf & (tfouls[idx, 1 - off] >= bonus_lim)

        # Lancers francs (faute sur tir, bonus, faute volontaire)
        ft_who = lu_off[idx, _choose(rng, w_on(r_ft, lu_off) + 1e-4)]
        best_ft = lu_off[idx, np.argmax(w_on(pft, lu_off), axis=1)]
        ft_who = np.where(ifoul, best_ft, ft_who)
        n_ft = np.zeros(n, int)
        n_ft[is_ft] = np.where(rng.random(is_ft.sum()) < P["ft3_share"], 3, 2)
        n_ft[ifoul | nsf_bonus] = 2
        shooting = (kind == K_SHOT) & ~nsf_bonus
        foul_on_ft = (is_ft | ifoul | nsf_bonus) & ~nsf
        add("PF", fouler, 1, foul_on_ft)
        tfouls[idx[is_ft | ifoul], 1 - off[is_ft | ifoul]] += 1

        # Tir
        shooter = lu_off[idx, _choose(rng, w_on(r_shot, lu_off))]
        zmix = mix[shooter].copy()
        trail_late = late & (margin_off < 0) & (margin_off >= -9)
        zmix[trail_late] *= np.where(IS3, 2.2, 1.0)
        heave = shooting & last & (rem <= 3.0)
        zone = _choose(rng, zmix)
        zone = np.where(heave, ZI["AB3"], zone)
        pmake = pct[shooter, zone] * def_zone[off, zone] * scale[off]
        pmake = np.where(ctx == CTX_STEAL, pmake * P["trans_bonus"], pmake)
        # Effets d'ecart (2e mi-temps) : l'equipe largement devant se relache, la menee force
        lead = np.clip((np.abs(margin_off) - 10) * P["score_k"], 0, P["score_cap"]) * (period >= 3)
        pmake = pmake * (1 - np.sign(margin_off) * lead)
        pmake = np.where(heave, P["heave_pct"], np.clip(pmake, 0.02, 0.92))
        made = shooting & (rng.random(n) < pmake)
        miss = shooting & ~made
        val = np.where(IS3[zone], 3, 2)
        blk_p = blk_base[zone] * (w_on(r_blk, lu_def).sum(axis=1) / mean_blk)
        blocked = miss & (rng.random(n) < np.clip(blk_p, 0, 0.6))
        blocker = lu_def[idx, _choose(rng, w_on(r_blk, lu_def) + 1e-4)]
        and_one = made & (rng.random(n) < and1[zone])
        n_ft = np.where(and_one, 1, n_ft)
        assisted = made & (rng.random(n) < astp[shooter, zone])
        mates = w_on(s_ast, lu_off) * (lu_off != shooter[:, None])
        assister = lu_off[idx, _choose(rng, mates + 1e-6)]
        add("FGA", shooter, 1, shooting)
        add("FGM", shooter, 1, made)
        add("3PA", shooter, 1, shooting & IS3[zone])
        add("3PM", shooter, 1, made & IS3[zone])
        add("PTS", shooter, val, made)
        add("AST", assister, 1, assisted)
        add("BLK", blocker, 1, blocked)
        add("PF", fouler, 1, and_one)
        score[idx[made], off[made]] += val[made]

        # Lancers : resultat lancer par lancer (jusqu'a 3)
        ft_shooter = np.where(and_one, shooter, ft_who)
        ft_made = np.zeros(n, int)
        last_ft_miss = np.zeros(n, bool)
        for j in range(3):
            take = n_ft > j
            ok = take & (rng.random(n) < np.clip(pft[ft_shooter] * 1.0, 0.3, 0.97))
            ft_made += ok
            last_ft_miss = np.where(take, ~ok, last_ft_miss)
        has_ft = n_ft > 0
        add("FTA", ft_shooter, n_ft, has_ft)
        add("FTM", ft_shooter, ft_made, has_ft)
        add("PTS", ft_shooter, ft_made, has_ft)
        score[idx[has_ft], off[has_ft]] += ft_made[has_ft]

        # Rebonds : tir rate (hors contre hors-jeu) ou dernier lancer rate
        reb_shot = miss
        reb_ft = has_ft & last_ft_miss & ~reb_shot
        need_reb = reb_shot | reb_ft
        p_or = np.where(reb_ft, P["orb_ft"], orb[off] * np.where(IS3[zone], 1.05, 1.0))
        oreb = need_reb & (rng.random(n) < p_or)
        dreb = need_reb & ~oreb
        reb_o_who = lu_off[idx, _choose(rng, w_on(s_or, lu_off) + 1e-4)]
        reb_d_who = lu_def[idx, _choose(rng, w_on(s_dr, lu_def) + 1e-4)]
        add("OREB", reb_o_who, 1, oreb)
        add("DREB", reb_d_who, 1, dreb)

        if log:
            logs.append({
                "t": t.astype(np.float32).copy(), "d": d.astype(np.float32), "per": period.astype(np.int8).copy(),
                "off": off.astype(np.int8).copy(), "kind": kind.astype(np.int8), "act": act.copy(),
                "ctx": ctx.astype(np.int8).copy(),
                "to": to_who.astype(np.int16), "stl": np.where(stl, stl_who, -1).astype(np.int16), "offf": offf,
                "nsf": np.where(nsf, fouler, -1).astype(np.int16), "fouler": fouler.astype(np.int16),
                "sh": shooter.astype(np.int16), "zone": zone.astype(np.int8), "made": made, "heave": heave,
                "blk": np.where(blocked, blocker, -1).astype(np.int16), "and1": and_one,
                "ast": np.where(assisted, assister, -1).astype(np.int16),
                "ftw": ft_shooter.astype(np.int16), "nft": n_ft.astype(np.int8), "ftm": ft_made.astype(np.int8),
                "oreb": np.where(oreb, reb_o_who, -1).astype(np.int16), "dreb": np.where(dreb, reb_d_who, -1).astype(np.int16),
                "sh_val": val.astype(np.int8), "shooting": shooting, "ifoul": ifoul,
                "sc": score.astype(np.int16).copy(),
                "lu_h": lu_h.astype(np.int16), "lu_a": lu_a.astype(np.int16),
            })

        # Minutes jouees : duree de l'etape pour les 10 joueurs sur le terrain
        for lu in (lu_h, lu_a):
            np.add.at(MIN, (np.repeat(idx[act], 5), lu[act].ravel()), np.repeat((d[act] / 60.0).astype(np.float32), 5))

        # Possession suivante
        n_poss += live & ~oreb
        t = np.where(act, t + d, t)
        new_off = np.where(oreb, off, 1 - off)
        off = np.where(live, new_off, off)
        ctx = np.where(oreb, CTX_OREB, np.where(stl, CTX_STEAL, np.where(dreb, CTX_DREB, CTX_DEAD)))

        # Fin de periode
        ended = act & (t >= p_end - 1e-6)
        if ended.any():
            tied = score[:, 0] == score[:, 1]
            finish = ended & (period >= 4) & ~tied
            done |= finish
            nxt = ended & ~finish
            period = np.where(nxt, period + 1, period)
            t = np.where(nxt, p_end, t)
            p_end = np.where(nxt, np.where(period <= 4, period * 720.0, 2880.0 + (period - 4) * 300.0), p_end)
            # QT2 et QT3 au perdant de l'entre-deux, QT4 au gagnant ; prolongation : entre-deux
            off = np.where(nxt & np.isin(period, (2, 3)), 1 - tip, off)
            off = np.where(nxt & (period == 4), tip, off)
            off = np.where(nxt & (period >= 5), (rng.random(n) < 0.5).astype(int), off)
            ctx = np.where(nxt, CTX_DEAD, ctx)
            tfouls[nxt] = 0

    n_ot = np.maximum(period - 4, 0)
    box["MIN"] = MIN
    out = {"score": score, "box": box, "n_poss": n_poss, "n_ot": n_ot, "lineups": lineups,
           "team_of": team_of, "n_home": nh}
    if log:
        out["log"] = {k: np.stack([s[k] for s in logs]) for k in logs[0]}   # (steps, n)
    return out


def pick_typical(res, inp, home_wins=None):
    """Le match « type » : score proche de la mediane, chaque joueur important proche de sa moyenne
    simulee. home_wins : None = vainqueur majoritaire ; True / False = le match type PARMI les
    simulations gagnees par cette equipe (scenario de chaque equipe, scores medians de ces simulations)."""
    sc = res["score"].astype(float)
    hw = sc[:, 0] > sc[:, 1]
    major = (hw.mean() >= 0.5) if home_wins is None else bool(home_wins)
    sub = sc[hw == major] if (hw == major).any() else sc
    med = np.median(sub, axis=0)
    z = ((sc[:, 0] - med[0]) / sc[:, 0].std()) ** 2 + ((sc[:, 1] - med[1]) / sc[:, 1].std()) ** 2
    # Candidats : le bon vainqueur, chaque score a +/- 3 pts de sa mediane (elargi si besoin)
    for tol in (3, 5, 8, 99):
        cand = (hw == major) & (np.abs(sc[:, 0] - med[0]) <= tol) & (np.abs(sc[:, 1] - med[1]) <= tol)
        if cand.sum() >= 20: break
    z = np.where(cand, z * 0.1, 1e6)
    z += np.where(res["n_ot"] > 0, 3, 0)
    box = res["box"]
    mins = box["MIN"].mean(axis=0)
    for j in np.nonzero(mins >= 15)[0]:
        for key in ("PTS", "REB", "AST", "MIN"):
            x = (box["OREB"][:, j] + box["DREB"][:, j]) if key == "REB" else box[key][:, j]
            x = x.astype(float)
            if x.std() > 0:
                z += ((x - x.mean()) / x.std()) ** 2 / 4
    return int(np.argmin(z))
