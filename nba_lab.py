"""
nba_lab.py — Laboratoire d'analyses NBA : chaque nuit, le bot essaie d'AJOUTER une analyse a son modele de props
(Wilson, 16/11/2026 : « tester differentes analyses / algorithmes chaque nuit ; que chaque nuit le bot s'ameliore un
peu ; sur des matchs deja passes pour etre sur du resultat ; que les matchs recents comptent un peu plus »).

Principe
  - Modele de base = le calage des props en service (nba_prop_calib) : logit p = logit(marche) + biais[stat]
    + poids[stat] x (logit simu - logit marche). Une « analyse » ajoute un signal x (repos, dos-a-dos, tendance des
    minutes, ligne contre sa moyenne, absences, defense adverse...) : + beta x (x - moyenne) / ecart-type.
  - Jugement A L'AVEUGLE (walk-forward) sur les props deja joues (backtests du bot de nuit : ~48 000 vraies lignes,
    2025-26 et 2026-27) : chaque mois est predit par un modele appris uniquement sur les mois d'avant ; les signaux
    ne lisent que les matchs anterieurs. Les matchs recents comptent plus (demi-vie HALF_LIFE jours), a
    l'apprentissage comme a la note.
  - Note = log-loss pondere de TOUTES les probabilites (le taux de reussite des picks, ±1,5 pt sur ~1 000 paris, est
    trop bruite pour juger ; il est affiche). Adoption : gain >= MIN_GAIN, gain positif dans >= 99 % des tirages
    bootstrap (par journee), et pas de degradation sur chaque saison. Une seule nouveaute par nuit (la meilleure).
  - Une analyse adoptee est remise en cause tous les RECHECK_DAYS jours : retiree si elle n'aide plus.
  - Une analyse refusee est retentee apres RETEST_DAYS jours (nouveaux matchs), ou des qu'une autre est adoptee
    (son apport se juge par rapport au nouveau modele).

Production : p_side(stat, p_sim, p_mkt, over, x) dans nba_prop_calib utilise ce modele quand des analyses sont
adoptees et que les signaux du prop sont fournis (x_for(g, pr)) ; sinon le calage seul.
"""
import json
import math
import statistics
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from pathlib import Path

OUT = Path("data/nba_lab.json")
HALF_LIFE = 120.0          # jours : un match d'il y a 4 mois pese moitie moins qu'un match d'hier
MIN_GAIN = 0.0002          # log-loss pondere
P_MAX = 0.01               # part des tirages bootstrap ou le gain est <= 0
SEASON_TOL = 0.0003        # degradation toleree sur une saison
RETEST_DAYS, RECHECK_DAYS = 21, 28
L2 = 5.0
EV_PICK = 0.03
MIN_TRAIN = 3000

# ─── Catalogue des analyses (signal oriente « plus ») ───────────────────────
CATALOGUE = {
    "b2b":        "dos-à-dos (son équipe a joué la veille)",
    "rest_adv":   "jours de repos de son équipe moins ceux de l'adversaire",
    "home":       "domicile / extérieur",
    "min_trend":  "tendance des minutes (5 derniers matchs contre 20)",
    "line_l10":   "ligne comparée à sa moyenne sur 10 matchs",
    "line_l40":   "ligne comparée à sa moyenne sur 40 matchs",
    "med_gap":    "ligne comparée à sa médiane sur 10 matchs",
    "last_dev":   "dernier match au-dessus / en dessous de sa moyenne (sur-réaction du marché)",
    "hit_l10":    "part de ses 10 derniers matchs au-dessus de la ligne",
    "move":       "mouvement de la cote depuis l'ouverture",
    "cv":         "irrégularité du joueur (écart-type / moyenne sur 20 matchs)",
    "star_out":   "points des coéquipiers majeurs absents",
    "opp_allowed": "ce que l'adversaire concède sur cette stat (15 derniers matchs)",
    "h2h":        "face-à-face contre cet adversaire",
    "venue_split": "écart domicile / extérieur propre au joueur",
    "blowout":    "écart prévu par le marché (titulaires sortis en cas de blowout)",
    "total":      "total de points prévu par le marché (rythme)",
    "fav":        "son équipe favorite ou outsider (écart du marché)",
    "form_combo": "ligne contre sa forme récente (moyenne, médiane et réussite sur 10 matchs réunies)",
    "sim_min_gap": "minutes prévues par la simulation contre ses 10 derniers matchs",
    "early":      "début de saison (peu de matchs joués cette saison)",
    "vig":        "marge du bookmaker sur ce prop (ligne incertaine)",
    "shots_trend": "tendance de ses tirs tentés (5 derniers matchs contre 20)",
}
STATS_ALL = ("PTS", "REB", "AST", "3PM", "STL", "BLK", "TO", "FGA")


