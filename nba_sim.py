"""
nba_sim.py — Simulateur Monte-Carlo de matchs NBA au niveau box-score.

Remplace l'approche « joueur par joueur » de nba_picks_engine.py (moyenne
ponderee + loi normale, joueurs independants) par une simulation du match
entier : les stats des joueurs sont tirees a partir du meme scenario de match
(rythme, adresse de l'equipe, minutes, blowout), donc coherentes entre elles.

1. Modele equipe (regression ridge ponderee dans le temps, ajustee adversaire) :
   possessions/48 = mu + P_dom + P_ext ; points/100 poss = mu + Off_att - Def_def + avantage terrain.
   Ancrage optionnel sur les lignes Vegas (total + ecart) avec un poids W_VEGAS.
2. Profil joueur (moyennes exponentielles + retrait bayesien vers la moyenne du poste) :
   minutes, tentatives 2PT/3PT/LF et pertes par possession jouee, % de reussite,
   part des rebonds offensifs/defensifs disponibles, part des paniers assistes.
3. Simulation (N tirages vectorises numpy) :
   possessions -> choc d'adresse par equipe -> minutes (normalisees a 240, starters
   reduits en cas de blowout) -> volumes d'equipe repartis selon minutes x usage
   (les absents liberent des tirs pour les autres) -> reussite binomiale ->
   rebonds et passes repartis par multinomiale -> prolongations si egalite.

Usage :
    from nba_sim import NBAState, simulate
    st = NBAState(); for gid, g, season in games: st.add_game(gid, g, season)
    inp = st.project(home_id, away_id, home_roster, away_roster, lines)
    res = simulate(inp, n=10000)
"""
import math
from collections import defaultdict

import numpy as np

from nba_wowy import Wowy

# ── Parametres (a ajuster par nba_backtest.py) ────────────────────────────────
P = {
    "team_tau":        40.0,   # decroissance ridge equipe, en jours de match
    "prior_season_w":  0.5,    # poids des matchs de la saison precedente
    "ridge_eff":       8.0,    # retrait des notes off/def (en matchs equivalents)
    "ridge_pace":      8.0,
    "w_vegas":         0.75,   # poids des lignes Vegas dans le score attendu
    "hl_rates":        30.0,   # demi-vie (matchs) des taux joueur
    "hl_minutes":      4.0,    # demi-vie (matchs) des minutes
    "min_long_w":      0.0,    # part d'une moyenne longue (demi-vie min_long_hl) dans les minutes attendues.
    "min_long_hl":     20.0,   # Teste le 28/10 (nba_minutes_diag) : 0,25 / 0,4 degradent l'erreur de minutes -> 0
    "k_usage":         150.0,  # pseudo-possessions du retrait vers la moyenne du poste
    "k_p2":            80.0,   # pseudo-tentatives pour 2P%
    "k_p3":            150.0,
    "k_ft":            40.0,
    "pace_sd":         4.75,   # ecart-type des possessions d'un match
    "eff_sd":          0.025,  # choc d'adresse par equipe et par match
    "usage_k":         12.0,   # dispersion gamma des volumes de tirs joueur
    "usage_pow":       1.15,   # concentration de l'usage : parts de tirs/lancers ~ taux^usage_pow
                               # (1.0 sous-projetait les stars de ~2 pts, backtest 2025-26)
    "ast_pow":         1.15,   # idem pour la repartition des passes decisives
    "vol_c":           0.2,    # bruit du volume d'equipe, en multiple de l'ecart-type Poisson
    "shoot_c":         0.85,    # bruit des paniers d'equipe, en multiple de l'ecart-type binomial
    "min_sd_floor":    3.5,
    "min_sd_scale":    1.0,    # facteur sur l'ecart-type des minutes (titulaires plus stables que l'historique brut)
    "min_prior_w":     0.5,    # poids des matchs de la saison precedente dans les minutes
    "norm_pow":        2.0,    # retour des minutes vers 240 : poids m * (1 - m/46)^norm_pow
    "blowout_from":    15.0,   # ecart a partir duquel les titulaires sortent
    "blowout_slope":   0.015,  # -1,5 % de minutes titulaires par point d'ecart au-dela
    "blowout_floor":   0.70,
    # Effets de l'adversaire (0 = desactive, 1 = actif). Backtests --blind du 28/10/2026 sur les 3 echantillons
    # de vraies cotes (ESPN BET oct-nov 2025, avr-juin 2026, DraftKings 2026-27), log-loss de la stat visee :
    "opp_stl":         0.0,    # interceptions ~ pertes de balle adverses : -0,0006 / +0,0017 / +0,0004 -> rejete
    "opp_blk":         0.0,    # contres ~ tirs a 2 pts manques adverses : +0,0031 / -0,0007 / +0,0022 -> rejete
    "opp_ast":         1.0,    # passes ajustees a la defense adverse : +0,0006 / +0,0021 / +0,0005 -> retenu
    # Absents de la rotation (nba_wowy, « with or without you » appris sur les 40 derniers matchs de
    # l'equipe) : minutes en plus = wowy_min x minutes recuperees par le passe + wowy_start x hausse de
    # la probabilite d'etre titulaire. Backtests --blind du 28/10 (3 echantillons de vraies cotes) :
    # (0,15 ; 0,74) appris sur les minutes, (0,3 ; 3) retenu : log-loss 0,6928 -> 0,6924 / 0,6708 -> 0,6703 /
    # 0,6923 -> 0,6880, ROI regle a l'ouverture +23,5 -> +24,4 / +14,0 -> +15,3 / +16,3 -> +16,3 % ; (0,5 ; 5) trop fort.
    "wowy_min":        0.3,
    "wowy_start":      3.0,
                               # (gain faible : le marche integre deja l'essentiel de l'effet adversaire)
}

