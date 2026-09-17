# -*- coding: utf-8 -*-
"""Controle d'integrite apres optimize_html.py, sans echappement shell.

Verifie que CHAQUE balise dont le style a ete retire porte bien une classe,
dans les deux formes de HTML (normale et encodee en JSON).
"""
import io
import re
import sys

src = sys.argv[1] if len(sys.argv) > 1 else "index.html"
ref = sys.argv[2] if len(sys.argv) > 2 else None
s = io.open(src, encoding="utf-8").read()

print("fichier : %s  (%.2f Mo)" % (src, len(s.encode()) / 1048576))
print()

# --- classes posees, par forme
n_norm = len(re.findall(r'class="[^"]*\bs[0-9a-f]+\b', s))
n_json = len(re.findall(r'class=\\"[^"\\]*\bs[0-9a-f]+\b', s))
print("classes posees")
print("  HTML normal        : %d" % n_norm)
print("  HTML encode (JSON) : %d" % n_json)
print()

# --- styles restants, par forme
st_norm = re.findall(r'(?<!\\)style="([^"]+)"', s)
st_json = re.findall(r'style=\\"([^"\\]+)\\"', s)
print("styles encore en ligne")
print("  HTML normal        : %d occurrences, %d valeurs distinctes" % (len(st_norm), len(set(st_norm))))
print("  HTML encode (JSON) : %d occurrences, %d valeurs distinctes" % (len(st_json), len(set(st_json))))
print()

# --- le point critique : des balises sans style ET sans classe dans le JSON ?
bal_json = re.findall(r'<[a-zA-Z][^<>]*>', s)
sans_rien = 0
for b in bal_json:
    if "\\\"" not in b:
        continue
    if "style=" not in b and "class=" not in b and re.match(r"<(div|span|td|tr|table|details|summary)\b", b):
        sans_rien += 1
print("balises JSON sans style ni classe (div/span/td/...) : %d" % sans_rien)

# --- regles CSS orphelines
regles = set(re.findall(r"\.(s[0-9a-f]+)\{", s))
utilisees = set()
for a in re.findall(r'class=\\?"([^"\\]+)', s):
    utilisees.update(a.split())
orph = regles - utilisees
print("regles CSS definies : %d, jamais referencees : %d" % (len(regles), len(orph)))
if orph:
    print("  exemples : %s" % ", ".join(sorted(orph)[:8]))
print()

# --- comparaison au fichier de reference, si fourni
if ref:
    r = io.open(ref, encoding="utf-8").read()
    vis = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ",
        re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I))).strip()
    seq = lambda h: re.findall(r"<\s*(/?[a-zA-Z][a-zA-Z0-9]*)", h)
    print("comparaison avec %s" % ref)
    print("  texte visible identique : %s" % ("oui" if vis(r) == vis(s) else "NON"))
    print("  sequence des balises    : %s" % ("identique" if seq(r) == seq(s) else "MODIFIEE"))
    ra, sa = len(r.encode()), len(s.encode())
    print("  poids : %.2f Mo -> %.2f Mo  (%.1f %% de gain)" % (ra / 1048576, sa / 1048576, (ra - sa) / ra * 100))