def _val(r, st):
    g = lambda k: float(r.get(k) or 0)
    parts = {"PRA": ("PTS", "REB", "AST"), "PR": ("PTS", "REB"), "PA": ("PTS", "AST"), "RA": ("REB", "AST")}.get(st)
    return sum(g(k) for k in parts) if parts else g(st)


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _logit(p):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def _dec(am):
    am = float(am)
    return 1 + (am / 100 if am > 0 else 100 / -am)


def _novig_over(o, u):
    try:
        a, b = 1 / _dec(o), 1 / _dec(u)
        return a / (a + b)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# ─── Etat des feuilles de match (seuls les matchs anterieurs) ───────────────
class State:
    def __init__(self):
        self.ph = defaultdict(list)                  # pid -> [entree de match]
        self.tdates = defaultdict(list)              # equipe -> dates jouees
        self.allowed = defaultdict(list)             # equipe -> [{stat: total concede}]
        self.league = deque(maxlen=600)              # derniers totaux concedes (toutes equipes)
        self.trecent = defaultdict(lambda: deque(maxlen=5))   # equipe -> pids ayant joue ses 5 derniers matchs
        self.h2h = defaultdict(list)                 # (pid, adversaire) -> [stats]

    def add(self, g):
        d = _d(g["date"])
        for s, o in (("home", "away"), ("away", "home")):
            T, O = g[s]["abbr"], g[o]["abbr"]
            played = [p for p in g[s].get("players") or [] if not p.get("dnp") and p.get("MIN")]
            for p in played:
                e = {"d": d, "MIN": float(p["MIN"]), "home": s == "home", "team": T, "opp": O,
                     **{k: float(p.get(k) or 0) for k in STATS_ALL}}
                self.ph[str(p["id"])].append(e)
                self.h2h[(str(p["id"]), O)].append(e)
            opp_rows = [p for p in g[o].get("players") or [] if not p.get("dnp") and p.get("MIN")]
            tot = {k: sum(float(p.get(k) or 0) for p in opp_rows) for k in STATS_ALL}
            self.allowed[T].append(tot); self.league.append(tot)
            self.tdates[T].append(d)
            self.trecent[T].append({str(p["id"]) for p in played})

    def key_mates(self, team, pid):
        """Coequipiers reguliers (>= 24 min sur leurs 10 derniers, encore dans l'equipe) : {pid: points L10}."""
        out = {}
        for q in set().union(*self.trecent[team]) if self.trecent[team] else ():
            if q == pid: continue
            H = self.ph.get(q) or []
            if len(H) < 5 or H[-1]["team"] != team: continue
            L = H[-10:]
            if statistics.mean(e["MIN"] for e in L) >= 24:
                out[q] = statistics.mean(e["PTS"] for e in L)
        return out

    def feats(self, pid, team, opp, home, d, stat, line, odds, lines, absent):
        """Tous les signaux du catalogue pour un prop (0 quand l'historique manque)."""
        f = dict.fromkeys(CATALOGUE, 0.0)
        H = self.ph.get(pid) or []
        v = [_val(e, stat) for e in H]
        # repos
        def rest(t):
            D = self.tdates.get(t) or []
            return min(max((d - D[-1]).days - 1, 0), 3) if D else 3
        TD = self.tdates.get(team) or []
        f["b2b"] = 1.0 if TD and (d - TD[-1]).days == 1 else 0.0
        f["rest_adv"] = float(rest(team) - rest(opp))
        f["home"] = 1.0 if home else -1.0
        if lines and lines.get("home_spread") is not None:
            sp = float(lines["home_spread"])
            fav = (-sp if home else sp) / 10
            f["fav"] = fav
            mins = statistics.mean(e["MIN"] for e in H[-10:]) if H else 0
            f["blowout"] = max(abs(sp) - 5, 0) / 10 * (1.0 if mins >= 28 else 0.0)
        if lines and lines.get("total"):
            f["total"] = (float(lines["total"]) - 225) / 10
        try:
            f["vig"] = (1 / _dec(odds["over"]) + 1 / _dec(odds["under"]) - 1) * 10
        except (TypeError, ValueError, KeyError, ZeroDivisionError):
            pass
        po = _novig_over(odds.get("over"), odds.get("under"))
        p0 = _novig_over(odds.get("over_open"), odds.get("under_open"))
        if po is not None and p0 is not None:
            f["move"] = (po - p0) * 10
        # coequipiers absents
        if absent:
            km = self.key_mates(team, pid)
            f["star_out"] = sum(km[q] for q in absent if q in km) / 20
        # defense adverse sur la stat
        A = self.allowed.get(opp) or []
        if len(A) >= 5 and len(self.league) >= 100:
            a = statistics.mean(_val(t, stat) for t in A[-15:])
            lg = statistics.mean(_val(t, stat) for t in self.league)
            if lg > 0: f["opp_allowed"] = (a - lg) / lg * 10
        n_season = sum(1 for e in H if (d - e["d"]).days < 200 and e["d"] >= date(d.year - (0 if d.month >= 8 else 1), 9, 1))
        f["early"] = 1 / (1 + n_season / 5)
        if len(v) < 5:
            return f
        L10, L20, L40 = v[-10:], v[-20:], v[-40:]
        sd = max(statistics.pstdev(L20), 0.5)
        m10, m20 = statistics.mean(L10), statistics.mean(L20)
        f["line_l10"] = (m10 - line) / sd
        f["line_l40"] = (statistics.mean(L40) - line) / sd
        f["med_gap"] = (statistics.median(L10) - line) / sd
        f["last_dev"] = (v[-1] - m10) / sd
        f["hit_l10"] = sum(1 for x in L10 if x > line) / len(L10) - 0.5
        f["cv"] = sd / max(m20, 1.0)
        f["form_combo"] = (f["line_l10"] + f["med_gap"] + 2 * f["hit_l10"]) / 3
        mm = odds.get("m_mu")
        if mm:
            f["sim_min_gap"] = (float(mm) - statistics.mean(e["MIN"] for e in H[-10:])) / 5
        if len(H) >= 10:
            m5 = statistics.mean(e["MIN"] for e in H[-5:]); mm20 = statistics.mean(e["MIN"] for e in H[-20:])
            if mm20 > 0: f["min_trend"] = max(min(m5 / mm20 - 1, 0.5), -0.5) * 5
            s5 = statistics.mean(e["FGA"] for e in H[-5:]); s20 = statistics.mean(e["FGA"] for e in H[-20:])
            if s20 > 0: f["shots_trend"] = max(min(s5 / s20 - 1, 0.5), -0.5) * 5
        hh = [_val(e, stat) for e in self.h2h.get((pid, opp)) or []]
        if len(hh) >= 3:
            f["h2h"] = math.log((statistics.mean(hh) + 0.5) / (m20 + 0.5)) * 5
        E40 = H[-40:]
        same = [_val(e, stat) for e in E40 if e["home"] == home]
        other = [_val(e, stat) for e in E40 if e["home"] != home]
        if len(same) >= 5 and len(other) >= 5:
            f["venue_split"] = (statistics.mean(same) - statistics.mean(same + other)) / sd
        return f