LEAGUE = {  # valeurs par defaut, recalculees sur les donnees
    "pace": 100.0, "eff": 114.0, "v2": 0.50, "v3": 0.38, "vft": 0.24, "vto": 0.13,
    "orb": 0.27, "dreb_credit": 0.95, "ast": 0.62,
}

STATS = ("PTS", "REB", "AST", "3PM", "STL", "BLK", "TO", "MIN", "FGA", "2PA", "3PA", "FTA", "OREB", "DREB")


def pos_group(pos):
    pos = (pos or "").upper()
    if pos in ("PG", "SG", "G"): return "G"
    if pos == "C": return "C"
    return "F"


def _poss(t):
    return t["FGA"] - t["OREB"] + t["TO"] + 0.44 * t["FTA"]


def _game_len(g):
    """Duree du match en minutes (48 + 5 par prolongation)."""
    n_q = max(len(g["home"].get("quarters") or []), 4)
    return 48.0 + 5.0 * (n_q - 4)


class NBAState:
    """Etat du modele, mis a jour match apres match (walk-forward)."""

    def __init__(self, params=None):
        self.p = dict(P, **(params or {}))
        self.day_idx = -1
        self._last_date = None
        self.season = None
        self.team_recs = []                    # 1 ligne par equipe et par match
        self.player_hist = defaultdict(list)   # pid -> [record] du plus ancien au plus recent
        self.player_info = {}                  # pid -> {name, pos, team}
        self.pos_sums = defaultdict(lambda: defaultdict(float))
        self.league_sums = defaultdict(float)
        self._team_cache = None
        self.wowy = Wowy()

    # ── Mise a jour ──────────────────────────────────────────────────────────
    def add_game(self, gid, g, season):
        if g["date"] != self._last_date:
            self.day_idx += 1
            self._last_date = g["date"]
        if season != self.season:
            self.season = season
        self._team_cache = None
        self.wowy.add_game(g)
        glen = _game_len(g)
        h, a = g["home"], g["away"]
        if "stats" not in h or "stats" not in a: return
        poss = (_poss(h["stats"]) + _poss(a["stats"])) / 2.0
        for side, opp, is_home in ((h, a, 1), (a, h, 0)):
            t, o = side["stats"], opp["stats"]
            fg2a, fg2m = t["FGA"] - t["3PA"], t["FGM"] - t["3PM"]
            miss, opp_miss = t["FGA"] - t["FGM"], o["FGA"] - o["FGM"]
            self.team_recs.append({
                "day": self.day_idx, "season": season, "team": side["id"], "opp": opp["id"],
                "home": is_home, "poss48": poss * 48.0 / glen, "eff": 100.0 * side["pts"] / poss,
                "v2": fg2a / poss, "v3": t["3PA"] / poss, "vft": t["FTA"] / poss, "vto": t["TO"] / poss,
                "orb": t["OREB"] / max(miss, 1), "ast": t["AST"] / max(t["FGM"], 1),
                "dreb_credit": o["DREB"] / max(miss - t["OREB"], 1),
            })
            L = self.league_sums
            L["poss"] += poss; L["pts"] += side["pts"]; L["min"] += glen
            L["fg2a"] += fg2a; L["fg3a"] += t["3PA"]; L["fta"] += t["FTA"]; L["to"] += t["TO"]
            L["miss"] += miss; L["oreb"] += t["OREB"]; L["dreb_opp"] += o["DREB"]
            L["fgm"] += t["FGM"]; L["ast"] += t["AST"]

            for pl in side.get("players") or []:
                if pl.get("dnp") or not pl.get("MIN"):
                    # Laisse sur le banc par choix de l'entraineur : compte pour la
                    # probabilite de jouer (les absents pour blessure ne comptent pas)
                    if "COACH" in (pl.get("reason") or "").upper():
                        self.player_hist[pl["id"]].append({"day": self.day_idx, "season": season, "dnp": True,
                                                           "team": side["id"]})
                    continue
                m = float(pl["MIN"])
                frac = m / glen
                rec = {
                    "day": self.day_idx, "season": season, "team": side["id"], "starter": pl.get("starter", False),
                    "MIN": m * 48.0 / glen,   # minutes ramenees a un match de 48'
                    "op": poss * frac,         # possessions jouees
                    "or_opp": miss * frac, "dr_opp": opp_miss * frac, "ast_opp": t["FGM"] * frac,
                    "2PA": pl["FGA"] - pl["3PA"], "2PM": pl["FGM"] - pl["3PM"],
                    "3PA": pl["3PA"], "3PM": pl["3PM"], "FTA": pl["FTA"], "FTM": pl["FTM"],
                    "OREB": pl["OREB"], "DREB": pl["DREB"], "AST": pl["AST"], "TO": pl["TO"],
                    "STL": pl["STL"], "BLK": pl["BLK"],
                }
                self.player_hist[pl["id"]].append(rec)
                self.player_info[pl["id"]] = {"name": pl["name"], "pos": pl.get("pos", ""), "team": side["id"]}
                ps = self.pos_sums[pos_group(pl.get("pos"))]
                for k in ("op", "or_opp", "dr_opp", "ast_opp", "2PA", "2PM", "3PA", "3PM", "FTA", "FTM",
                          "OREB", "DREB", "AST", "TO", "STL", "BLK"):
                    ps[k] += rec[k]

    # ── Modele equipe ────────────────────────────────────────────────────────
    def _weights(self, recs, tau):
        cur = self.day_idx + 1
        w = np.array([math.exp(-(cur - r["day"]) / tau) *
                      (1.0 if r["season"] == self.season else self.p["prior_season_w"]) for r in recs])
        return w

    def team_model(self):
        if self._team_cache is not None: return self._team_cache
        recs = [r for r in self.team_recs if self.day_idx + 1 - r["day"] < 6 * self.p["team_tau"]]
        teams = sorted({r["team"] for r in recs})
        ti = {t: i for i, t in enumerate(teams)}
        n, k = len(recs), len(teams)
        w = self._weights(recs, self.p["team_tau"])

        # Efficacite : y = mu + Off[t] - Def[o] + h*home
        X = np.zeros((n, 2 * k + 2))
        y = np.array([r["eff"] for r in recs])
        for i, r in enumerate(recs):
            X[i, 0] = 1.0; X[i, 1] = r["home"]
            X[i, 2 + ti[r["team"]]] = 1.0; X[i, 2 + k + ti[r["opp"]]] = -1.0
        beta = _ridge(X, y, w, self.p["ridge_eff"], free=2)
        eff = {"mu": beta[0], "home": beta[1],
               "off": {t: beta[2 + i] for t, i in ti.items()}, "def": {t: beta[2 + k + i] for t, i in ti.items()}}

        # Rythme : y = mu + P[t] + P[o] (chaque match compte 2 fois, une par equipe)
        Xp = np.zeros((n, k + 1)); yp = np.array([r["poss48"] for r in recs])
        for i, r in enumerate(recs):
            Xp[i, 0] = 1.0; Xp[i, 1 + ti[r["team"]]] += 1.0; Xp[i, 1 + ti[r["opp"]]] += 1.0
        bp = _ridge(Xp, yp, w, self.p["ridge_pace"], free=1)
        pace = {"mu": bp[0], "team": {t: bp[1 + i] for t, i in ti.items()}}

        # Taux d'equipe (volumes par possession, rebonds, passes) : moyennes ponderees
        rates, allowed = {}, {}
        lg = {key: float(np.average([r[key] for r in recs], weights=w))
              for key in ("v2", "v3", "vft", "vto", "orb", "ast", "dreb_credit")}
        by_team, by_opp = defaultdict(list), defaultdict(list)
        for r, wi in zip(recs, w):
            by_team[r["team"]].append((r, wi)); by_opp[r["opp"]].append((r, wi))
        K = 10.0   # retrait vers la ligue (en matchs equivalents)
        for t in teams:
            rates[t] = {key: _shrunk([(r[key], wi) for r, wi in by_team[t]], lg[key], K)
                        for key in ("v2", "v3", "vft", "vto", "orb", "ast")}
            allowed[t] = {key: _shrunk([(r[key], wi) for r, wi in by_opp[t]], lg[key], K)
                          for key in ("v2", "v3", "vft", "vto", "orb", "ast")}
        self._team_cache = {"eff": eff, "pace": pace, "rates": rates, "allowed": allowed, "league": lg}
        return self._team_cache

    def availability(self, pid, team_id, n=5):
        """Part des n derniers matchs de l'equipe ou le joueur figurait sur la feuille
        (joue ou laisse sur le banc). Les inactifs (blesses de longue duree non declares,
        joueurs envoyes en G-League) n'y figurent pas. None si l'equipe n'a pas encore
        joue cette saison ; 1.0 pour un joueur arrive d'une autre equipe depuis."""
        team_days = sorted({r["day"] for r in self.team_recs
                            if r["team"] == team_id and r["season"] == self.season})[-n:]
        if not team_days: return None
        hist = self.player_hist.get(pid) or []
        days = {r["day"] for r in hist if r.get("team") == team_id}
        if hist and hist[-1].get("team") != team_id and hist[-1]["day"] >= team_days[0]:
            return 1.0   # transfere recemment
        return len(days.intersection(team_days)) / len(team_days)

    def rotation_share(self, pid, team_id, n=3):
        """Part des n derniers matchs de l'equipe ou le joueur a JOUE (minutes > 0).
        None tant que l'equipe a joue moins de 2 matchs ou si le joueur vient d'arriver.
        0 pour un joueur present mais laisse sur le banc (« Coach's decision ») : ses
        minutes de la saison passee ne disent rien de son role actuel."""
        team_days = sorted({r["day"] for r in self.team_recs
                            if r["team"] == team_id and r["season"] == self.season})[-n:]
        if len(team_days) < 2: return None
        hist = self.player_hist.get(pid) or []
        if hist and hist[-1].get("team") != team_id and hist[-1]["day"] >= team_days[0]:
            return None
        played = {r["day"] for r in hist if r.get("team") == team_id and not r.get("dnp")}
        return len(played.intersection(team_days)) / len(team_days)

    # ── Profil joueur ────────────────────────────────────────────────────────
    def player_profile(self, pid, pos=None):
        hist = self.player_hist.get(pid) or []
        info = self.player_info.get(pid, {})
        grp = pos_group(pos or info.get("pos"))
        ps = self.pos_sums[grp]
        prior = {
            "r2": ps["2PA"] / max(ps["op"], 1), "r3": ps["3PA"] / max(ps["op"], 1),
            "rft": ps["FTA"] / max(ps["op"], 1), "rto": ps["TO"] / max(ps["op"], 1),
            "rstl": ps["STL"] / max(ps["op"], 1), "rblk": ps["BLK"] / max(ps["op"], 1),
            "p2": ps["2PM"] / max(ps["2PA"], 1), "p3": ps["3PM"] / max(ps["3PA"], 1),
            "pft": ps["FTM"] / max(ps["FTA"], 1),
            "s_or": ps["OREB"] / max(ps["or_opp"], 1), "s_dr": ps["DREB"] / max(ps["dr_opp"], 1),
            "s_ast": ps["AST"] / max(ps["ast_opp"], 1),
        }
        # Probabilite de jouer (hors blessure) sur les 10 derniers matchs disponibles
        last = hist[-10:]
        if last:
            wp = 0.5 ** (np.arange(len(last))[::-1] / (2 * self.p["hl_minutes"]))
            p_play = float(np.average([0.0 if r.get("dnp") else 1.0 for r in last], weights=wp))
        else:
            p_play = 0.5
        recent = [r for r in hist if not r.get("dnp")][-80:]
        nh = len(recent)
        if nh:
            idx = np.arange(nh)[::-1]   # 0 = match le plus recent
            seas = np.array([1.0 if r["season"] == self.season else self.p["prior_season_w"] for r in recent])
            w = 0.5 ** (idx / self.p["hl_rates"]) * seas
            S = lambda key: float(np.dot(w, [r[key] for r in recent]))
        else:
            S = lambda key: 0.0
        ku = self.p["k_usage"]
        op = S("op")
        prof = {
            "r2": (S("2PA") + ku * prior["r2"]) / (op + ku),
            "r3": (S("3PA") + ku * prior["r3"]) / (op + ku),
            "rft": (S("FTA") + ku * prior["rft"]) / (op + ku),
            "rto": (S("TO") + ku * prior["rto"]) / (op + ku),
            "rstl": (S("STL") + ku * prior["rstl"]) / (op + ku),
            "rblk": (S("BLK") + ku * prior["rblk"]) / (op + ku),
            "p2": (S("2PM") + self.p["k_p2"] * prior["p2"]) / (S("2PA") + self.p["k_p2"]),
            "p3": (S("3PM") + self.p["k_p3"] * prior["p3"]) / (S("3PA") + self.p["k_p3"]),
            "pft": (S("FTM") + self.p["k_ft"] * prior["pft"]) / (S("FTA") + self.p["k_ft"]),
            "s_or": (S("OREB") + 40 * prior["s_or"]) / (S("or_opp") + 40),
            "s_dr": (S("DREB") + 40 * prior["s_dr"]) / (S("dr_opp") + 40),
            "s_ast": (S("AST") + 40 * prior["s_ast"]) / (S("ast_opp") + 40),
            "n_games": nh, "n_season": sum(1 for r in recent if r["season"] == self.season),
            "p_play": p_play,
        }
        # Minutes : moyenne exponentielle courte (role actuel), ecart-type pondere
        if nh:
            mins = np.array([r["MIN"] for r in recent])
            seas_m = np.array([1.0 if r["season"] == self.season else self.p["min_prior_w"] for r in recent])
            wm = 0.5 ** (idx / self.p["hl_minutes"]) * seas_m
            mu = float(np.average(mins, weights=wm))
            sd = float(math.sqrt(np.average((mins - mu) ** 2, weights=wm)))
            if self.p["min_long_w"] > 0:
                wl = 0.5 ** (idx / self.p["min_long_hl"]) * seas_m
                mu = (1 - self.p["min_long_w"]) * mu + self.p["min_long_w"] * float(np.average(mins, weights=wl))
            prof["m_mu"], prof["m_sd"] = mu, min(max(sd, self.p["min_sd_floor"]), 10.0) * self.p["min_sd_scale"]
            prof["starter_rate"] = float(np.average([r["starter"] for r in recent], weights=wm))
        else:
            prof["m_mu"], prof["m_sd"], prof["starter_rate"] = 8.0, 6.0, 0.0
        return prof

    # ── Projection d'un match ────────────────────────────────────────────────
    def project(self, home_id, away_id, home_roster, away_roster, lines=None):
        """roster : [{id, name, pos}] des joueurs disponibles (blesses deja retires).
        lines : {home_spread, total} Vegas ou None."""
        tm = self.team_model()
        eff, pace, lg = tm["eff"], tm["pace"], tm["league"]
        pace_mu = pace["mu"] + pace["team"].get(home_id, 0) + pace["team"].get(away_id, 0)
        e_h = eff["mu"] + eff["home"] + eff["off"].get(home_id, 0) - eff["def"].get(away_id, 0)
        e_a = eff["mu"] + eff["off"].get(away_id, 0) - eff["def"].get(home_id, 0)
        model_h, model_a = pace_mu * e_h / 100.0, pace_mu * e_a / 100.0
        pts_h, pts_a = model_h, model_a
        vegas = None
        if lines and lines.get("total") and lines.get("home_spread") is not None:
            vt, vm = float(lines["total"]), -float(lines["home_spread"])
            vegas = ((vt + vm) / 2.0, (vt - vm) / 2.0)
            w = self.p["w_vegas"]
            pts_h = w * vegas[0] + (1 - w) * model_h
            pts_a = w * vegas[1] + (1 - w) * model_a

        def _team(tid, oid, roster):
            r, al = tm["rates"].get(tid, lg), tm["allowed"].get(oid, lg)
            vol = {k: r[k] * al[k] / lg[k] for k in ("v2", "v3", "vft", "vto")}
            players = []
            wm, ws = self.p.get("wowy_min", 0), self.p.get("wowy_start", 0)
            here = {pl["id"] for pl in roster}
            absents = [j for j in self.wowy.regulars(tid) if j not in here] if (wm or ws) else []
            for pl in roster:
                prof = self.player_profile(pl["id"], pl.get("pos"))
                prof.update(id=pl["id"], name=pl.get("name") or self.player_info.get(pl["id"], {}).get("name", ""),
                            pos=pl.get("pos", ""))
                if absents:   # qui recupere les minutes / la place de titulaire des absents
                    dm, ds = self.wowy.delta(tid, pl["id"], absents)
                    sr = min(max(prof["starter_rate"] + ds, 0.0), 1.0)
                    prof["m_mu"] = max(prof["m_mu"] + wm * dm + ws * (sr - prof["starter_rate"]), 0.0)
                    if ws: prof["starter_rate"] = sr
                players.append(prof)
            ast = r["ast"] * (al["ast"] / lg["ast"]) ** self.p["opp_ast"]
            return {"id": tid, "vol": vol, "orb": r["orb"] * al["orb"] / lg["orb"], "ast": ast,
                    "players": players}

        return {
            "home": _team(home_id, away_id, home_roster), "away": _team(away_id, home_id, away_roster),
            "pace": pace_mu, "pts_h": pts_h, "pts_a": pts_a, "model": (model_h, model_a), "vegas": vegas,
            "dreb_credit": lg["dreb_credit"], "params": self.p, "lg_vto": lg["vto"], "lg_v2": lg["v2"],
        }


