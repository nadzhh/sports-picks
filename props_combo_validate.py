"""
props_combo_validate.py — Peut-on deduire une ligne REB+AST (absente de DraftKings via ESPN) des lignes rebonds et
passes ? Verification sur la saison 2025-26 (lignes principales DraftKings sans la marge + feuilles de match).

Methode testee : moyenne de chaque stat retrouvee a partir de sa ligne et de sa probabilite sans marge (loi de Poisson
ou binomiale negative), puis somme des deux. Ligne REB+AST de test = la demi-valeur la plus proche de la mediane
(comme un bookmaker). Comparaison des probabilites annoncees aux frequences reelles, par tranche ; dispersion apprise
sur la 1re moitie de la saison, testee sur la 2nde. Controle : la meme methode appliquee a PTS+REB, PTS+AST et PRA,
comparee aux VRAIES lignes DraftKings de ces marches (sans la marge).
Usage : python props_combo_validate.py
"""
import math, sys
from collections import defaultdict

import nba_boxdb as B
import nba_props_hist as H


def nb_sf(k, mu, r):
    """P(X > k) pour X ~ binomiale negative (moyenne mu, dispersion r ; r tres grand = Poisson)."""
    if r is None or r > 1e5:
        p, s = math.exp(-mu), 0.0
        for i in range(int(k) + 1):
            if i: p *= mu / i
            s += p
        return max(0.0, 1 - s)
    q = r / (r + mu)
    p, s = q ** r, 0.0
    for i in range(int(k) + 1):
        if i: p *= (i - 1 + r) / i * (1 - q)
        s += p
    return max(0.0, 1 - s)


def mean_from(line, p_over, r):
    lo, hi = 0.01, 80.0
    for _ in range(60):
        m = (lo + hi) / 2
        if nb_sf(math.floor(line), m, r) < p_over: lo = m
        else: hi = m
    return (lo + hi) / 2


def samples(year):
    props, box = H.load_props(year) or {}, B.load_season(year)
    out = []
    for gid, gp in props.items():
        g = box.get(str(gid))
        if not g: continue
        rows = {r["id"]: r for s in ("home", "away") for r in (g.get(s) or {}).get("players") or [] if not r.get("dnp") and r.get("MIN")}
        main = H.props_main_lines(gp)
        by = defaultdict(dict)
        for (aid, stat), v in main.items(): by[aid][stat] = v
        for aid, S in by.items():
            if aid in rows and "REB" in S and "AST" in S:
                out.append((g["date"], S, rows[aid]))
    return sorted(out, key=lambda x: x[0])


def derived(S, a, b, r):
    mu = mean_from(S[a]["line"], S[a]["p_over_novig"], r[a]) + mean_from(S[b]["line"], S[b]["p_over_novig"], r[b])
    return mu


def calib(rows, r, name="REB+AST"):
    pairs = []
    for _, S, act in rows:
        mu = derived(S, "REB", "AST", r)
        rr = None if r["REB"] is None else (r["REB"] + r["AST"])   # dispersion de la somme (approximation)
        line = math.floor(mu) + 0.5 if mu % 1 >= 0.5 else math.floor(mu) - 0.5 + 1
        p = nb_sf(math.floor(line), mu, rr)
        pairs.append((p, act["REB"] + act["AST"] > line))
    n = len(pairs); mp = sum(p for p, _ in pairs) / n; ob = sum(o for _, o in pairs) / n
    brier = sum((p - o) ** 2 for p, o in pairs) / n
    print(f"  {name} : {n} cas · annonce {100 * mp:.1f} % · reel {100 * ob:.1f} % · Brier {brier:.4f}")
    for lo in (0.3, 0.4, 0.5, 0.6):
        Bn = [(p, o) for p, o in pairs if lo <= p < lo + 0.1]
        if len(Bn) >= 30:
            print(f"     {int(100 * lo)}-{int(100 * lo) + 10} % : annonce {100 * sum(p for p, _ in Bn) / len(Bn):.1f} % · reel "
                  f"{100 * sum(o for _, o in Bn) / len(Bn):.1f} % (n={len(Bn)})")
    return abs(mp - ob)


def control(rows, r):
    """Meme methode sur PTS+REB / PTS+AST / PRA : ecart a la VRAIE probabilite DraftKings sans marge du combine."""
    for combo, parts in (("PR", ("PTS", "REB")), ("PA", ("PTS", "AST")), ("PRA", ("PTS", "REB", "AST"))):
        d = []
        for _, S, _ in rows:
            if combo not in S or not all(p in S for p in parts): continue
            mu = sum(mean_from(S[p]["line"], S[p]["p_over_novig"], r[p]) for p in parts)
            rr = sum(r[p] for p in parts) if all(r[p] for p in parts) else None
            d.append(nb_sf(math.floor(S[combo]["line"]), mu, rr) - S[combo]["p_over_novig"])
        if d:
            print(f"  controle {combo} : {len(d)} cas · ecart moyen deduit - DraftKings {100 * sum(d) / len(d):+.1f} pts · "
                  f"ecart absolu moyen {100 * sum(abs(x) for x in d) / len(d):.1f} pts")


def main():
    y = B.season_year() - 1
    rows = samples(y)
    print(f"saison {y} : {len(rows)} joueurs-matchs avec lignes rebonds et passes")
    half = len(rows) // 2
    print("Poisson (sans dispersion) :")
    calib(rows, {"REB": None, "AST": None, "PTS": None})
    best = None
    for rv in (4, 6, 8, 12, 20, 40):
        err = calib(rows[:half], {"REB": rv, "AST": rv, "PTS": rv}, f"dispersion r={rv} (1re moitie)")
        if best is None or err < best[0]: best = (err, rv)
    rv = best[1]
    print(f"dispersion retenue r={rv} -> test sur la 2nde moitie :")
    calib(rows[half:], {"REB": rv, "AST": rv, "PTS": rv}, "REB+AST (2nde moitie)")
    control(rows, {"REB": rv, "AST": rv, "PTS": 8})
    return rv


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