def _seasons(rows):
    """Annees de saison (2026 = 2025-26) a charger : celles des lignes, plus la precedente pour l'historique."""
    ys = set()
    for r in rows:
        dd = _d(r["date"]); y = dd.year + (1 if dd.month >= 8 else 0)
        ys.add(y)
    return sorted(ys | {min(ys) - 1}) if ys else []


def build(rows):
    """Signaux de chaque ligne de backtest (sans fuite : etat construit avec les matchs precedents seulement)."""
    import nba_boxdb as B
    by_gid = defaultdict(list)
    for i, r in enumerate(rows):
        by_gid[str(r["gid"])].append(i)
    X = [None] * len(rows)
    st = State()
    games = []
    for y in _seasons(rows):
        try:
            games += [(gid, g) for gid, g in B.load_season(y).items() if g.get("type") in (None, "regular", "playoff", "postseason")]
        except Exception:
            pass
    games.sort(key=lambda kv: (kv[1]["date"], kv[0]))
    for gid, g in games:
        idx = by_gid.get(str(gid))
        if idx:
            side_of = {}
            present = {}
            for s in ("home", "away"):
                ps = [p for p in g[s].get("players") or [] if not p.get("dnp") and p.get("MIN")]
                present[s] = {str(p["id"]) for p in ps}
                for p in g[s].get("players") or []: side_of[str(p["id"])] = s
            for i in idx:
                r = rows[i]
                s = side_of.get(str(r["pid"]))
                if not s: continue
                o = "away" if s == "home" else "home"
                T = g[s]["abbr"]
                km = st.key_mates(T, str(r["pid"]))
                absent = [q for q in km if q not in present[s]]
                X[i] = st.feats(str(r["pid"]), T, g[o]["abbr"], s == "home", _d(g["date"]), r["stat"], float(r["line"]),
                                r, g.get("lines"), absent)
        st.add(g)
    return X, st