def _shrunk(vals_w, prior, k):
    if not vals_w: return prior
    v = np.array([x for x, _ in vals_w]); w = np.array([wi for _, wi in vals_w])
    return float((np.dot(v, w) + k * prior) / (w.sum() + k))


def _ridge(X, y, w, lam, free=1):
    """Moindres carres ponderes avec penalite ridge sauf sur les `free` premieres colonnes."""
    W = w[:, None]
    A = X.T @ (X * W)
    reg = np.full(X.shape[1], lam); reg[:free] = 1e-6
    A[np.diag_indices_from(A)] += reg
    return np.linalg.solve(A, X.T @ (y * w))


# ── Simulation ────────────────────────────────────────────────────────────────

def _side_arrays(team):
    ps = team["players"]
    g = lambda key: np.array([p[key] for p in ps], dtype=float)
    return {k: g(k) for k in ("m_mu", "m_sd", "r2", "r3", "rft", "rto", "rstl", "rblk",
                              "p2", "p3", "pft", "s_or", "s_dr", "s_ast", "starter_rate", "p_play")}


def _draw_minutes(rng, A, n, total, blow, pw=None):
    """Minutes par joueur (n, k), somme = total ; titulaires reduits par `blow` (n,)."""
    m = rng.normal(A["m_mu"], A["m_sd"], size=(n, len(A["m_mu"])))
    m = np.clip(m, 0, 46)
    m[:, A["m_mu"] < 1] = 0
    m *= rng.random(m.shape) < A["p_play"][None, :]
    m = m * np.where(A["starter_rate"] >= 0.5, 1.0, 0.0)[None, :] * (blow[:, None] - 1) + m
    if total < 240:   # prolongation : repartition proportionnelle
        return m * total / np.maximum(m.sum(axis=1, keepdims=True), 1e-9)
    return _normalize_minutes(m, total, pw=pw)


