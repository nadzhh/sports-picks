"""
foot_sim.py — Simulateur Monte-Carlo de matchs de foot, minute par minute (Top 5 + coupes d'Europe).

1. Niveau des equipes (FootState.team_model) : xG hors penalty attendu = base x domicile x attaque
   x faiblesse defensive adverse, poids decroissant avec l'anciennete. Les championnats ont chacun leur
   base ; en coupe d'Europe, un facteur par championnat (appris sur les matchs de coupe) les relie.
2. Joueurs (player_profile) : part de l'xG de l'equipe quand il est sur le terrain (retrait vers la
   moyenne du poste), minutes habituelles, banque de ses vrais tirs (position, xG, situation).
   Gestion des absences : l'attaque du soir = attaque habituelle x (somme des parts x minutes
   attendues de l'effectif disponible) ^ lineup_pow — les remplacants comptent avec leur propre part.
3. Simulation (simulate) : minute par minute, temps additionnel compris ; rythme selon la minute,
   effets du score, cartons rouges, penalties, buts contre son camp, remplacements ; chaque tir est
   un vrai tir passe du joueur qui tire (sa position et son xG) : buteurs et video coherents.

    st = FootState(); for rec in matchs_chronologiques: st.add_match(rec)
    inp = st.project(home_id, away_id, league, home_squad, away_squad, neutral=False)
    res = simulate(inp, n=10000)
"""
import math
from collections import defaultdict
from datetime import date

import numpy as np

P = {
    "hl_days":        200.0,  # demi-vie (jours) du poids d'un match dans le niveau des equipes
    "goal_w":         0.0,    # part des buts reels (hors penalty) melangee a l'xG dans le niveau des equipes
    "home_boost":     1.0,    # avantage du terrain en plus de celui mesure sur l'xG (1,04 teste : rejete)
    "k_team":         3.0,    # retrait vers la moyenne (en matchs equivalents)
    "k_cup":          12.0,   # retrait des facteurs de championnat (coupes d'Europe)
    "k_share":        900.0,  # minutes equivalentes du retrait de la part d'xG vers le poste
    "lineup_pow":     1.0,    # 0 = absences ignorees, 1 = attaque proportionnelle a l'effectif dispo
    # Dynamiques mesurees sur la base (Premier League 2024-25 et 2025-26, 580 matchs ; nba-style :
    # foot_calib.py du 28/10/2026)
    "finish":         0.97,   # buts / xG des tirs hors penalty
    "red_rate":       0.072,  # cartons rouges par equipe et par match
    "red_self":       0.70,   # xG/min de l'equipe a 10 apres un rouge (mesure 0,69 sur 40 matchs)
    "red_opp":        1.55,   # xG/min de l'adversaire apres un rouge (mesure 1,61)
    "og_rate":        0.043,  # buts contre son camp par equipe et par match
    "pen_rate":       0.11,   # penalties obtenus par equipe et par match (moyenne, module par l'attaque)
    "pen_conv":       0.79,
    "added1":         3.2,    # temps additionnel 1re / 2e periode, en minutes au rythme moyen
    "added2":         8.0,
    # Reglages valides le 28/10/2026 (foot_backtest, saison 2025-26, Premier League / Liga / Bundesliga /
    # Serie A, ~1 450 matchs vs cloture Pinnacle) : k_team 3 + intensity_sd 0,3 + rating_beta 0,3 ameliorent
    # le 1X2 dans les 4 championnats (1,0108 -> 1,0078 ; marche 0,978) et le +2,5 dans 3 sur 4 (0,6803 ->
    # 0,6794 ; marche 0,675) ; nuls 26,0 % simules / 25,7 % reels. Rejetes : home_boost 1,04, finish 0,94,
    # goal_w 0,3, hl_days 120 / 320.
    "intensity_sd":   0.3,    # intensite de match commune aux deux equipes (lognormale) : matchs fermes / ouverts
    "rating_beta":    0.3,    # qualite de l'onze (note FotMob moyenne vs onze habituel) : attaque x exp(b*d),
                              # xG concede x exp(-b*d) — capte la defense (gardien, charniere) absente des tirs
}
# Rythme : part relative de l'xG par tranche de 5 minutes (temps reglementaire), puis additionnel
TEMPO = np.array([0.87, 0.96, 0.98, 0.95, 0.98, 1.03, 1.07, 1.04, 0.97,
                  1.0, 1.06, 1.1, 1.1, 1.05, 0.98, 0.96, 0.95, 0.94])