# ─── Modele et jugement walk-forward ────────────────────────────────────────
def _design(R, X, feats, stats, mu, sd):
    import numpy as np
    n, S, F = len(R), len(stats), len(feats)
    si = {s: k for k, s in enumerate(stats)}
    M = np.zeros((n, 2 * S + F)); off = np.zeros(n)
    for i, r in enumerate(R):
        lm, ls = _logit(r["p_mkt"]), _logit(r["p_sim"])
        k = si[r["stat"]]
        M[i, k] = 1.0; M[i, S + k] = ls - lm; off[i] = lm
        for j, f in enumerate(feats):
            M[i, 2 * S + j] = (X[i][f] - mu[f]) / sd[f]
    return M, off


def _fit(M, off, y, w, l2=L2):
    import numpy as np
    b = np.zeros(M.shape[1])
    w = w / w.mean()
    for _ in range(25):
        p = 1 / (1 + np.exp(-(off + M @ b)))
        g = M.T @ (w * (p - y)) + l2 * b
        Hm = (M * (w * p * (1 - p))[:, None]).T @ M + l2 * np.eye(M.shape[1])
        step = np.linalg.solve(Hm, g)
        b -= step
        if np.abs(step).max() < 1e-7: break
    return b


def _norm(X, idx, feats):
    mu, sd = {}, {}
    for f in feats:
        v = [X[i][f] for i in idx]
        mu[f] = statistics.mean(v) if v else 0.0
        sd[f] = statistics.pstdev(v) or 1.0 if v else 1.0
    return mu, sd


def _recency(R, idx, ref):
    import numpy as np
    return np.array([0.5 ** (max((ref - _d(R[i]["date"])).days, 0) / HALF_LIFE) for i in idx])