def _normalize_minutes(m, total, cap=46.0, pw=None):
    """Ramene la somme des minutes a `total` en ajustant surtout les fins de rotation :
    poids m * (1 - m/cap)^2, quasi nul pour un titulaire a 36-40'. Avec un effectif
    plethorique ce sont les remplacants qui perdent leurs minutes (pas les titulaires),
    avec un effectif decime ils en gagnent le plus."""
    for _ in range(8):
        gap = total - m.sum(axis=1, keepdims=True)
        if np.abs(gap).max() < 0.05: break
        w = m * (1 - m / cap) ** (P["norm_pow"] if pw is None else pw)
        m = np.clip(m + gap * w / np.maximum(w.sum(axis=1, keepdims=True), 1e-9), 0, cap)
    return m * total / np.maximum(m.sum(axis=1, keepdims=True), 1e-9)


def _split_counts(rng, total, weights):
    """Reparti total (n,) entre les joueurs selon weights (n, k) -> (n, k)."""
    wsum = weights.sum(axis=1, keepdims=True)
    pv = weights / np.maximum(wsum, 1e-12)
    return rng.multinomial(total.astype(np.int64), pv)


def _usage_split(rng, lam_team, share, k_disp, vol_c):
    """Volume d'equipe ~ Poisson(lam_team) reparti par multinomiale selon share (n, k).
    Les parts recoivent un bruit gamma (soir avec / sans d'un joueur) mais le total
    reste celui de l'equipe : si un joueur prend plus de tirs, les autres en prennent moins."""
    share = share * rng.gamma(k_disp, 1.0 / k_disp, size=share.shape)
    # A possessions donnees le volume d'equipe varie peu (sous-disperse vs Poisson) :
    # le hasard du score vient surtout de l'adresse. vol_c calibre sur 2025-26.
    team = np.maximum(np.rint(rng.normal(lam_team, vol_c * np.sqrt(lam_team))), 0)
    return _split_counts(rng, team, share)