# Effet du score sur le rythme de l'equipe (ecart de son point de vue, borne a +/-2), mesure A NIVEAU
# EGAL (xG reel / xG attendu des deux equipes) : menee +5-6 %, en tete d'un but -3,5 %. Sans ce controle,
# on lit l'inverse (les equipes qui menent sont les plus fortes). +2 : +11 % mesure = effet de selection
# (une equipe a 2-0 est dans un bon soir), garde neutre.
STATE = {-2: 1.05, -1: 1.06, 0: 1.0, 1: 0.97, 2: 1.0}


def pos_group(code):
    """Postes FotMob : titulaires (11 gardien, 3x defense, 5x-8x milieu, 9x+ attaque) et
    remplacants (0 gardien, 1 defense, 2 milieu, 3 attaque)."""
    if code is None: return "MID"
    if code in (0, 11): return "GK"
    if code == 1 or 30 <= code < 50: return "DEF"
    if code == 2 or 50 <= code < 90: return "MID"
    return "ATT"


def _ord(iso):
    return date.fromisoformat(iso[:10]).toordinal()


class FootState:
    def __init__(self, params=None):
        self.p = dict(P, **(params or {}))
        self.team_recs = []                       # (jour, equipe, adversaire, ligue, domicile, npxg pour, npxg contre)
        self.team_league = {}                     # equipe -> championnat national (le plus recent)
        self.player_hist = defaultdict(list)      # pid -> [(jour, equipe, minutes, titulaire, npxg, buts, poste)]
        self.player_shots = defaultdict(list)     # pid -> [(x, y, xg, situation, type, cadre, bloque)]
        self.team_pens = defaultdict(lambda: [0.0, 0])   # equipe -> [penalties obtenus, matchs]
        self.player_info = {}
        self.team_games = defaultdict(list)       # equipe -> [(jour, [(pid, minutes)])]
        self._cache = None

    def add_match(self, r):
        self._cache = None
        d, lid = _ord(r["date"]), r["league"]
        sides = {1: r["home"]["id"], 0: r["away"]["id"]}
        npxg = {1: 0.0, 0: 0.0}
        npg = {1: 0, 0: 0}
        pens = {1: 0, 0: 0}
        by_player = defaultdict(lambda: [0.0, 0])
        for s in r["shots"]:
            side, pid, xg, sit = s[0], s[1], s[6], s[9]
            if s[13]: continue                     # but contre son camp
            if sit == "Penalty":
                pens[side] += 1; continue
            npxg[side] += xg
            npg[side] += s[8] == "Goal"
            by_player[pid][0] += xg
            if s[8] == "Goal": by_player[pid][1] += 1
            self.player_shots[pid].append((s[4], s[5], xg, sit, s[10], s[11], s[12], s[8]))
            if len(self.player_shots[pid]) > 120: self.player_shots[pid] = self.player_shots[pid][-120:]
        for side in (1, 0):
            t, o = sides[side], sides[1 - side]
            self.team_recs.append((d, t, o, lid, side, npxg[side], npxg[1 - side], npg[side]))
            self.team_pens[t][0] += pens[side]; self.team_pens[t][1] += 1
            if lid not in (42, 73): self.team_league[t] = lid
        length = 120 if r.get("extra_time") else 90
        played = {1: [], 0: []}
        squad = {1: ([], []), 0: ([], [])}
        for p in r["players"]:
            pid, name, side, starter, pos, t_in, t_out = p[:7]
            rating = p[8] if len(p) > 8 else None
            self.player_info[pid] = {"name": name, "pos": pos_group(pos) if pos not in (None,) else "MID"}
            squad[side][0 if starter else 1].append(pid)
            if t_in is None: continue
            mins = max(min(t_out, length) - t_in, 0)
            xg, g = by_player.get(pid, (0.0, 0))
            self.player_hist[pid].append((d, sides[side], mins, starter, xg, g, pos_group(pos), npxg[side] * mins / length,
                                          t_out if t_out < length else None, rating if mins >= 20 else None))
            played[side].append((pid, mins * 90.0 / length))
        for side in (1, 0):
            self.team_games[sides[side]].append((d, played[side], squad[side]))

    # ── Niveau des equipes ──────────────────────────────────────────────────
    def team_model(self, day):
        if self._cache and self._cache[0] == day: return self._cache[1]
        recs = [r for r in self.team_recs if r[0] < day]
        if not recs: return None
        w = np.array([0.5 ** ((day - r[0]) / self.p["hl_days"]) for r in recs])
        gw = self.p.get("goal_w", 0.0)
        y = np.array([(1 - gw) * r[5] + gw * r[7] for r in recs])
        team = [r[1] for r in recs]; opp = [r[2] for r in recs]; home = np.array([r[4] for r in recs])
        lg = [r[3] for r in recs]
        cup = np.array([l in (42, 73) for l in lg])
        dom = {r[1]: self.team_league.get(r[1], -1) for r in recs}
        teams = sorted(set(team) | set(opp))
        ti = {t: i for i, t in enumerate(teams)}
        T, O = np.array([ti[t] for t in team]), np.array([ti[o] for o in opp])
        leagues = sorted(set(lg))
        li = {l: i for i, l in enumerate(leagues)}
        L = np.array([li[l] for l in lg])
        dl = sorted(set(dom.values()))
        di = {l: i for i, l in enumerate(dl)}
        DT = np.array([di[dom.get(t, -1)] for t in team]); DO = np.array([di[dom.get(o, -1)] for o in opp])
        base = np.array([np.average(y[L == i], weights=w[L == i]) for i in range(len(leagues))])
        a, dw = np.ones(len(teams)), np.ones(len(teams))
        qa, qd = np.ones(len(dl)), np.ones(len(dl))
        hf, K, Kc = 1.1, self.p["k_team"], self.p["k_cup"]
        for _ in range(30):
            ca = np.where(cup, qa[DT] * qd[DO], 1.0)
            e = base[L] * np.where(home == 1, hf, 1 / hf) * ca
            num = np.bincount(T, w * y, len(teams)); den = np.bincount(T, w * e * dw[O], len(teams))
            a = (num + K) / (den + K)
            num = np.bincount(O, w * y, len(teams)); den = np.bincount(O, w * e * a[T], len(teams))
            dw = (num + K) / (den + K)
            core = base[L] * a[T] * dw[O]
            if cup.any():
                c = cup
                num = np.bincount(DT[c], (w * y)[c], len(dl)); den = np.bincount(DT[c], (w * core * np.where(home == 1, hf, 1 / hf) * qd[DO])[c], len(dl))
                qa = (num + Kc) / (den + Kc)
                num = np.bincount(DO[c], (w * y)[c], len(dl)); den = np.bincount(DO[c], (w * core * np.where(home == 1, hf, 1 / hf) * qa[DT])[c], len(dl))
                qd = (num + Kc) / (den + Kc)
            ca = np.where(cup, qa[DT] * qd[DO], 1.0)
            pred = core * ca
            hw = np.sum(w * y * (home == 1)) / np.sum(w * pred * (home == 1))
            aw = np.sum(w * y * (home == 0)) / np.sum(w * pred * (home == 0))
            hf = math.sqrt(hw / aw)
        m = {"teams": ti, "a": a, "d": dw, "base": {l: base[i] for l, i in li.items()}, "hf": hf,
             "qa": {l: qa[i] for l, i in di.items()}, "qd": {l: qd[i] for l, i in di.items()},
             "dom": dom}
        self._cache = (day, m)
        return m

    # ── Joueurs ─────────────────────────────────────────────────────────────
    def player_profile(self, pid, day, team_id):
        h = [r for r in self.player_hist.get(pid, []) if r[0] < day][-40:]
        info = self.player_info.get(pid, {})
        grp = info.get("pos") or "MID"
        prior = {"GK": 0.0, "DEF": 0.045, "MID": 0.09, "ATT": 0.17}[grp]   # part d'xG / 90 min jouees (a 11)
        w = np.array([0.5 ** ((day - r[0]) / 120.0) for r in h]) if h else np.array([])
        mins = np.array([r[2] for r in h]) if h else np.array([])
        team_on = np.array([r[7] for r in h]) if h else np.array([])
        xg = np.array([r[4] for r in h]) if h else np.array([])
        k = self.p["k_share"]
        # part de l'xG de l'equipe pendant ses minutes, ramenee a « 1 joueur sur 11 »
        on_min = float(np.dot(w, mins)) if h else 0.0
        share = ((float(np.dot(w, xg)) + prior * k / 90 * 1.3) / (float(np.dot(w, team_on)) + k / 90 * 1.3 / 1.0)
                 if h else prior)
        starts = [r for r in h if r[3]]
        recent = h[-8:]
        rt = [(r[9], wi) for r, wi in zip(h, w) if len(r) > 9 and r[9]]
        rating = ((sum(x * wi for x, wi in rt) + 6.7 * 3) / (sum(wi for _, wi in rt) + 3)) if rt else 6.6
        return {
            "id": pid, "name": info.get("name", ""), "pos": grp, "share": max(share, 0.0),
            "start_rate": float(np.mean([r[3] for r in recent])) if recent else 0.0,
            "min_start": float(np.mean([r[2] for r in starts[-10:]])) if starts else 75.0,
            "full_rate": float(np.mean([r[8] is None for r in starts[-10:]])) if starts else 0.5,
            "out_min": float(np.mean([r[8] for r in starts[-10:] if r[8] is not None])) if any(r[8] is not None for r in starts[-10:]) else 70.0,
            "sub_rate": float(np.mean([r[2] > 0 for r in h[-10:] if not r[3]])) if any(not r[3] for r in h[-10:]) else 0.4,
            "n": len(h), "on_min": on_min, "rating": rating,
        }

    @staticmethod
    def expected_attack(players):
        """Somme des parts x minutes attendues / 90 d'un effectif (titulaires + banc)."""
        return sum(p["share"] * (p["min_start"] if p["starter"] else p["sub_rate"] * 22) / 90 for p in players)

    def xi_rating(self, team_id, day, xi):
        """Note moyenne de l'onze (profils a la date)."""
        rs = [self.player_profile(pid, day, team_id)["rating"] for pid in xi]
        return sum(rs) / len(rs) if rs else None

    def typical_rating(self, team_id, day, n=8):
        games = [g for g in self.team_games.get(team_id, []) if g[0] < day][-n:]
        vals = [self.xi_rating(team_id, day, g[2][0]) for g in games if g[2][0]]
        vals = [v for v in vals if v]
        return sum(vals) / len(vals) if vals else None

    def typical_attack(self, team_id, day, n=8):
        """Reference : meme estimation appliquee aux vraies feuilles de match des n derniers matchs de
        l'equipe (une equipe au complet donne ainsi un multiplicateur de 1 par construction)."""
        games = [g for g in self.team_games.get(team_id, []) if g[0] < day][-n:]
        if not games: return None
        prof, tot = {}, 0.0
        for _, _, (xi, bench) in games:
            ps = []
            for pid in xi + bench:
                if pid not in prof: prof[pid] = self.player_profile(pid, day, team_id)
                ps.append(dict(prof[pid], starter=pid in xi))
            tot += self.expected_attack(ps)
        return tot / len(games)

    def project(self, home_id, away_id, league, home_squad, away_squad, day, neutral=False):
        """squad : {"xi": [pid], "bench": [pid], "confirmed": bool} (xi vide = composition probable
        d'apres les derniers matchs). -> entrees de simulate()."""
        m = self.team_model(day)
        cup = league in (42, 73)
        out = {"league": league, "params": self.p}
        lam = {}
        for side, tid, oid, sq in (("home", home_id, away_id, home_squad), ("away", away_id, home_id, away_squad)):
            ia, io = m["teams"].get(tid), m["teams"].get(oid)
            base = m["base"].get(league, np.mean(list(m["base"].values())))
            a = m["a"][ia] if ia is not None else 0.85
            d = m["d"][io] if io is not None else 1.0
            hb = self.p.get("home_boost", 1.0)
            f = 1.0 if neutral else (m["hf"] * hb if side == "home" else 1 / (m["hf"] * hb))
            if cup:
                f *= m["qa"].get(m["dom"].get(tid, -1), 0.9) * m["qd"].get(m["dom"].get(oid, -1), 1.0)
            lam[side] = base * a * d * f
            players = [self.player_profile(pid, day, tid) for pid in (sq.get("xi") or []) + (sq.get("bench") or [])]
            for p in players:
                p["starter"] = p["id"] in (sq.get("xi") or [])
            ref = self.typical_attack(tid, day)
            exp_attack = self.expected_attack(players)
            mult = (min(exp_attack / ref, 1.3) ** self.p["lineup_pow"]) if ref and players else 1.0
            q = 0.0
            if self.p.get("rating_beta") and sq.get("xi"):
                r_now, r_ref = self.xi_rating(tid, day, sq["xi"]), self.typical_rating(tid, day)
                if r_now and r_ref: q = max(min(r_now - r_ref, 0.6), -0.6)
            pens = self.team_pens.get(tid, [0, 0])
            out[side] = {"id": tid, "npxg": lam[side] * mult, "npxg_base": lam[side], "lineup_mult": mult, "xi_q": q,
                         "pen_rate": (pens[0] + self.p["pen_rate"] * 10) / (pens[1] + 10) * min(max(a, 0.6), 1.6),
                         "players": players}
        b = self.p.get("rating_beta", 0.0)
        if b:   # qualite de l'onze : plus d'attaque pour soi, moins d'occasions concedees
            for side, o in (("home", "away"), ("away", "home")):
                out[side]["q_mult"] = math.exp(b * out[side]["xi_q"] - b * out[o]["xi_q"])
            for side in ("home", "away"):
                out[side]["npxg"] *= out[side]["q_mult"]
                out[side]["lineup_mult"] *= out[side]["q_mult"]
        return out