def walk_forward(R, X, feats):
    """Probabilite de l'over predite hors echantillon pour chaque ligne (None pour les premiers mois)."""
    import numpy as np
    stats = sorted({r["stat"] for r in R})
    months = sorted({r["date"][:7] for r in R})
    pred = [None] * len(R)
    for mth in months:
        tr = [i for i, r in enumerate(R) if r["date"][:7] < mth]
        te = [i for i, r in enumerate(R) if r["date"][:7] == mth]
        if len(tr) < MIN_TRAIN or not te: continue
        ref = _d(mth + "-01")
        mu, sd = _norm(X, tr, feats)
        Rt = [R[i] for i in tr]; Xt = [X[i] for i in tr]
        M, off = _design(Rt, Xt, feats, stats, mu, sd)
        y = np.array([1.0 if R[i]["actual"] > R[i]["line"] else 0.0 for i in tr])
        b = _fit(M, off, y, _recency(R, tr, ref))
        M2, off2 = _design([R[i] for i in te], [X[i] for i in te], feats, stats, mu, sd)
        p = 1 / (1 + np.exp(-(off2 + M2 @ b)))
        for k, i in enumerate(te): pred[i] = float(p[k])
    return pred


def _ll(p, y):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return -math.log(p if y else 1 - p)


def evaluate(R, pred, ref):
    """Note (log-loss pondere par recence), log-loss par saison, reussite et ROI des picks."""
    idx = [i for i, p in enumerate(pred) if p is not None]
    if not idx: return None
    w = _recency(R, idx, ref)
    L = [_ll(pred[i], R[i]["actual"] > R[i]["line"]) for i in idx]
    out = {"n": len(idx), "ll": float(sum(a * b for a, b in zip(w, L)) / w.sum())}
    by = defaultdict(list)
    for i, l in zip(idx, L):
        dd = _d(R[i]["date"]); by[f"{dd.year - (0 if dd.month >= 8 else 1)}-{str(dd.year + (1 if dd.month >= 8 else 0))[2:]}"].append(l)
    out["season"] = {s: round(statistics.mean(v), 5) for s, v in sorted(by.items())}
    acc = [(pred[i] > 0.5) == (R[i]["actual"] > R[i]["line"]) for i in idx if pred[i] != 0.5]
    out["acc"] = round(100 * sum(acc) / len(acc), 2) if acc else None
    win = roi = n = 0
    for i in idx:
        r, p = R[i], pred[i]
        try:
            do, du = _dec(r["over"]), _dec(r["under"])
        except (TypeError, ValueError, KeyError):
            continue
        eo, eu = p * do - 1, (1 - p) * du - 1
        if max(eo, eu) < EV_PICK: continue
        over = eo >= eu
        ok = (r["actual"] > r["line"]) == over
        n += 1; win += ok; roi += (do if over else du) * ok - 1
    out["picks"] = {"n": n, "hit": round(100 * win / n, 1) if n else None, "roi": round(100 * roi / n, 1) if n else None}
    return out


def _gain_test(R, pb, pc, ref, B=400, seed=7):
    """Gain pondere (base - candidat) et part des tirages bootstrap (par journee) ou il est <= 0."""
    import numpy as np
    idx = [i for i in range(len(R)) if pb[i] is not None and pc[i] is not None]
    w = _recency(R, idx, ref)
    dl = np.array([_ll(pb[i], R[i]["actual"] > R[i]["line"]) - _ll(pc[i], R[i]["actual"] > R[i]["line"]) for i in idx])
    days = defaultdict(lambda: [0.0, 0.0])
    for k, i in enumerate(idx):
        days[R[i]["date"][:10]][0] += w[k] * dl[k]; days[R[i]["date"][:10]][1] += w[k]
    D = np.array(list(days.values()))
    gain = float(D[:, 0].sum() / D[:, 1].sum())
    rng = np.random.default_rng(seed)
    bs = []
    for _ in range(B):
        s = D[rng.integers(0, len(D), len(D))]
        bs.append(s[:, 0].sum() / s[:, 1].sum())
    return gain, float(np.mean(np.array(bs) <= 0))


# ─── Nuit ───────────────────────────────────────────────────────────────────
def load():
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"active": [], "tested": {}, "history": []}


def rows_from_files(paths):
    R = []
    for p in paths:
        D = json.loads(Path(p).read_text(encoding="utf-8"))
        R += [r for r in D.get("real") or [] if r["actual"] != r["line"] and r.get("p_play", 1) >= 0.9
              and r.get("p_mkt") is not None and r.get("p_sim") is not None]
    R.sort(key=lambda r: (r["date"], str(r["gid"])))
    return R


