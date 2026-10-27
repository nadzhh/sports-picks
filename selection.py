# -*- coding: utf-8 -*-
"""Selection des paris proposes et calcul de la mise.

Ce module est le filtre unique place entre le moteur d'analyse et l'affichage.
Le moteur continue de produire tout ce qu'il sait produire ; c'est ici qu'on
decide ce qui merite d'etre propose, et avec quelle mise.

Quatre regles, dans l'ordre
───────────────────────────
1. ON EXCLUT les marches dont la perte est demontree. Mesure du 17/09/2026 :
   « Buteur » 31,1 % de reussite sur 351 paris, « Double Chance Buteur » 41,8 %
   sur 275, « Double buteur » 18,2 % sur 22. A eux trois, 648 paris historiques
   et 48 % du volume football — ils tiraient tout le reste vers le bas.

2. ON TRADUIT le score en probabilite reelle. Le score des moteurs n'est pas
   une probabilite : annonce a 70-79 %, il gagne 53,6 % du temps. Et il ne veut
   pas dire la meme chose selon le marche, d'ou une table par marche
   (voir calibrage.py).

3. ON COMPARE a la cote quand elle est connue. Un pari a 74 % vaut la mise a
   2,50 et la detruit a 1,20. Sans cette comparaison, on selectionne sur la
   conviction au lieu de la valeur — c'etait le defaut de fond.

4. ON EN GARDE TROIS AU PLUS par match, les meilleurs.

Les mises suivent la confiance reelle : 5 %, 3 %, 1 % ou 0,5 % du capital.
Un marche encore « SURVEILLE » est plafonne a 1 % : on continue d'apprendre
sur lui sans risquer gros.

Validation temporelle (marches choisis sur le passe, mesures sur la suite) :
    mai -> juin    : 43,5 %  sans regle   ->  70,5 % avec
    mai+juin -> juil: 46,1 %  sans regle   ->  88,2 % avec
"""
import calibrage

MAX_PAR_MATCH = 3

# Paliers de mise, en % du capital. Ordre decroissant.
PALIER_5, PALIER_3, PALIER_1, PALIER_05 = 5.0, 3.0, 1.0, 0.5

# Seuils de probabilite reelle (et non de score affiche) pour chaque palier.
SEUIL_5 = 70.0
SEUIL_3 = 62.0
SEUIL_1 = 55.0

# En dessous, un pari n'est pas propose du tout.
PLANCHER = 45.0

# Valeur minimale exigee quand la cote est connue : sous ce seuil, le pari ne
# rapporte pas assez pour compenser sa variance.
VALEUR_MIN = 0.02

# Nombre de cotes reelles exigees pour oser une cote MOYENNE de marche.
N_COTES_MIN = 10


def _marche(pick):
    """Le moteur nomme le marche « type », l'historique le nomme « market »."""
    return pick.get("type") or pick.get("market") or "?"


def evaluer(pick, sport, cal=None):
    """Annote un pari : probabilite reelle, valeur, palier de mise.

    Renvoie None si le pari ne doit pas etre propose.
    """
    cal = cal or calibrage.charger()
    marche = _marche(pick)
    statut = calibrage.statut_marche(sport, marche, cal)
    if statut == "EXCLU":
        return None

    p_cal = calibrage.probabilite(sport, marche, pick.get("confidence"), cal)
    if p_cal < PLANCHER:
        return None

    # Valeur = probabilite reelle x cote - 1. Sans cote precise, on utilise la
    # cote MOYENNE historique du marche : c'est une mesure, pas une hypothese.
    cote = pick.get("cote")
    cote_estimee = False
    if not (cote and cote > 1.0):
        m = (cal.get("marches", {}) or {}).get(calibrage.cle(sport, marche), {})
        c = m.get("cote_moyenne")
        if c and m.get("n_cotes", 0) >= N_COTES_MIN:
            cote, cote_estimee = c, True
    valeur = ((p_cal / 100.0) * cote - 1.0) if (cote and cote > 1.0) else None

    # Palier de base, sur la probabilite reelle
    if p_cal >= SEUIL_5:
        mise = PALIER_5
    elif p_cal >= SEUIL_3:
        mise = PALIER_3
    elif p_cal >= SEUIL_1:
        mise = PALIER_1
    else:
        mise = PALIER_05

    # Un marche pas encore prouve reste a petite mise, quelle que soit sa
    # probabilite apparente : c'est la que se cachent les faux positifs.
    if statut != "RETENU":
        mise = min(mise, PALIER_1)

    # REGLE DE FOND : une grosse mise se justifie par le PRIX, pas par la
    # conviction. Le moteur misait gros sur des paris dont il ignorait la cote.
    #
    # Trois cas, et la difference entre les deux derniers est essentielle :
    #
    #  a) cote REELLE connue -> la valeur est mesurable. Elle peut faire monter
    #     au palier maximal comme faire descendre au minimal.
    #
    #  b) cote ESTIMEE (moyenne du marche) -> elle ne vaut QUE pour retrograder.
    #     La moyenne d'un marche est calculee sur les paris qui ont une cote
    #     enregistree, pas sur celui qu'on evalue : croiser « Victoire domicile
    #     a 2,25 en moyenne » avec « ce pari-ci a 75 % » produit un avantage de
    #     +69 %, qui est un artefact de composition, pas un avantage. En
    #     revanche, si meme la cote moyenne rend le pari perdant, c'est un
    #     signal fiable — on le garde.
    #
    #  c) aucune cote -> on s'appuie sur la seule chose honnetement mesuree,
    #     la probabilite calibree, et on plafonne a 3 %.
    if cote_estimee or valeur is None:
        mise = min(mise, PALIER_3)
        if valeur is not None and valeur < 0:
            # Perdant meme au prix moyen du marche : signal fiable.
            mise = PALIER_05
    elif valeur < VALEUR_MIN:
        mise = PALIER_05
    else:
        if mise >= PALIER_5 and valeur < 0.05:
            mise = PALIER_3
        if mise >= PALIER_3 and valeur < VALEUR_MIN:
            mise = PALIER_1

    pick = dict(pick)
    pick["p_cal"] = round(p_cal, 1)
    pick["statut_marche"] = statut
    pick["mise_pct"] = mise
    pick["mise_label"] = ("%g %%" % mise)
    pick["valeur_pct"] = round(valeur * 100, 1) if valeur is not None else None
    pick["cote_estimee"] = cote_estimee
    if cote_estimee:
        pick["cote_reference"] = cote
    # Ecart entre ce que le moteur annonce et la realite mesuree : utile a
    # l'affichage pour ne plus mentir a l'utilisateur.
    if pick.get("confidence") is not None:
        pick["ecart_calibration"] = round(p_cal - pick["confidence"], 1)
    return pick


