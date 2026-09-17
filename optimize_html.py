#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Allege index.html en extrayant les styles repetes dans des classes CSS.

Constat qui motive ce script (audit du 17/09/2026) :
    index.html            : 6,64 Mo
    attributs style=...   : 42 675 occurrences, 2,96 Mo, soit 45 % de la page
    valeurs distinctes    : 862  ->  98 % de doublons

Le generateur ecrit les styles element par element. C'est commode a la
generation, mais la meme chaine de 77 octets se retrouve mille fois dans la
page. Ce post-traitement remplace chaque style repete par une classe courte et
regroupe les regles dans le <style> existant.

Pourquoi en post-traitement plutot que dans generate_site.py : ce dernier fait
11 562 lignes et 110 fonctions. Un passage separe se teste, se mesure et
s'annule ; une refonte du generateur, non.

Deux formes de HTML coexistent dans la page, toutes deux traitees :
  - HTML normal          : <div style="...">
  - HTML encode en JSON  : <div style=\\"...\\">   (variables window._BK2_*)

Trois garde-fous avant toute ecriture :
  1. le texte visible doit etre rigoureusement identique ;
  2. la sequence des balises doit etre identique ;
  3. aucun attribut style ne doit avoir ete perdu sans contrepartie.

Usage :
    python optimize_html.py                 # index.html, en place
    python optimize_html.py --dry-run       # mesure sans rien ecrire
    python optimize_html.py --in a --out b
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

# En dessous de 2 occurrences, la regle CSS pese plus que le gain.
SEUIL = 2
PREFIXE = "s"
FORMES = ('\\"', '"')          # l'echappee d'abord : "style=\"" contient "style=\""
TAG = re.compile(r"<[a-zA-Z][^<>]*>")


def _attribut(balise: str, nom: str, q: str):
    """Localise nom=Q...Q dans une balise. Renvoie (debut, fin, valeur) ou None."""
    ouvre = nom + "=" + q
    i = balise.find(ouvre)
    if i < 0:
        return None
    j = balise.find(q, i + len(ouvre))
    if j < 0:
        return None
    return i, j + len(q), balise[i + len(ouvre):j]


def _forme_de(balise: str, nom: str):
    """Quelle forme de guillemet cette balise utilise-t-elle pour cet attribut ?"""
    for q in FORMES:
        if nom + "=" + q in balise:
            return q
    return None


def collecte(html: str) -> dict:
    """Compte les valeurs de style=, toutes formes confondues."""
    compte: dict = {}
    for m in TAG.finditer(html):
        balise = m.group(0)
        q = _forme_de(balise, "style")
        if not q:
            continue
        a = _attribut(balise, "style", q)
        if not a:
            continue
        v = a[2].strip()
        if len(v) >= 4:
            compte[v] = compte.get(v, 0) + 1
    return compte


def reecrire(html: str, classes: dict) -> tuple:
    """Retire les styles connus et pose la classe correspondante."""
    sortie = []
    curseur = 0
    faits = 0

    for m in TAG.finditer(html):
        balise = m.group(0)
        q = _forme_de(balise, "style")
        if not q:
            continue
        a = _attribut(balise, "style", q)
        if not a:
            continue
        cls = classes.get(a[2].strip())
        if cls is None:
            continue

        # 1. retrait de l'attribut style (et de l'espace qui le precede)
        d, f = a[0], a[1]
        while d > 0 and balise[d - 1] == " ":
            d -= 1
        nouvelle = balise[:d] + balise[f:]

        # 2. fusion dans class= s'il existe, sinon ajout avant le >
        qc = _forme_de(nouvelle, "class")
        ac = _attribut(nouvelle, "class", qc) if qc else None
        if ac:
            valeur = (ac[2] + " " + cls).strip()
            nouvelle = nouvelle[:ac[0]] + "class=" + qc + valeur + qc + nouvelle[ac[1]:]
        else:
            ferme = "/>" if nouvelle.rstrip().endswith("/>") else ">"
            nouvelle = nouvelle[:nouvelle.rfind(ferme)].rstrip() + " class=" + q + cls + q + ferme

        sortie.append(html[curseur:m.start()])
        sortie.append(nouvelle)
        curseur = m.end()
        faits += 1

    sortie.append(html[curseur:])
    return "".join(sortie), faits


# --------------------------------------------------------------- garde-fous
def texte_visible(html: str) -> str:
    s = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sequence_balises(html: str) -> list:
    return re.findall(r"<\s*(/?[a-zA-Z][a-zA-Z0-9]*)", html)


def optimiser(html: str) -> tuple:
    compte = collecte(html)
    retenus = sorted(
        ((v, n) for v, n in compte.items() if n >= SEUIL),
        key=lambda x: -(len(x[0]) * x[1]),
    )
    classes = {v: PREFIXE + format(i, "x") for i, (v, _) in enumerate(retenus)}

    html2, faits = reecrire(html, classes)

    regles = "".join("." + c + "{" + v.rstrip(";") + "}" for v, c in classes.items())
    if "</style>" in html2:
        html2 = html2.replace("</style>", regles + "</style>", 1)
    else:
        html2 = html2.replace("</head>", "<style>" + regles + "</style></head>", 1)

    return html2, {
        "distinctes": len(compte),
        "classes": len(classes),
        "remplacements": faits,
        "css": len(regles),
        "restants": len(collecte(html2)),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Allege index.html (styles repetes -> classes CSS)")
    p.add_argument("--in", dest="src", default="index.html")
    p.add_argument("--out", dest="dst", default=None)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    dst = a.dst or a.src

    if not os.path.exists(a.src):
        print("introuvable : " + a.src, file=sys.stderr)
        return 1

    avant = io.open(a.src, encoding="utf-8").read()
    apres, st = optimiser(avant)

    ok_texte = texte_visible(avant) == texte_visible(apres)
    ok_balises = sequence_balises(avant) == sequence_balises(apres)

    ga, gp = len(avant.encode()), len(apres.encode())
    print("  valeurs de style distinctes : %d" % st["distinctes"])
    print("  classes creees              : %d  (%d octets de CSS)" % (st["classes"], st["css"]))
    print("  attributs remplaces         : %d" % st["remplacements"])
    print("  styles restants en ligne    : %d valeurs (occurrences uniques, non extractibles)" % st["restants"])
    print()
    print("  texte visible identique     : %s" % ("oui" if ok_texte else "NON"))
    print("  sequence des balises        : %s" % ("identique" if ok_balises else "MODIFIEE"))
    print()
    print("  avant : %.2f Mo" % (ga / 1048576))
    print("  apres : %.2f Mo" % (gp / 1048576))
    print("  gain  : %.2f Mo  (%.1f %%)" % ((ga - gp) / 1048576, (ga - gp) / ga * 100))

    if not (ok_texte and ok_balises):
        print()
        print("ABANDON : un garde-fou a saute, rien n'a ete ecrit.", file=sys.stderr)
        return 2

    if a.dry_run:
        print()
        print("  (--dry-run : rien n'a ete ecrit)")
        return 0

    io.open(dst, "w", encoding="utf-8", newline="").write(apres)
    print()
    print("  ecrit : " + dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