def night(paths, at, dry=False, log=print):
    """Une nuit du laboratoire : teste les analyses eligibles, adopte la meilleure (ou retire une analyse devenue
    inutile), refait le modele de production. Renvoie le rapport (et ecrit data/nba_lab.json sauf --dry)."""
    S = load()
    now = _d(at)
    R = rows_from_files(paths)
    X, _ = build(R)
    keep = [i for i, x in enumerate(X) if x is not None]
    R = [R[i] for i in keep]; X = [X[i] for i in keep]
    ref = max(_d(r["date"]) for r in R)
    active = [f for f in S.get("active") or [] if f in CATALOGUE]
    rep = {"at": at, "n": len(R), "active_before": list(active), "tried": [], "adopted": None, "removed": None}
    pb = walk_forward(R, X, active)
    base = evaluate(R, pb, ref)
    rep["base"] = base
    log(f"  labo NBA : {len(R)} props, base {active or 'calage seul'} : ll {base['ll']:.5f}, reussite {base['acc']} %, "
        f"picks {base['picks']}")
    tested = S.setdefault("tested", {})
    # 1) remise en cause d'une analyse adoptee (une par nuit, la plus ancienne verification)
    due = [f for f in active if (now - _d(tested.get(f, {}).get("at", "2000-01-01"))).days >= RECHECK_DAYS]
    if due:
        f = min(due, key=lambda f: tested.get(f, {}).get("at", ""))
        pr = walk_forward(R, X, [a for a in active if a != f])
        g, pneg = _gain_test(R, pr, pb, ref)        # apport de f = (sans f) - (avec f)
        tested[f] = {"at": at, "gain": round(g, 5), "p": round(pneg, 3), "ok": g > 0 and pneg <= 0.10}
        rep["recheck"] = {"name": f, "gain": round(g, 5), "p": round(pneg, 3)}
        log(f"  labo NBA : verification de {f} : apport {g:+.5f} (p={pneg:.2f})")
        if g <= 0 or pneg > 0.10:
            active = [a for a in active if a != f]
            rep["removed"] = f
            pb = pr; base = evaluate(R, pb, ref); rep["base_after"] = base
    # 2) essais (si rien n'a ete retire cette nuit)
    if not rep["removed"]:
        elig = [f for f in CATALOGUE if f not in active and
                (f not in tested or (now - _d(tested[f]["at"])).days >= RETEST_DAYS or tested[f].get("vs") != sorted(active))]
        res = []
        for f in elig:
            pc = walk_forward(R, X, active + [f])
            ev = evaluate(R, pc, ref)
            g, pneg = _gain_test(R, pb, pc, ref)
            worse = [s for s in base["season"] if ev["season"].get(s, 0) > base["season"][s] + SEASON_TOL]
            ok = g >= MIN_GAIN and pneg <= P_MAX and not worse
            tested[f] = {"at": at, "gain": round(g, 5), "p": round(pneg, 3), "ok": ok, "vs": sorted(active)}
            t = {"name": f, "label": CATALOGUE[f], "gain": round(g, 5), "p": round(pneg, 3), "ok": ok,
                 "acc": ev["acc"], "picks": ev["picks"], "season": ev["season"]}
            rep["tried"].append(t); res.append((t, pc, ev))
            log(f"  labo NBA : essai {f} : gain {g:+.5f} (p={pneg:.3f}), reussite {ev['acc']} %, picks {ev['picks']}"
                + ("  -> OK" if ok else ""))
        okk = [x for x in res if x[0]["ok"]]
        if okk:
            t, pc, ev = max(okk, key=lambda x: x[0]["gain"])
            active = active + [t["name"]]
            rep["adopted"] = dict(t, before={"acc": base["acc"], "picks": base["picks"], "ll": round(base["ll"], 5)},
                                  after={"acc": ev["acc"], "picks": ev["picks"], "ll": round(ev["ll"], 5)})
    rep["active"] = active
    # 3) modele de production (toutes les lignes, recence par rapport a aujourd'hui)
    model = fit_production(R, X, active, ref) if active else None
    if not dry:
        S["active"] = active
        S["model"] = model
        S["at"] = at
        if rep["adopted"] or rep["removed"]:
            S.setdefault("history", []).append({"at": at, "adopted": (rep["adopted"] or {}).get("name"),
                                                "removed": rep["removed"],
                                                "before": (rep["adopted"] or {}).get("before"),
                                                "after": (rep["adopted"] or {}).get("after")})
        S["last"] = {k: v for k, v in rep.items() if k != "tried"} | {"tried": rep["tried"]}
        OUT.write_text(json.dumps(S, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep


def fit_production(R, X, feats, ref):
    import numpy as np
    stats = sorted({r["stat"] for r in R})
    idx = list(range(len(R)))
    mu, sd = _norm(X, idx, feats)
    M, off = _design(R, X, feats, stats, mu, sd)
    y = np.array([1.0 if r["actual"] > r["line"] else 0.0 for r in R])
    b = _fit(M, off, y, _recency(R, idx, ref))
    S = len(stats)
    return {"stats": stats, "coef": {s: [round(float(b[k]), 5), round(float(b[S + k]), 5)] for k, s in enumerate(stats)},
            "feats": feats, "beta": {f: round(float(b[2 * S + j]), 5) for j, f in enumerate(feats)},
            "mu": {f: mu[f] for f in feats}, "sd": {f: sd[f] for f in feats}, "ref": str(ref)}


# ─── Production : signaux des props du soir ─────────────────────────────────
_LIVE = {}


def model():
    if "model" not in _LIVE:
        _LIVE["model"] = (load().get("model") or None)
    return _LIVE["model"]


def p_over(stat, p_sim, p_mkt, x):
    """Probabilite de l'over par le modele du labo ; None si pas de modele, stat inconnue ou signaux absents."""
    M = model()
    if not M or x is None or stat not in M["coef"]: return None
    a, w = M["coef"][stat]
    z = _logit(p_mkt) + a + w * (_logit(p_sim) - _logit(p_mkt))
    for f in M["feats"]:
        if f not in x: return None
        z += M["beta"][f] * (x[f] - M["mu"][f]) / (M["sd"][f] or 1.0)
    return 1 / (1 + math.exp(-z))


def _live_state():
    if "state" not in _LIVE:
        import nba_boxdb as B
        y = B.season_year()
        st = State()
        games = []
        for yy in (y - 1, y):
            try:
                games += list(B.load_season(yy).items())
            except Exception:
                pass
        for _, g in sorted(games, key=lambda kv: (kv[1]["date"], kv[0])):
            st.add(g)
        _LIVE["state"] = st
    return _LIVE["state"]


def x_for(g, pr):
    """Signaux d'un prop du soir (g, pr de data/nba_sim.json) ; None s'il n'y a aucune analyse adoptee."""
    if not model(): return None
    try:
        st = _live_state()
        s = pr["side"]; o = "away" if s == "home" else "home"
        d = (datetime.fromisoformat(g["date"].replace("Z", "+00:00")) - timedelta(hours=6)).date()   # date US
        out_ids = {str(x.get("id")) for x in ((g.get("injuries") or {}).get(s) or {}).get("out") or [] if x.get("id")}
        T = g[s]["abbr"]
        absent = [q for q in st.key_mates(T, str(pr["id"])) if q in out_ids]
        pl = next((p for p in g.get("players") or [] if str(p.get("id")) == str(pr["id"])), {}) or {}
        odds = dict(pr, m_mu=pl.get("MIN"))
        return st.feats(str(pr["id"]), T, g[o]["abbr"], s == "home", d, pr["stat"], float(pr["line"]),
                        odds, g.get("lines"), absent)
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    rep = night(sys.argv[1:], datetime.now().strftime("%Y-%m-%dT%H:%M+00:00"), dry=True)
    print(json.dumps({k: v for k, v in rep.items()}, ensure_ascii=False, indent=1)[:6000])