# ── Simulation ───────────────────────────────────────────────────────────────

def _pools(players, shots_db, team_pool):
    """Banque de tirs par joueur (a defaut : celle de son equipe/poste). -> tableaux plats + offsets."""
    xs, start, length = [], [], []
    for p in players:
        sh = shots_db.get(p["id"]) or []
        if len(sh) < 8: sh = sh + team_pool
        start.append(len(xs)); length.append(len(sh)); xs += sh
    return xs, np.array(start), np.array(length)


def simulate(inp, n=10000, seed=None, shots_db=None, log_sims=0):
    """10 000 matchs minute par minute. -> dict (scores, mi-temps, buts/tirs par joueur, rouges, journal
    d'evenements des log_sims premiers matchs pour la video)."""
    rng = np.random.default_rng(seed)
    p = inp["params"]
    shots_db = shots_db or {}
    sides = ("home", "away")
    added1 = np.clip(rng.poisson(p["added1"], n), 0, 6)
    added2 = np.clip(rng.poisson(p["added2"], n), 2, 12)
    isd = p.get("intensity_sd", 0.0)
    intensity = np.exp(rng.normal(-isd * isd / 2, isd, n)) if isd else np.ones(n)
    T = 45 + 6 + 45 + 12
    S, POOL = {}, {}
    for s in sides:
        pl = inp[s]["players"]
        k = len(pl)
        share = np.array([q["share"] for q in pl]) if k else np.zeros(0)
        st = np.array([q["starter"] for q in pl], bool) if k else np.zeros(0, bool)
        # entree / sortie (minute de jeu continue, 0..90)
        t_in = np.where(st[None, :], 0.0, np.inf) * np.ones((n, k))
        t_out = np.full((n, k), np.inf)
        for j, q in enumerate(pl):
            if q["starter"]:
                if q["pos"] != "GK":
                    off = rng.random(n) > q["full_rate"]
                    t_out[off, j] = np.clip(rng.normal(q["out_min"], 10, off.sum()), 46, 89)
            else:
                on = rng.random(n) < min(q["sub_rate"], 0.95)
                t_in[on, j] = np.clip(rng.normal(70, 11, on.sum()), 46, 88)
        team_pool = [x for q in pl for x in (shots_db.get(q["id"]) or [])][-400:] or [(85, 34, 0.1, "RegularPlay", "RightFoot", 0, 0, "Miss")]
        pool, pst, plen = _pools(pl, shots_db, team_pool)
        S[s] = dict(share=share, t_in=t_in, t_out=t_out, k=k, rate=inp[s]["npxg"], pen=inp[s]["pen_rate"],
                    pst=pst, plen=plen, mean_xg=np.mean([x[2] for x in team_pool]))
        POOL[s] = (np.array([x[2] for x in pool]) if pool else np.array([0.1]), pool)
        S[s]["goals"] = np.zeros(n, int); S[s]["ht"] = np.zeros(n, int); S[s]["red"] = np.zeros(n, int)
        S[s]["pg"] = np.zeros((n, k), int); S[s]["ps"] = np.zeros((n, k), int); S[s]["pt"] = np.zeros((n, k), int)
        S[s]["xg"] = np.zeros(n)
    log = [[] for _ in range(log_sims)]
    reg_minutes = 90 + p["added1"] + p["added2"]
    for half in (1, 2):
        span = 45 + (6 if half == 1 else 12)
        for mm in range(span):
            minute = (0 if half == 1 else 45) + mm + 1           # minute affichee (46.. en 2e periode)
            in_added = mm >= 45
            alive = mm < 45 + (added1 if half == 1 else added2)   # le temps additionnel de ce match court encore
            if not alive.any(): break
            tb = min(int((minute - 1) // 5), 17) if not in_added else (8 if half == 1 else 17)
            tempo = TEMPO[tb]
            gt = (minute if not in_added else (45 if half == 1 else 90)) - 0.5    # minute de jeu continue
            for s, o in (("home", "away"), ("away", "home")):
                A, B = S[s], S[o]
                diff = np.clip(A["goals"] - B["goals"], -2, 2)
                st_mult = np.select([diff == -2, diff == -1, diff == 1, diff == 2], [STATE[-2], STATE[-1], STATE[1], STATE[2]], 1.0)
                red_mult = np.where(A["red"] > 0, p["red_self"], 1.0) * np.where(B["red"] > 0, p["red_opp"], 1.0)
                xg_min = A["rate"] / reg_minutes * tempo * st_mult * red_mult * intensity
                # tirs : xG de la minute / xG moyen d'un tir de l'equipe
                shot = alive & (rng.random(n) < np.minimum(xg_min / A["mean_xg"], 0.9))
                idx = np.nonzero(shot)[0]
                if len(idx) and A["k"]:
                    on = (A["t_in"][idx] <= gt) & (A["t_out"][idx] > gt)
                    w = on * A["share"][None, :] + 1e-9
                    cw = np.cumsum(w, axis=1)
                    j = np.argmax(cw > rng.random(len(idx))[:, None] * cw[:, -1:], axis=1)
                    pi = A["pst"][j] + (rng.random(len(idx)) * A["plen"][j]).astype(int)
                    xg = POOL[s][0][pi]
                    goal = rng.random(len(idx)) < np.clip(xg * p["finish"], 0, 0.97)
                    A["goals"][idx] += goal; A["xg"][idx] += xg
                    A["pg"][idx, j] += goal; A["ps"][idx, j] += 1
                    sot = goal | np.array([POOL[s][1][q][5] for q in pi], bool)
                    A["pt"][idx, j] += sot
                    if half == 1: A["ht"][idx] += goal
                    for ii, jj, qq, gg in zip(idx, j, pi, goal):
                        if ii < log_sims:
                            log[ii].append((minute if not in_added else (45 if half == 1 else 90) + (mm - 44), s, int(jj), int(qq), bool(gg), "jeu"))
                # penalty
                pen = alive & (rng.random(n) < A["pen"] / reg_minutes * tempo * st_mult)
                pidx = np.nonzero(pen)[0]
                if len(pidx):
                    gpen = rng.random(len(pidx)) < p["pen_conv"]
                    A["goals"][pidx] += gpen; A["xg"][pidx] += 0.76
                    if half == 1: A["ht"][pidx] += gpen
                    if A["k"]:
                        taker = int(np.argmax(A["share"] * np.array([1.0 if q.get("starter") else 0.3 for q in inp[s]["players"]])))
                        A["pg"][pidx, taker] += gpen; A["ps"][pidx, taker] += 1; A["pt"][pidx, taker] += 1
                        for ii, gg in zip(pidx, gpen):
                            if ii < log_sims: log[ii].append((minute, s, taker, -1, bool(gg), "penalty"))
                # but contre son camp (en faveur de s)
                og = alive & (rng.random(n) < p["og_rate"] / reg_minutes)
                A["goals"][og] += 1
                if half == 1: A["ht"][og] += 1
                for ii in np.nonzero(og[:log_sims])[0]: log[ii].append((minute, s, -1, -2, True, "csc"))
                # carton rouge (subi par s)
                red = alive & (rng.random(n) < p["red_rate"] / reg_minutes) & (A["red"] == 0)
                A["red"][red] += 1
                for ii in np.nonzero(red[:log_sims])[0]: log[ii].append((minute, s, -1, -3, False, "rouge"))
    res = {"home_goals": S["home"]["goals"], "away_goals": S["away"]["goals"], "home_ht": S["home"]["ht"],
           "away_ht": S["away"]["ht"], "home_red": S["home"]["red"], "away_red": S["away"]["red"],
           "home_xg": S["home"]["xg"], "away_xg": S["away"]["xg"], "log": log, "pool": {s: POOL[s][1] for s in sides},
           "subs": {s: (S[s]["t_in"][:log_sims], S[s]["t_out"][:log_sims]) for s in sides}}
    for s in sides:
        res[s + "_pgoals"], res[s + "_pshots"], res[s + "_psot"] = S[s]["pg"], S[s]["ps"], S[s]["pt"]
    return res
