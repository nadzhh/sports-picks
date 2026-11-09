"""Calage des probabilites de props NBA (Wilson, 09/11/2026 : « c'est a l'algorithme de mieux faire son travail »).

La simulation seule est trop sure d'elle (selection du simulateur : 61 % annonces, 52 % gagnes) et le bookmaker
surcote les « plus de » (2025-26 : over 48,0 % reel pour 49,5 % sans marge ; 2026-27 : 47,5 % pour 49,5 %). Plutot que de
retirer des stats ou un cote, on apprend PAR STAT, sur les rejeux de la simulation contre les vraies lignes
(nba_backtest.py --blind, deux saisons) :

    logit p = logit(p_marche) + biais[stat] + poids[stat] x (logit(p_simu) - logit(p_marche))

(biais = ce que le marche sans marge rate en moyenne sur cette stat, poids = la part de l'avis de la simulation qui
apporte vraiment quelque chose). La ligne du marche garde un coefficient 1 : un coefficient libre devenait instable
(PRA 0,23 sur 2025-26). Mesures du 09/11 : appris sur 2025-26, teste sur 2026-27 log-loss 0,6847 contre 0,6857 pour le
marche et 0,6855 pour l'ancien melange fixe (marche + 10 % de l'ecart) ; sur les 966 paris reels de la selection du
simulateur (production, jamais vus) 0,6787 contre 0,6824 (marche) et 0,6812 (ancien melange).

Refait chaque nuit par improve_bot (data/nba_prop_calib.json), garde seulement s'il bat le marche hors echantillon.
"""
import json
import math
from pathlib import Path

PATH = Path("data/nba_prop_calib.json")
L2 = 5.0   # retrait vers 0 (aucune correction) des deux coefficients d'une stat : peu de donnees -> peu de correction
_COEF = None


def _logit(p):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def coefs():
    global _COEF
    if _COEF is None:
        try:
            _COEF = json.loads(PATH.read_text(encoding="utf-8")).get("coef") or {}
        except (OSError, ValueError):
            _COEF = {}
    return _COEF


def p_over(stat, p_sim, p_mkt):
    """Probabilite calee de l'over (stat inconnue : marche + biais moyen, sans avis de la simulation)."""
    c = coefs()
    a, w = c.get(stat) or c.get("_all") or (0.0, 0.0)
    z = _logit(p_mkt) + a + w * (_logit(p_sim) - _logit(p_mkt))
    return 1 / (1 + math.exp(-z))


def p_side(stat, p_sim_over, p_mkt_over, over):
    p = p_over(stat, p_sim_over, p_mkt_over)
    return p if over else 1 - p


# ---------------------------------------------------------------- apprentissage (numpy seulement ici)
def _fit_one(rows, l2=L2):
    import numpy as np
    pm = np.clip(np.array([r[1] for r in rows]), 1e-3, 1 - 1e-3); ps = np.clip(np.array([r[0] for r in rows]), 1e-3, 1 - 1e-3)
    y = np.array([float(r[2]) for r in rows])
    off = np.log(pm / (1 - pm)); X = np.c_[np.ones(len(rows)), np.log(ps / (1 - ps)) - off]
    w = np.zeros(2)
    for _ in range(50):
        p = 1 / (1 + np.exp(-(off + X @ w)))
        w -= np.linalg.solve((X.T * (p * (1 - p))) @ X + l2 * np.eye(2), X.T @ (p - y) + l2 * w)
    # poids >= 0 : une simulation sans apport sur une stat (PA, PRA en 2025-26) est ignoree, pas prise a contre-pied
    return [round(float(w[0]), 4), round(max(float(w[1]), 0.0), 4)]


def fit(rows):
    """rows : [(p_sim_over, p_mkt_over, over_reel, stat)] -> {stat: [biais, poids], "_all": [...]}."""
    by = {}
    for r in rows: by.setdefault(r[3], []).append(r)
    out = {s: _fit_one(R) for s, R in by.items() if len(R) >= 200}
    out["_all"] = _fit_one(rows)
    return out


def logloss(rows, coef=None):
    """(log-loss marche, log-loss calee) ; coef=None -> coefficients en service."""
    global _COEF
    keep = _COEF
    if coef is not None: _COEF = coef
    try:
        lm = lc = 0.0
        for ps, pm, y, st in rows:
            q = p_over(st, ps, pm); m = min(max(pm, 1e-4), 1 - 1e-4)
            lm -= math.log(m if y else 1 - m); lc -= math.log(q if y else 1 - q)
        return round(lm / len(rows), 5), round(lc / len(rows), 5)
    finally:
        _COEF = keep


def refit(old_rows, cur_rows, at, notes=None):
    """Apprend sur la saison passee, juge sur la saison en cours (hors echantillon) ; si le calage bat le marche,
    les coefficients de service sont refaits sur les deux saisons et ecrits. Renvoie le rapport."""
    global _COEF
    rep = {"at": at, "n": [len(old_rows), len(cur_rows)]}
    if len(old_rows) < 2000 or len(cur_rows) < 500:
        rep["note"] = "pas assez de props pour recaler"; return rep
    test = logloss(cur_rows, fit(old_rows))
    rep["test"] = {"marche": test[0], "cale": test[1]}
    if test[1] >= test[0]:
        rep["note"] = "le calage ne bat pas le marche hors echantillon : coefficients precedents gardes"; return rep
    coef = fit(old_rows + cur_rows)
    PATH.write_text(json.dumps({"at": at, "coef": coef, "test": rep["test"], "n": rep["n"]}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    _COEF = coef
    rep["coef"] = coef
    return rep


def rows_from_backtest(path):
    D = json.loads(Path(path).read_text(encoding="utf-8"))
    return [(r["p_sim"], r["p_mkt"], r["actual"] > r["line"], r["stat"]) for r in D.get("real") or []
            if r["actual"] != r["line"] and r.get("p_play", 1) >= 0.9]


if __name__ == "__main__":
    import sys
    from datetime import datetime, timezone
    old, cur = sys.argv[1], sys.argv[2]   # fichiers backtest_*.json : saison passee, saison en cours
    print(json.dumps(refit(rows_from_backtest(old), rows_from_backtest(cur),
                           datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M+00:00")), ensure_ascii=False, indent=1))