def _rang(p):
    """Meilleur d'abord : grosse mise, puis valeur, puis probabilite."""
    return (-p["mise_pct"], -(p.get("valeur_pct") if p.get("valeur_pct") is not None else -1), -p["p_cal"])


def selectionner(sport, team_picks, home_pp, away_pp, fun_picks, maxi=MAX_PAR_MATCH, cal=None):
    """Filtre et classe les paris d'UN match. Renvoie les quatre listes,
    ne contenant que les paris retenus, annotes et limites a `maxi` au total."""
    cal = cal or calibrage.charger()
    listes = {"team": team_picks or [], "home": home_pp or [], "away": away_pp or [], "fun": fun_picks or []}

    candidats = []
    for origine, lst in listes.items():
        for p in lst:
            e = evaluer(p, sport, cal)
            if e is not None:
                candidats.append((origine, e))

    candidats.sort(key=lambda x: _rang(x[1]))
    retenus = candidats[:maxi]

    sortie = {"team": [], "home": [], "away": [], "fun": []}
    for origine, p in retenus:
        sortie[origine].append(p)
    return sortie["team"], sortie["home"], sortie["away"], sortie["fun"]


def allouer_journee(matchs, cap_pct=10.0):
    """Classe les paris du jour et signale ou s'arrete le budget raisonnable.

    Le palier de chaque pari n'est PAS modifie : il dit ce que ce pari vaut,
    pris isolement, et c'est l'information utile. Mais vingt paris a 5 %
    engageraient tout le capital en un jour — on indique donc le rang de chaque
    pari, le cumul engage, et a partir d'ou on depasse le plafond.

    L'utilisateur reste maitre : il voit tout, et sait ou tracer la ligne.
    """
    tous = []
    for m in matchs:
        for lst in ("picks", "home_players", "away_players", "fun_picks"):
            tous.extend(m.get(lst, []))
    if not tous:
        return matchs

    tous.sort(key=_rang)
    cumul = 0.0
    for i, p in enumerate(tous, 1):
        cumul += p.get("mise_pct", 0.0)
        p["rang_jour"] = i
        p["cumul_pct"] = round(cumul, 2)
        p["hors_budget"] = cumul > cap_pct
    return matchs


def resume(matchs):
    """Statistiques de la selection, pour le journal du pipeline."""
    n_m = len(matchs)
    n_p = sum(len(m.get("picks", [])) + len(m.get("home_players", []))
              + len(m.get("away_players", [])) + len(m.get("fun_picks", [])) for m in matchs)
    sans = sum(1 for m in matchs if m.get("no_picks"))
    paliers = {}
    for m in matchs:
        for lst in ("picks", "home_players", "away_players", "fun_picks"):
            for p in m.get(lst, []):
                k = p.get("mise_label", "?")
                paliers[k] = paliers.get(k, 0) + 1
    detail = " · ".join("%s : %d" % (k, v) for k, v in sorted(paliers.items(), reverse=True))
    return "%d matchs, %d paris retenus (%d matchs sans pari)%s" % (
        n_m, n_p, sans, ("  [" + detail + "]") if detail else "")
