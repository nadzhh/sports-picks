"""
nba_wowy.py — « With Or Without You » : qui recupere les minutes (et la place de titulaire)
d'un joueur absent, appris sur l'historique de chaque equipe.

Pour chaque paire de coequipiers (i, j) : minutes de i dans les matchs ou j etait absent,
moins ses minutes quand j jouait (memes 40 derniers matchs de l'equipe, i ayant joue), avec
retrait vers 0 selon le nombre de matchs sans j. Idem pour la probabilite que i soit titulaire.

    w = Wowy(); for gid, g in matchs_chronologiques: w.add_game(g)
    w.delta(team_id, pid, absents) -> (minutes en plus, probabilite de titulaire en plus)

Usage : python nba_wowy.py 2026   # validation : 1re moitie de saison -> 2e moitie
"""
import json, sys
from collections import defaultdict, deque

N_GAMES = 40      # fenetre de matchs de l'equipe
K_SHRINK = 3.0    # matchs sans j equivalents du retrait vers 0
ROT_MIN = 18.0    # joueur dont l'absence compte : 18+ min de moyenne sur ses matchs recents
STATS = ("PTS", "REB", "AST", "3PM", "STL", "BLK")
K_RATE = 4.0      # matchs sans j equivalents du retrait des ratios de stats vers 1 (pas d'effet)


class Wowy:
    def __init__(self, n_games=N_GAMES, k=K_SHRINK):
        self.n, self.k = n_games, k
        self.games = defaultdict(lambda: deque(maxlen=n_games))   # equipe -> [{pid: (min, starter, PTS..BLK)}]
        self.allowed = defaultdict(lambda: deque(maxlen=n_games)) # equipe -> [(joueurs de l'equipe, stats adverses)]

    def add_game(self, g):
        for s in ("home", "away"):
            t = g[s]
            if not t.get("players"): continue
            gm = {p["id"]: (float(p.get("MIN") or 0), bool(p.get("starter")), *[float(p.get(k) or 0) for k in STATS])
                  for p in t["players"] if not p.get("dnp") and p.get("MIN")}
            self.games[t["id"]].append(gm)
            o = g["away" if s == "home" else "home"]
            if o.get("players"):   # ce que l'equipe a concede ce soir-la, selon qui jouait chez elle
                opp = [sum(float(p.get(k) or 0) for p in o["players"] if not p.get("dnp")) for k in STATS]
                self.allowed[t["id"]].append((frozenset(gm), opp))

    def regulars(self, team_id, last=5):
        """Joueurs de rotation recents : {pid: minutes moyennes sur ses matchs joues parmi les `last` derniers}."""
        out = defaultdict(list)
        for gm in list(self.games[team_id])[-last:]:
            for pid, v in gm.items(): out[pid].append(v[0])
        return {pid: sum(v) / len(v) for pid, v in out.items() if len(v) >= 2 and sum(v) / len(v) >= ROT_MIN}

    def delta(self, team_id, pid, absents):
        """(delta minutes, delta proba titulaire) de pid quand les joueurs `absents` manquent."""
        gms = [gm for gm in self.games[team_id] if pid in gm]
        if not gms: return 0.0, 0.0
        dm = ds = 0.0
        for j in absents:
            if j == pid: continue
            wo = [gm[pid] for gm in gms if j not in gm]
            wi = [gm[pid] for gm in gms if j in gm]
            if not wo or not wi: continue
            f = len(wo) / (len(wo) + self.k)
            dm += f * (sum(x[0] for x in wo) / len(wo) - sum(x[0] for x in wi) / len(wi))
            ds += f * (sum(x[1] for x in wo) / len(wo) - sum(x[1] for x in wi) / len(wi))
        return dm, ds


    def stat_ratios(self, team_id, pid, absents, shrink=K_RATE):
        """{stat: ratio} du rendement PAR MINUTE de pid sans les `absents` / avec eux (les minutes sont deja
        traitees par delta) ; chaque absent compte a part (produit), retrait vers 1 selon le nombre de matchs."""
        gms = [gm for gm in self.games[team_id] if pid in gm and gm[pid][0] > 0]
        out = {k: 1.0 for k in STATS}
        for j in absents:
            if j == pid: continue
            wo = [gm[pid] for gm in gms if j not in gm]
            wi = [gm[pid] for gm in gms if j in gm]
            if not wo or not wi: continue
            f = len(wo) / (len(wo) + shrink)
            mwo, mwi = sum(x[0] for x in wo), sum(x[0] for x in wi)
            for i, k in enumerate(STATS):
                a, b = sum(x[2 + i] for x in wo) / mwo, sum(x[2 + i] for x in wi) / mwi
                if b > 0: out[k] *= 1 + f * (min(max(a / b, 0.5), 2.0) - 1)
        return out

    def allowed_ratios(self, team_id, absents, shrink=K_RATE):
        """{stat: ratio} de ce que l'equipe CONCEDE quand ses joueurs `absents` manquent / quand ils jouent."""
        recs = list(self.allowed[team_id])
        out = {k: 1.0 for k in STATS}
        for j in absents:
            wo = [o for here, o in recs if j not in here]
            wi = [o for here, o in recs if j in here]
            if not wo or not wi: continue
            f = len(wo) / (len(wo) + shrink)
            for i, k in enumerate(STATS):
                a, b = sum(o[i] for o in wo) / len(wo), sum(o[i] for o in wi) / len(wi)
                if b > 0: out[k] *= 1 + f * (min(max(a / b, 0.7), 1.4) - 1)
        return out

    def avg_min(self, team_id, j):
        """Minutes moyennes de j sur ses matchs joues dans la fenetre (0 s'il n'a pas joue)."""
        m = [gm[j][0] for gm in self.games[team_id] if j in gm]
        return sum(m) / len(m) if m else 0.0

    def split(self, team_id, pid, j):
        """Pour l'argumentaire : moyennes par match de pid sans j / avec j (n matchs, minutes, stats)."""
        gms = [gm for gm in self.games[team_id] if pid in gm]
        wo = [gm[pid] for gm in gms if j not in gm]
        wi = [gm[pid] for gm in gms if j in gm]
        if not wo or not wi: return None
        avg = lambda L, i: sum(x[i] for x in L) / len(L)
        return {"n_wo": len(wo), "n_wi": len(wi), "MIN": (avg(wo, 0), avg(wi, 0)),
                **{k: (avg(wo, 2 + i), avg(wi, 2 + i)) for i, k in enumerate(STATS)}}

    def allowed_split(self, team_id, j):
        """Pour l'argumentaire : ce que l'equipe concede par match sans j / avec j."""
        recs = list(self.allowed[team_id])
        wo = [o for here, o in recs if j not in here]
        wi = [o for here, o in recs if j in here]
        if not wo or not wi: return None
        return {"n_wo": len(wo), "n_wi": len(wi),
                **{k: (sum(o[i] for o in wo) / len(wo), sum(o[i] for o in wi) / len(wi)) for i, k in enumerate(STATS)}}


