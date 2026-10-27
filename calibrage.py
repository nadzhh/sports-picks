# -*- coding: utf-8 -*-
"""Construit data/calibration.json a partir de l'historique resolu.

Pourquoi ce module existe
─────────────────────────
Le score de confiance produit par les moteurs n'est PAS une probabilite. Mesure
faite le 17/09/2026 sur 1 540 paris resolus : les paris annonces a 70-79 %
gagnent 53,6 % du temps, ceux a 90-100 % gagnent 60,7 %. L'ecart va de 16 a
34 points selon la tranche.

Pire, la courbe n'est pas monotone (70-74 -> 47,5 % mais 75-79 -> 70,3 %).
Ce n'est pas du bruit : c'est un effet de composition. La tranche 70-74 est
remplie de « Double Chance Buteur » (44 % de reussite), la tranche 75-79 de
marches « Tirs » (77-87 %). Le score n'a donc pas le meme sens d'un marche a
l'autre — d'ou une calibration PAR MARCHE.

Ce que produit ce module
────────────────────────
  marches[sport/marche] = { n, wins, wr, borne_basse, borne_haute, statut }
      statut RETENU    : borne basse >= 50 %  -> mises normales
      statut SURVEILLE : pas assez de recul   -> mise minimale, on continue
                                                 d'apprendre sans risquer gros
      statut EXCLU     : borne haute < 50 %   -> perte demontree, on arrete

  calibration[sport/marche][tranche] = probabilite reelle observee
      sert a convertir un score en probabilite utilisable (mise, valeur).

Usage :
    python calibrage.py            # regenere data/calibration.json
    python calibrage.py --montrer  # affiche sans ecrire
"""
import io
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime

HIST = "data/all_picks_history.json"
SORTIE = "data/calibration.json"

# Un marche n'est juge qu'a partir de ce nombre de paris resolus.
N_MIN_JUGEMENT = 20
# En dessous de ce taux, un marche perd de l'argent quelle que soit la cote
# raisonnable : on s'en sert comme frontiere.
SEUIL = 50.0
# Repli quand un marche n'a pas encore d'historique dans sa tranche.
PAS_TRANCHE = 10
# Bornes de probabilite. Aucun pari n'est certain, aucun n'est nul.
P_MIN, P_MAX = 5.0, 90.0


def wilson(w, n, z=1.96):
    """Intervalle de confiance a 95 %. Sans lui, on prend 6 gagnants sur 10
    pour un bon marche."""
    if n == 0:
        return (0.0, 0.0)
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    e = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - e) / d, (c + e) / d)


def cle(sport, marche):
    return "%s/%s" % (sport, marche)


