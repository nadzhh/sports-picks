"""Poids du face-a-face (H2H) dans le moteur de picks NBA, mesure chaque nuit (Wilson, 16/11/2026 : « oui ; il faut que
ce soit en constante amelioration »).

Constat du 16/11 (etude walk-forward sur 3 saisons de feuilles de match, ~10 000 cas par stat) : quand un joueur a
fait X % de plus (ou de moins) que son niveau recent contre un adversaire (>= 3 face-a-face), on ne retrouve au match
suivant contre lui qu'environ 10 % de cet ecart (pente PTS +0,08, REB +0,12, AST +0,13). Le moteur appliquait 100 %
de l'ecart (plafonne a +-8 %) et +5 points de confiance a 70 % de reussite H2H : il surpondérait le H2H.

Ici : regression, stat par stat, de log(reel / niveau L20) sur log(moyenne H2H / niveau L20), sans fuite (seuls les
matchs anterieurs comptent). Le poids retenu est la pente, bornee a [0 ; 0,5] et ramenee vers 0 quand l'echantillon est
petit. improve_bot le refait chaque nuit et l'ecrit dans data/model_params.json (cle « h2h_w ») ; nba_picks_engine
l'applique : attendu x (moyenne H2H / niveau) ^ poids, et bonus de confiance H2H = 5 x poids / 0,5.
"""
import math
from collections import defaultdict

STATS = ("PTS", "REB", "AST", "3PM", "PRA", "PR", "PA")
DEFAULT = 0.10
MIN_N = 500


def _val(p, st):
    g = lambda k: float(p.get(k) or 0)
    return {"PRA": g("PTS") + g("REB") + g("AST"), "PR": g("PTS") + g("REB"), "PA": g("PTS") + g("AST")}.get(st, g(st))


def measure(years=(2025, 2026, 2027), min_h2h=3):
    """{stat: {"w": poids, "slope": pente brute, "se": erreur type, "n": cas}}."""
    import numpy as np
    import nba_boxdb as B
    hist, h2h = defaultdict(list), defaultdict(list)
    X = defaultdict(list)
    for y in years:
        for gid, g in sorted(B.load_season(y).items(), key=lambda kv: (kv[1]["date"], kv[0])):
            if g.get("type") not in (None, "regular"): continue
            upd = []
            for s, o in (("home", "away"), ("away", "home")):
                opp = g[o]["abbr"]
                for p in g[s].get("players") or []:
                    if p.get("dnp") or not p.get("MIN") or float(p["MIN"]) < 15: continue
                    row = {st: _val(p, st) for st in STATS}
                    H = hist[p["id"]]
                    if len(H) >= 20:
                        hh = h2h[(p["id"], opp)]
                        if len(hh) >= min_h2h:
                            for st in STATS:
                                base = sum(r[st] for r in H[-20:]) / 20
                                if base < 1: continue
                                hm = sum(r[st] for r in hh) / len(hh)
                                X[st].append((math.log((hm + 0.5) / (base + 0.5)), math.log((row[st] + 0.5) / (base + 0.5))))
                    upd.append((p["id"], opp, row))
            for pid, opp, row in upd:
                hist[pid].append(row); h2h[(pid, opp)].append(row)
    out = {}
    for st in STATS:
        if len(X[st]) < 50: continue
        D = np.array(X[st]); A = np.column_stack([np.ones(len(D)), D[:, 0]]); z = D[:, 1]
        b, *_ = np.linalg.lstsq(A, z, rcond=None)
        res = z - A @ b
        se = float(np.sqrt(np.linalg.inv(A.T @ A)[1, 1] * np.var(res)))
        n = len(D)
        # retrait vers 0 selon la precision (poids nul si la pente n'est pas distincte de 0), borne [0 ; 0,5]
        w = max(0.0, min(0.5, float(b[1]) * (b[1] ** 2 / (b[1] ** 2 + se ** 2)) if b[1] > 0 else 0.0))
        out[st] = {"w": round(float(w), 3), "slope": round(float(b[1]), 3), "se": round(se, 3), "n": int(n)}
    return out


def refit(P, at):
    """Met a jour P["h2h_w"] (stats avec >= MIN_N cas) ; renvoie le rapport de la nuit."""
    m = measure()
    cur = dict(P.get("h2h_w") or {})
    for st, r in m.items():
        if r["n"] >= MIN_N: cur[st] = r["w"]
    cur["_at"] = at
    P["h2h_w"] = cur
    return {"at": at, "mesure": m}


def weight(stat, params=None):
    """Poids en service (FG3M = 3PM) ; DEFAULT si jamais mesure."""
    if params is None:
        try:
            import json
            from pathlib import Path
            params = json.loads(Path("data/model_params.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            params = {}
    w = (params.get("h2h_w") or {}).get({"FG3M": "3PM"}.get(stat, stat))
    return DEFAULT if w is None else float(w)


if __name__ == "__main__":
    import json, sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(measure(), indent=1))
