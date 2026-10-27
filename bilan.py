# -*- coding: utf-8 -*-
"""Bilan pondere par la mise.

Jusqu'ici le bilan comptait chaque pari pour une unite : un pari « grosse
confiance » et un pari « fun » pesaient pareil. C'est faux, et ca masque le
seul chiffre qui compte — ce que la methode rapporte reellement.

Ici, chaque pari pese sa mise (0,5 / 1 / 3 / 5 % du capital), et le gain est
calcule au prix reel quand il est connu.

    gain = mise x (cote - 1)   si gagne
    gain = - mise              si perdu
    gain = 0                   si annule (PUSH)

Deux precisions honnetes :
  - Les paris d'avant la regle n'avaient pas de mise. On la recalcule avec la
    regle actuelle : c'est donc « ce que la regle aurait donne », pas un
    releve de paris passes.
  - Quand la cote reelle est inconnue (77 % des cas depuis la panne du flux
    de cotes), le gain est calcule a la cote MOYENNE du marche. C'est une
    estimation, signalee comme telle, et jamais presentee comme un resultat.
"""
import calibrage
import selection


def mise_de(pick, sport, cal):
    """Mise que la regle actuelle aurait donnee a ce pari. 0 = pas propose."""
    e = selection.evaluer(pick, sport, cal)
    return (e["mise_pct"], e) if e else (0.0, None)


def calculer(picks, cal=None, capital=550.0):
    """Bilan pondere. `picks` = historique resolu."""
    cal = cal or calibrage.charger()
    r = {
        "n_total": 0, "n_joues": 0, "wins": 0, "losses": 0, "pushes": 0,
        "engage_pct": 0.0, "gain_pct": 0.0,
        "n_cote_reelle": 0, "n_cote_estimee": 0, "n_sans_cote": 0,
        "par_palier": {},
    }
    for p in picks:
        res = p.get("result")
        if res not in ("WIN", "LOSS", "PUSH"):
            continue
        r["n_total"] += 1
        sport = p.get("sport", "foot")
        mise, e = mise_de(p, sport, cal)
        if not mise:
            continue

        # Prix : reel d'abord, moyenne du marche ensuite. Jamais 1/confiance,
        # qui est une cote inventee par le moteur et rend le bilan circulaire.
        cote = p.get("cote")
        if cote and cote > 1.0:
            r["n_cote_reelle"] += 1
        else:
            cote = (e or {}).get("cote_reference")
            if cote:
                r["n_cote_estimee"] += 1
            else:
                r["n_sans_cote"] += 1
                continue  # sans aucun prix, le gain n'est pas calculable

        r["n_joues"] += 1
        r["engage_pct"] += mise
        lab = "%g %%" % mise
        d = r["par_palier"].setdefault(lab, {"n": 0, "w": 0, "gain": 0.0, "engage": 0.0})
        d["n"] += 1
        d["engage"] += mise
        if res == "WIN":
            r["wins"] += 1; d["w"] += 1
            g = mise * (cote - 1.0)
        elif res == "LOSS":
            r["losses"] += 1
            g = -mise
        else:
            r["pushes"] += 1
            g = 0.0
        r["gain_pct"] += g
        d["gain"] += g

    r["wr"] = (r["wins"] / (r["wins"] + r["losses"]) * 100) if (r["wins"] + r["losses"]) else 0.0
    r["roi_pct"] = (r["gain_pct"] / r["engage_pct"] * 100) if r["engage_pct"] else 0.0
    r["gain_eur"] = r["gain_pct"] / 100.0 * capital
    r["capital"] = capital
    return r