def _team_makes(rng, att, pct, shoot_c):
    """Paniers reussis (n, k). Le total d'equipe est tire avec shoot_c x l'ecart-type
    binomial puis reparti entre joueurs selon tentatives x adresse : un vrai match
    est moins aleatoire que des tirs independants (choix de tirs qui s'adaptent,
    secondes chances), cf. calibrage sur 2025-26 (ecart-type equipe 11,5 pts)."""
    exp = att * pct
    mu, var = exp.sum(axis=1), (exp * (1 - pct)).sum(axis=1)
    team = np.clip(np.rint(rng.normal(mu, shoot_c * np.sqrt(var))), 0, att.sum(axis=1))
    made = _split_counts(rng, team, exp)
    return np.minimum(made, att)


def _sim_block(rng, inp, n, frac, poss, shock, scale, blow):
    """Simule une portion de match (frac=1 temps reglementaire, 5/48 une prolongation)."""
    p = inp["params"]
    out = {}
    sides = {}
    for s in ("home", "away"):
        A = _side_arrays(inp[s])
        m = _draw_minutes(rng, A, n, 240.0 * frac, blow[s], pw=p.get("norm_pow"))
        on = m / (48.0 * frac)          # part du temps sur le terrain (somme = 5)
        vol = inp[s]["vol"]
        k = p["usage_k"]
        g = p.get("usage_pow", 1.0)
        fg2a = _usage_split(rng, vol["v2"] * poss, on * A["r2"] ** g, k, p["vol_c"])
        fg3a = _usage_split(rng, vol["v3"] * poss, on * A["r3"] ** g, k, p["vol_c"])
        fta = _usage_split(rng, vol["vft"] * poss, on * A["rft"] ** g, k, p["vol_c"])
        tov = _usage_split(rng, vol["vto"] * poss, on * A["rto"], k, p["vol_c"])
        sh = (1.0 + shock[s])[:, None] * scale[s]
        fg2m = _team_makes(rng, fg2a, np.clip(A["p2"] * sh, 0.05, 0.95), p["shoot_c"])
        fg3m = _team_makes(rng, fg3a, np.clip(A["p3"] * sh, 0.02, 0.80), p["shoot_c"])
        ftm = rng.binomial(fta, np.clip(A["pft"] * np.sqrt(sh), 0.2, 0.98))
        stl = rng.poisson(on * A["rstl"] * poss[:, None])
        blk = rng.poisson(on * A["rblk"] * poss[:, None])
        sides[s] = dict(A=A, m=m, on=on, fg2a=fg2a, fg3a=fg3a, fta=fta, fg2m=fg2m, fg3m=fg3m, ftm=ftm,
                        tov=tov, stl=stl, blk=blk, pts=2 * fg2m + 3 * fg3m + ftm)
    # L'adversaire nourrit la defense : interceptions a proportion de ses pertes de balle, contres
    # a proportion de ses tirs a 2 pts manques (ratio match simule / moyenne attendue, puissance opp_*)
    if p.get("opp_stl", 0) or p.get("opp_blk", 0):
        for s, o in (("home", "away"), ("away", "home")):
            S, O = sides[s], sides[o]
            if p.get("opp_stl", 0):
                to = O["tov"].sum(axis=1)
                f = to / max(to.mean(), 1e-9) * inp[o]["vol"]["vto"] / inp["lg_vto"]
                S["stl"] = rng.poisson(S["on"] * S["A"]["rstl"] * poss[:, None] * (f ** p["opp_stl"])[:, None])
            if p.get("opp_blk", 0):
                miss2 = (O["fg2a"] - O["fg2m"]).sum(axis=1)
                f = miss2 / max(miss2.mean(), 1e-9) * inp[o]["vol"]["v2"] / inp["lg_v2"]
                S["blk"] = rng.poisson(S["on"] * S["A"]["rblk"] * poss[:, None] * (f ** p["opp_blk"])[:, None])
    for s, o in (("home", "away"), ("away", "home")):
        S, O = sides[s], sides[o]
        fga, fgm = S["fg2a"] + S["fg3a"], S["fg2m"] + S["fg3m"]
        miss = (fga - fgm).sum(axis=1)
        team_or = rng.binomial(miss, np.clip(inp[s]["orb"], 0.05, 0.6))
        opp_dr = rng.binomial(miss - team_or, np.clip(inp["dreb_credit"], 0.5, 1.0))
        S["oreb"] = _split_counts(rng, team_or, S["on"] * S["A"]["s_or"])
        O["dreb"] = _split_counts(rng, opp_dr, O["on"] * O["A"]["s_dr"])
        team_ast = rng.binomial(fgm.sum(axis=1), np.clip(inp[s]["ast"], 0.3, 0.9))
        S["ast"] = _split_counts(rng, team_ast, S["on"] * S["A"]["s_ast"] ** p.get("ast_pow", 1.0))
    for s in ("home", "away"):
        S = sides[s]
        out[s] = {
            "PTS": S["pts"], "REB": S["oreb"] + S["dreb"], "OREB": S["oreb"], "DREB": S["dreb"], "AST": S["ast"],
            "3PM": S["fg3m"], "3PA": S["fg3a"], "2PA": S["fg2a"], "FGA": S["fg2a"] + S["fg3a"], "FTA": S["fta"],
            "STL": S["stl"], "BLK": S["blk"], "TO": S["tov"], "MIN": S["m"],
        }
    return out