def validate(year):
    """Erreur de minutes du simulateur (diag) vs simulateur + correction WOWY apprise en ligne."""
    from nba_boxdb import DB_DIR, load_season
    import numpy as np
    rows = json.loads((DB_DIR / f"minutes_diag_{year}.json").read_text(encoding="utf-8"))
    by_game = defaultdict(list)
    for r in rows: by_game[(r["gid"], r["team"])].append(r)
    prior, test = load_season(year - 1), load_season(year)
    games = sorted([(g["date"], gid, g, year - 1) for gid, g in prior.items()] +
                   [(g["date"], gid, g, year) for gid, g in test.items()])
    w = Wowy()
    feats = []
    for d, gid, g, season in games:
        if season == year:
            for s in ("home", "away"):
                t = g[s]
                rs = by_game.get((gid, t["id"]))
                if not rs: continue
                here = {r["pid"] for r in rs}
                regs = w.regulars(t["id"])
                absents = [j for j in regs if j not in here]
                for r in rs:
                    dm, ds = w.delta(t["id"], r["pid"], absents) if absents else (0.0, 0.0)
                    feats.append((d, r, dm, ds, len(absents)))
        w.add_game(g)
    feats.sort(key=lambda x: x[0])
    half = len(feats) // 2
    tr, te = feats[:half], feats[half:]

    def fit(data):
        # residu (joue - attendu) ~ a * dm, sur les seuls cas avec absent
        X = np.array([f[2] for f in data if f[4]]); y = np.array([f[1]["act"] - f[1]["exp"] for f in data if f[4]])
        return float((X @ y) / max(X @ X, 1e-9))
    a = fit(tr)
    rel = lambda f: max(f[1]["exp"], f[1]["act"]) >= 10
    for lab, data in (("apprentissage (1re moitie)", tr), ("validation (2e moitie)", te)):
        sub = [f for f in data if f[4] and rel(f)]
        base = np.mean([abs(f[1]["act"] - f[1]["exp"]) for f in sub])
        new = np.mean([abs(f[1]["act"] - f[1]["exp"] - a * f[2]) for f in sub])
        allr = [f for f in data if rel(f)]
        base_all = np.mean([abs(f[1]["act"] - f[1]["exp"]) for f in allr])
        new_all = np.mean([abs(f[1]["act"] - f[1]["exp"] - a * f[2]) for f in allr])
        print(f"{lab:28s} coef {a:.2f} | matchs avec absent(s) : {base:.3f} -> {new:.3f} min ({len(sub)}) "
              f"| tous : {base_all:.3f} -> {new_all:.3f}")
    # Titulaire : le delta predit-il les titulaires inattendus ?
    sub = [f for f in te if f[4]]
    hit = [f for f in sub if f[1]["starter"] and f[1]["sr"] < 0.5]
    if hit:
        print(f"Titulaires inattendus (validation, avec absents) : {len(hit)} ; delta titulaire moyen "
              f"{np.mean([f[3] for f in hit]):+.2f} vs {np.mean([f[3] for f in sub if not f[1]['starter']]):+.2f} chez les autres")
    return a


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    validate(int(sys.argv[1]) if len(sys.argv) > 1 else 2026)