def tranche(conf):
    if conf is None:
        return "na"
    c = max(0, min(99, int(conf)))
    return "%d-%d" % (c // PAS_TRANCHE * PAS_TRANCHE, c // PAS_TRANCHE * PAS_TRANCHE + PAS_TRANCHE - 1)


def construire(picks):
    resolus = [p for p in picks if p.get("result") in ("WIN", "LOSS")]

    # ── 1. Jugement par marche
    par_marche = defaultdict(lambda: {"n": 0, "w": 0})
    for p in resolus:
        d = par_marche[cle(p.get("sport", "?"), p.get("market", "?"))]
        d["n"] += 1
        d["w"] += 1 if p["result"] == "WIN" else 0

    # Cote moyenne reellement observee par marche. Indispensable : 77 % des
    # paris arrivent sans cote, et sans cote on ne peut pas juger de la valeur.
    # La moyenne historique du marche donne une estimation honnete, fondee sur
    # des mesures et non sur une hypothese.
    cotes = defaultdict(list)
    for p in resolus:
        if p.get("cote"):
            cotes[cle(p.get("sport", "?"), p.get("market", "?"))].append(p["cote"])

    marches = {}
    for k, d in par_marche.items():
        n, w = d["n"], d["w"]
        bi, bs = wilson(w, n)
        if n < N_MIN_JUGEMENT:
            statut = "SURVEILLE"
        elif bi * 100 >= SEUIL:
            statut = "RETENU"
        elif bs * 100 < SEUIL:
            statut = "EXCLU"
        else:
            statut = "SURVEILLE"
        lc = cotes.get(k, [])
        marches[k] = {
            "n": n, "wins": w,
            "wr": round(w / n * 100, 1),
            "borne_basse": round(bi * 100, 1),
            "borne_haute": round(bs * 100, 1),
            "statut": statut,
            "cote_moyenne": round(sum(lc) / len(lc), 2) if lc else None,
            "n_cotes": len(lc),
        }

    # ── 2. Calibration par marche ET par tranche de score
    par_tranche = defaultdict(lambda: defaultdict(lambda: {"n": 0, "w": 0}))
    for p in resolus:
        k = cle(p.get("sport", "?"), p.get("market", "?"))
        t = tranche(p.get("confidence"))
        par_tranche[k][t]["n"] += 1
        par_tranche[k][t]["w"] += 1 if p["result"] == "WIN" else 0

    calibration = {}
    for k, tr in par_tranche.items():
        base = marches[k]["wr"]
        entree = {}
        for t, d in tr.items():
            n, w = d["n"], d["w"]
            if n >= 10:
                # assez de recul dans cette tranche : on prend la mesure
                p_cal = w / n * 100
            elif n > 0:
                # trop peu : on tire vers le taux global du marche
                poids = n / 10.0
                p_cal = (w / n * 100) * poids + base * (1 - poids)
            else:
                p_cal = base
            # Bornage : une tranche ou tout a gagne donnerait 100 %, ce qui est
            # absurde et ferait exploser les mises. On plafonne a 90 % et on
            # plancher a 5 % — aucun pari sportif n'est certain.
            p_cal = max(P_MIN, min(P_MAX, p_cal))
            entree[t] = {"n": n, "p": round(p_cal, 1)}
        calibration[k] = entree

    # ── 3. Repli par sport, pour un marche inconnu
    par_sport = defaultdict(lambda: {"n": 0, "w": 0})
    for p in resolus:
        d = par_sport[p.get("sport", "?")]
        d["n"] += 1
        d["w"] += 1 if p["result"] == "WIN" else 0
    replis = {s: round(d["w"] / d["n"] * 100, 1) for s, d in par_sport.items() if d["n"]}

    return {
        "genere_le": datetime.now().isoformat(timespec="seconds"),
        "n_resolus": len(resolus),
        "parametres": {"n_min_jugement": N_MIN_JUGEMENT, "seuil_pct": SEUIL, "pas_tranche": PAS_TRANCHE},
        "marches": marches,
        "calibration": calibration,
        "replis_sport": replis,
    }


# ─────────────────────────────────────────────── lecture, cote consommateur
_cache = None


def charger(chemin=SORTIE):
    global _cache
    if _cache is None:
        try:
            _cache = json.load(io.open(chemin, encoding="utf-8"))
        except Exception:
            _cache = {"marches": {}, "calibration": {}, "replis_sport": {}}
    return _cache


def statut_marche(sport, marche, cal=None):
    """RETENU / SURVEILLE / EXCLU — SURVEILLE par defaut (prudent)."""
    cal = cal or charger()
    return (cal.get("marches", {}).get(cle(sport, marche), {}) or {}).get("statut", "SURVEILLE")


def borner(p):
    """Aucun pari sportif n'est certain. Un marche a 100 % sur son historique
    reste un marche a 90 % au plus — sans quoi il justifierait une mise maximale
    et une valeur infinie. Applique a TOUS les chemins, table comme replis :
    c'est par les replis que le 100 % passait."""
    return max(P_MIN, min(P_MAX, p))


def probabilite(sport, marche, confidence, cal=None):
    """Convertit un score de confiance en probabilite reelle observee (%)."""
    cal = cal or charger()
    k = cle(sport, marche)
    tr = cal.get("calibration", {}).get(k)
    if tr:
        e = tr.get(tranche(confidence))
        if e:
            return borner(e["p"])
    m = cal.get("marches", {}).get(k)
    if m:
        return borner(m["wr"])
    return borner(cal.get("replis_sport", {}).get(sport, 50.0))


if __name__ == "__main__":
    if not os.path.exists(HIST):
        print("historique introuvable : %s" % HIST, file=sys.stderr)
        raise SystemExit(1)
    picks = json.load(io.open(HIST, encoding="utf-8")).get("picks", [])
    d = construire(picks)

    ordre = {"RETENU": 0, "SURVEILLE": 1, "EXCLU": 2}
    print("Calibration sur %d paris resolus" % d["n_resolus"])
    print()
    print("  %-34s %5s %7s %-16s %s" % ("marche", "n", "taux", "intervalle 95%", "statut"))
    for k, m in sorted(d["marches"].items(), key=lambda x: (ordre[x[1]["statut"]], -x[1]["n"])):
        if m["n"] < 5:
            continue
        print("  %-34s %5d %6.1f%% [%5.1f – %5.1f]  %s"
              % (k[:34], m["n"], m["wr"], m["borne_basse"], m["borne_haute"], m["statut"]))
    print()
    for s in ("RETENU", "SURVEILLE", "EXCLU"):
        n = sum(1 for m in d["marches"].values() if m["statut"] == s)
        paris = sum(m["n"] for m in d["marches"].values() if m["statut"] == s)
        print("  %-10s : %2d marches, %4d paris historiques" % (s, n, paris))

    if "--montrer" in sys.argv:
        print()
        print("(--montrer : rien n'a ete ecrit)")
    else:
        io.open(SORTIE, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=1))
        print()
        print("  ecrit : %s" % SORTIE)