def _expected_pts(team, poss, pw=None, g=1.0):
    """Points attendus d'une equipe a minutes moyennes (sert a caler l'adresse sur la cible)."""
    A = _side_arrays(team)
    m = _normalize_minutes((np.clip(A["m_mu"], 0, 46) * A["p_play"])[None, :], 240.0, pw=pw)[0]
    on = m / 48.0
    v = team["vol"]
    def share(r): x = on * r ** g; return x / max(x.sum(), 1e-12)
    return poss * (v["v2"] * 2 * np.dot(share(A["r2"]), A["p2"]) + v["v3"] * 3 * np.dot(share(A["r3"]), A["p3"])
                   + v["vft"] * np.dot(share(A["rft"]), A["pft"]))


def simulate(inp, n=10000, seed=None):
    rng = np.random.default_rng(seed)
    p = inp["params"]
    # Calage : l'adresse est ajustee pour que les points moyens collent a la cible
    pw = p.get("norm_pow")
    g = p.get("usage_pow", 1.0)
    scale = {"home": inp["pts_h"] / max(_expected_pts(inp["home"], inp["pace"], pw, g), 1),
             "away": inp["pts_a"] / max(_expected_pts(inp["away"], inp["pace"], pw, g), 1)}
    # Pre-tirage de calage : la formule ci-dessus ignore absences tirees au sort, blowouts
    # et saturation des pourcentages (le total simule sortait ~4 pts sous la cible)
    if p.get("pilot", 600):
        prng = np.random.default_rng(None if seed is None else seed + 7919)
        npil = p.get("pilot", 600)
        pposs = np.maximum(prng.normal(inp["pace"], p["pace_sd"], npil), 70)
        pshock = {s: prng.normal(0, p["eff_sd"], npil) for s in ("home", "away")}
        pres = _sim_block(prng, inp, npil, 1.0, pposs, pshock, scale, {s: np.ones(npil) for s in ("home", "away")})
        for s, key in (("home", "pts_h"), ("away", "pts_a")):
            scale[s] *= inp[key] / max(pres[s]["PTS"].sum(axis=1).mean(), 1)
    poss = np.maximum(rng.normal(inp["pace"], p["pace_sd"], n), 70)
    shock = {"home": rng.normal(0, p["eff_sd"], n), "away": rng.normal(0, p["eff_sd"], n)}
    # Ecart attendu dans ce scenario -> sorties des titulaires en cas de blowout
    margin_est = (inp["pts_h"] * (1 + shock["home"]) - inp["pts_a"] * (1 + shock["away"])) * poss / inp["pace"]
    excess = np.clip(np.abs(margin_est) - p["blowout_from"], 0, None)
    b = np.maximum(1 - p["blowout_slope"] * excess, p["blowout_floor"])
    blow = {"home": b, "away": b}
    res = _sim_block(rng, inp, n, 1.0, poss, shock, scale, blow)
    # Prolongations : on rejoue 5' pour les tirages a egalite
    n_ot = np.zeros(n, dtype=int)
    for _ in range(4):
        tie = res["home"]["PTS"].sum(axis=1) == res["away"]["PTS"].sum(axis=1)
        idx = np.nonzero(tie)[0]
        if not len(idx): break
        n_ot[idx] += 1
        sub = _sim_block(rng, inp, len(idx), 5.0 / 48.0, poss[idx] * 5.0 / 48.0,
                         {k: v[idx] for k, v in shock.items()}, scale, {k: np.ones(len(idx)) for k in blow})
        for s in ("home", "away"):
            for key in res[s]:
                res[s][key][idx] += sub[s][key]
    res["n_ot"] = n_ot
    res["poss"] = poss
    return res


