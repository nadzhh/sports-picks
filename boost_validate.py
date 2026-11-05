"""
boost_validate.py — Verifie boost_model sur l'historique : modele cale sur la cloture Pinnacle (1X2 + plus/moins 2,5),
probabilites des paris speciaux typiques des boosts comparees aux frequences reelles (Top 5, 2024-25 et 2025-26).
Mesure aussi F1 (part des buts en 1re mi-temps) et RHO sur la base. Usage : python boost_validate.py
"""
import difflib, math, sys
from collections import defaultdict

import foot_db
import boost_model as BM
from foot_backtest import load_odds, _norm, FD


def f(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def fair(*o):
    if not all(o): return None
    s = sum(1 / x for x in o); return [1 / x / s for x in o]


def matched():
    D = foot_db.load_all()
    by = defaultdict(list)
    for r in D.values():
        if r["league"] in FD and r.get("ht") and r.get("score"): by[(r["league"], r["season"])].append(r)
    out = []
    for (lid, season), recs in by.items():
        try: rows = load_odds(lid, season)
        except Exception: continue
        names = {}
        for r in recs:
            for s in ("home", "away"): names[r[s]["id"]] = r[s]["name"]
        fd = sorted({x["HomeTeam"] for x in rows} | {x["AwayTeam"] for x in rows})
        m = {}
        for tid, nm in names.items():
            m[max(fd, key=lambda z: difflib.SequenceMatcher(None, _norm(nm), _norm(z)).ratio()
                  + (0.3 if _norm(z) in _norm(nm) or _norm(nm) in _norm(z) else 0))] = tid
        idx = {}
        for x in rows:
            try:
                d, mo, y = x["Date"].split("/"); y = int(y) + (2000 if len(y) == 2 else 0)
                day = __import__("datetime").date(y, int(mo), int(d)).toordinal()
            except Exception: continue
            h, a = m.get(x["HomeTeam"]), m.get(x["AwayTeam"])
            if h and a: idx[(day, h, a)] = x
        for r in recs:
            day = __import__("datetime").date.fromisoformat(r["date"][:10]).toordinal()
            x = idx.get((day, r["home"]["id"], r["away"]["id"])) or idx.get((day - 1, r["home"]["id"], r["away"]["id"]))
            if not x: continue
            p = fair(f(x.get("PSCH")), f(x.get("PSCD")), f(x.get("PSCA")))
            o = fair(f(x.get("PC>2.5")), f(x.get("PC<2.5")))
            if p and o: out.append((r, p, o[0]))
    return out


SPECIALS = {
    "dom gagne et 2 marquent": lambda a, b, c, d: a + c > b + d and b + d > 0 and a + c > 0,
    "ext gagne et 2 marquent": lambda a, b, c, d: b + d > a + c and b + d > 0 and a + c > 0,
    "dom marque 2+": lambda a, b, c, d: a + c >= 2,
    "ext marque 2+": lambda a, b, c, d: b + d >= 2,
    "dom gagne 1re MT": lambda a, b, c, d: a > b,
    "ext gagne 1re MT": lambda a, b, c, d: b > a,
    "dom gagne les 2 MT": lambda a, b, c, d: a > b and c > d,
    "ext gagne les 2 MT": lambda a, b, c, d: b > a and d > c,
    "dom gagne a 0": lambda a, b, c, d: a + c > 0 and b + d == 0,
    "2 marquent et +2,5": lambda a, b, c, d: a + c > 0 and b + d > 0 and a + b + c + d > 2,
    "but dans chaque MT": lambda a, b, c, d: a + b > 0 and c + d > 0,
    "+1,5 en 1re MT": lambda a, b, c, d: a + b > 1,
}


def main():
    M = matched()
    print("matchs avec cloture Pinnacle et score MT :", len(M))
    g1 = sum(r["ht"][0] + r["ht"][1] for r, _, _ in M); gt = sum(r["score"][0] + r["score"][1] for r, _, _ in M)
    print(f"part des buts en 1re mi-temps : {g1 / gt:.3f} (modele F1 = {BM.F1})")
    res = {k: [] for k in SPECIALS}
    for r, p, o in M:
        lh, la = BM.fit(p, (2.5, o))
        mm = BM.Match(lh, la)
        a, b = r["ht"]; c, d = r["score"][0] - a, r["score"][1] - b
        for k, pred in SPECIALS.items():
            res[k].append((mm.prob(pred), pred(a, b, c, d)))
    print(f"\n{'pari special':26s} {'annonce':>8s} {'reel':>6s} {'ecart':>6s} {'+/-95%':>7s}  Brier modele / naif")
    worst = 0
    for k, L in res.items():
        n = len(L); mp = sum(p for p, _ in L) / n; ob = sum(o for _, o in L) / n
        ci = 1.96 * math.sqrt(ob * (1 - ob) / n)
        brier = sum((p - o) ** 2 for p, o in L) / n; naive = sum((ob - o) ** 2 for _, o in L) / n
        worst = max(worst, abs(mp - ob) / max(ci, 1e-9))
        print(f"{k:26s} {100 * mp:7.1f} % {100 * ob:5.1f} % {100 * (ob - mp):+5.1f} {100 * ci:6.1f}   {brier:.4f} / {naive:.4f}")
    # calibration par tranches (tous speciaux confondus)
    allp = [x for L in res.values() for x in L]
    print("\ncalibration (tous speciaux) :")
    for lo in (0, .1, .2, .3, .4, .5, .6):
        B = [(p, o) for p, o in allp if lo <= p < lo + .1]
        if len(B) >= 50:
            print(f"  {int(100 * lo):2d}-{int(100 * lo) + 10:2d} % : annonce {100 * sum(p for p, _ in B) / len(B):5.1f} % · reel "
                  f"{100 * sum(o for _, o in B) / len(B):5.1f} % (n={len(B)})")
    return res


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
