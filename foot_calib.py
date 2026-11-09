"""
foot_calib.py — Recalage des probabilites affichees de la simulation foot (Wilson, 05/11/2026 : « améliorer », point 2).

Constat : sur l'historique reel (77 matchs), la simulation annoncait 60-80 % et l'evenement arrivait 53 % du temps.
Etude (backtests walk-forward data/foot_db/backtest_v4_2425.json et _all.json, parametres de production, 1 440 + 1 445
matchs du Top 5 avec composition officielle) :
  - plus de 2,5 buts et les deux marquent : TROP CONFIANTE (ecarts a 50 % trop grands) -> echelle logistique a < 1 ;
  - 1X2 : un peu trop PRUDENTE -> temperature t > 1 (et un leger bonus du nul).
Validation croisee : appris sur une saison, teste sur l'autre, dans les deux sens -> gain sur les 3 marches a chaque fois
(log-loss 1X2 1,0078 -> 1,0063 et 0,9744 -> 0,9735 ; +2,5 0,6794 -> 0,6792 et 0,6799 -> 0,6784 ; deux marquent
0,6842 -> 0,6837 et 0,6859 -> 0,6835). Sur l'historique reel, jamais vu a l'apprentissage : 1X2 0,9385 -> 0,9293,
+2,5 0,7005 -> 0,6893, deux marquent 0,7262 -> 0,7080 ; case 60-80 % : annonce 67 % / reel 53 % -> 65 % / 63 %.
Seuls ces 3 marches sont recales (les autres lignes de buts n'ont pas de backtest) ; les valeurs brutes restent dans
p_raw (archive et etudes futures, melange valide des picks du jour sur le +2,5).
Parametres appris sur les deux saisons (2 885 matchs).
"""
import math

T, D = 1.161, 0.032          # 1X2 : p_k ∝ p_k^T · e^(D si nul)
O25 = (0.727, 0.062)         # plus de 2,5 buts : logit' = a·logit + b
BTTS = (0.621, 0.073)        # les deux equipes marquent
EPS = 1e-6
try:   # parametres re-appris par improve_bot (memes donnees, apres un changement de reglage de la simulation)
    import json as _json
    _o = (_json.loads(open("data/model_params.json", encoding="utf-8").read()).get("foot_calib") or {})
    T, D = _o.get("T", T), _o.get("D", D)
    O25, BTTS = tuple(_o.get("O25", O25)), tuple(_o.get("BTTS", BTTS))
except (OSError, ValueError):
    pass


def _logit_scale(p, ab):
    p = min(max(p, EPS), 1 - EPS)
    z = ab[0] * math.log(p / (1 - p)) + ab[1]
    return 1 / (1 + math.exp(-z))


def apply(p):
    """Copie de p (format foot_sim_run : 1, X, 2, btts, over{...}) avec 1X2, +2,5 et les deux marquent recales."""
    q = dict(p)
    w = [max(p[k], EPS) ** T * (math.exp(D) if k == "X" else 1) for k in ("1", "X", "2")]
    s = sum(w)
    for k, v in zip(("1", "X", "2"), w):
        q[k] = round(v / s, 4)
    if "btts" in p: q["btts"] = round(_logit_scale(p["btts"], BTTS), 4)
    ov = dict(p.get("over") or {})
    if "2.5" in ov:
        o = _logit_scale(ov["2.5"], O25)
        lo, hi = ov.get("3.5", 0.0), ov.get("1.5", 1.0)   # reste coherent avec les lignes voisines (non recalees)
        ov["2.5"] = round(min(max(o, lo), hi), 4)
    q["over"] = ov
    return q


def sim_only(g):
    """Avis de la simulation SEULE pour un match (format de g["p"]) : depuis le 09/11/2026 les probabilites affichees
    d'un match ancre sur Pinnacle sont celles du marche (foot_sim_run.anchor) ; les filtres « la simulation est
    d'accord » (picks du jour, Bankroll IA, labo) gardent l'avis de la simulation, comme avant l'ancrage."""
    a = (g.get("anchor") or {}).get("sim")
    if not a: return g["p"]
    return {"1": a["1"], "X": a["X"], "2": a["2"], "btts": a["btts"], "over": dict(a.get("over") or {"2.5": a["o25"]})}


def ensure(g):
    """Recale une simulation archivee avant le 05/11/2026 (sans p_raw). Renvoie True si elle a change."""
    if "p_raw" in g or "p" not in g: return False
    g["p_raw"] = g["p"]
    g["p"] = apply(g["p"])
    return True