def summarize(inp, res, lines_grid=None):
    """Distribution par joueur : moyenne, mediane, quantiles, P(over) sur une grille de lignes."""
    out = {"home_pts": res["home"]["PTS"].sum(axis=1), "away_pts": res["away"]["PTS"].sum(axis=1)}
    hp, ap = out["home_pts"], out["away_pts"]
    team = {"home_mean": float(hp.mean()), "away_mean": float(ap.mean()),
            "total_mean": float((hp + ap).mean()), "total_sd": float((hp + ap).std()),
            "margin_mean": float((hp - ap).mean()), "margin_sd": float((hp - ap).std()),
            "p_home_win": float((hp > ap).mean()), "p_ot": float((res["n_ot"] > 0).mean())}
    players = []
    for s in ("home", "away"):
        for i, pl in enumerate(inp[s]["players"]):
            # Distributions conditionnelles au fait de jouer (pari annule sinon)
            on = res[s]["MIN"][:, i] > 0
            if not on.any(): continue
            d = {"id": pl["id"], "name": pl["name"], "side": s, "m_mu": pl["m_mu"], "p_play": float(on.mean())}
            for key in ("PTS", "REB", "AST", "3PM", "STL", "BLK", "MIN"):
                x = res[s][key][on, i]
                d[key] = {"mean": float(x.mean()), "q": np.percentile(x, [10, 25, 50, 75, 90]).tolist()}
            pra = (res[s]["PTS"][:, i] + res[s]["REB"][:, i] + res[s]["AST"][:, i])[on]
            d["PRA"] = {"mean": float(pra.mean()), "q": np.percentile(pra, [10, 25, 50, 75, 90]).tolist()}
            players.append(d)
    return {"team": team, "players": players}


def p_over(res, side, i, stat, line):
    """P(stat > line | joue) pour le joueur i d'un cote. stat peut etre combine : 'PTS+REB+AST'."""
    on = res[side]["MIN"][:, i] > 0
    x = sum(res[side][k][:, i] for k in stat.split("+"))[on]
    return float((x > line).mean()) if len(x) else float("nan")
