# -*- coding: utf-8 -*-
"""Controle rapide de la recherche globale (site_search.py), sans navigateur ni reseau.

    python verifie_recherche.py              # page minimale + index.html publie s'il est la

Verifie : bouton dans le menu, panneau et script injectes une seule fois (idempotent), syntaxe JS (node --check si
node est installe), index des fiches joueurs Analyse lu depuis un fragment type. Code de sortie 1 si un controle echoue.
(Le test complet dans un navigateur — clics, defilement, telephone 360 px — a ete fait avec Playwright le 16/11/2026.)
"""
import os
import shutil
import subprocess
import sys
import tempfile

import site_search

ok = True


def check(cond, msg):
    global ok
    print(("OK    " if cond else "ECHEC ") + msg)
    ok = ok and bool(cond)


PAGE = ('<html><head></head><body><div class="container"><div class="sp-nav" style="display:flex">'
        '<button class="sport-btn" id="nav-today">A</button><button class="sport-btn" id="nav-iabank">B</button></div>'
        '<div id="sport-today"></div></div></body></html>')
out = site_search.inject(PAGE)
check(out.count('id="nav-search"') == 1, "bouton Rechercher ajoute dans le menu")
check('class="sp-nav sps-on"' in out, "menu marque pour la barre du bas a 5 onglets")
nav_end = out.index("</div>", out.index('class="sp-nav'))
check(out.index('id="nav-search"') < nav_end, "bouton a l'interieur du menu")
check(out.count('id="sps"') == 1 and out.count("window.spSearch = {") == 1, "panneau et script injectes")
check(site_search.inject(out) == out, "injection idempotente")
check(site_search.inject("<html><body></body></html>") == "<html><body></body></html>", "sans menu : page inchangee")

node = shutil.which("node")
if node:
    js = site_search.SCRIPT.replace("<script>", "").replace("</script>", "")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
    r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
    os.unlink(f.name)
    check(r.returncode == 0, "syntaxe JS (node --check)" + ("" if r.returncode == 0 else " : " + r.stderr[:300]))
else:
    print("--    node absent : syntaxe JS non verifiee")

FRAG = ('<div id="nba-history-block" style="display:none"><div><div class="nba-match-header" '
        'onclick="toggleNbaMatch(\'hist-1\')"><div><div class="a">📅 Hier</div><div class="b">Spurs <span class="c">@</span> Jazz'
        '</div></div><div class="d">2026-11-15</div></div><div id="nba-body-hist-1"><div class="nba-teams2">'
        '<div class="player-analyse" data-player-name="Keyonte George"></div></div></div></div></div>'
        '<div><div class="e">🏀 NBA</div><div class="f">Magic <span class="c">@</span> Celtics</div><div class="nba-teams2">'
        '<div class="player-analyse" data-player-name="Jayson Tatum"></div>'
        '<div class="player-analyse" data-player-name="Jayson Tatum"></div>'
        '<div class="player-analyse" data-player-name="Nikola Vučević"></div></div></div>')
ix = site_search.analyse_index(FRAG)
check(ix["g"] == [["Spurs @ Jazz · 15/11", 1], ["Magic @ Celtics", 0]], "matchs du fragment Analyse (historique date)")
check(ix["p"] == [["Keyonte George", 0], ["Jayson Tatum", 1], ["Nikola Vučević", 1]], "joueurs (doublons retires)")
check("</script>" not in site_search.analyse_script(FRAG)[:-len("</script>")], "index embarque sans </script> premature")

if os.path.exists("index.html"):
    page = open("index.html", encoding="utf-8").read()
    if site_search.MARK in page:
        check(page.count('id="nav-search"') == 1, "index.html : recherche presente")
        check(("data-src=" not in page) or ("window.SP_SEARCH_PLAYERS=" in page), "index.html : index des fiches joueurs")
    else:
        print("--    index.html genere avant la recherche (prochaine generation)")

print("TOUT OK" if ok else "ECHEC(S)")
sys.exit(0 if ok else 1)
