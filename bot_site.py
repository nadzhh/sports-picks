"""Page « Suivi des nuits du bot » (Wilson 16/11/2026 : « voir dans l'interface les différents tests des
bots de la nuit afin de voir le suivi des améliorations »).

Lit data/improve_report.json (une entree par nuit, avec le detail des essais depuis le 16/11), data/model_params.json
(reglages adoptes) et data/nba_lab.json (labo d'analyses NBA). Ecrit lazy/bot.html, charge dans la page principale (Aujourd'hui › Bot de nuit, Wilson 17/11 : « sur la page
principale, pas une page a part ») ;
regeneree a la fin de chaque nuit par improve_bot.py et a la demande : python bot_site.py
"""
import html
import json
from datetime import datetime
from pathlib import Path

OUT = Path("bot.html")        # ancienne adresse : redirige vers la page principale
FRAG = Path("lazy/bot.html")  # Aujourd'hui › Bot de nuit (charge a la demande, site_lazy)
JOURS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
C1, C2 = "#3987e5", "#d95926"      # saison ancienne / saison recente (palette validee sur fond sombre)

LABELS = {   # reglages essayes par improve_bot (foot_sim.DEFAULTS, nba_sim.DEFAULTS)
    "rest_w": "effet des jours de repos", "cong_w": "effet de l'enchaînement des matchs",
    "hl_days": "mémoire du niveau des équipes (demi-vie)", "goal_w": "part des buts réels à côté des xG",
    "home_boost": "avantage du terrain", "k_team": "prudence sur le niveau des équipes",
    "k_cup": "prudence sur les coupes d'Europe", "k_share": "prudence sur la part d'xG d'un joueur",
    "lineup_pow": "poids des absents", "finish": "réalisme devant le but",
    "intensity_sd": "variabilité de l'intensité d'un match", "rating_beta": "poids du classement Elo",
    "hl_rates": "mémoire des stats joueur (demi-vie)", "rate_prior_w": "poids de la saison passée",
    "hl_minutes": "mémoire des minutes (demi-vie)", "k_usage": "prudence sur l'usage d'un joueur",
    "usage_k": "dispersion des volumes de tirs", "usage_pow": "concentration des tirs sur les leaders",
    "ast_pow": "concentration des passes", "vol_c": "bruit du volume d'équipe", "shoot_c": "bruit de l'adresse",
    "min_sd_scale": "irrégularité des minutes", "blowout_from": "écart à partir duquel les titulaires sortent",
    "blowout_slope": "minutes perdues par point d'écart", "mkt_min_alpha": "minutes recalées sur le marché",
    "wowy_min": "minutes recalées sur les absents",
}


def _e(s):
    return html.escape(str(s), quote=True)


def _fr(x, d=2):
    return f"{x:,.{d}f}".replace(",", " ").replace(".", ",")


def _sg(x, d=2):
    return ("+" if x > 0 else "−" if x < 0 else "") + _fr(abs(x), d)


def _v(x):
    """Valeur de reglage a la francaise (1.15 -> 1,15 ; 900.0 -> 900)."""
    if isinstance(x, (int, float)):
        return (f"{x:g}" if abs(x) < 1e6 else str(x)).replace(".", ",").replace("-", "−")
    return str(x)


def _lab(p):
    return LABELS.get(p, p)


def _when(at, time=True):
    try:
        d = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return _e(at or "?")
    return f"{JOURS[d.weekday()]} {d:%d/%m}" + (f" · {d:%H:%M} UTC" if time else "")


def _load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ─── Courbe ─────────────────────────────────────────────────────────────────
def _chart(nights, key, title, unit_note):
    """Log-loss de base de chaque nuit, une ligne par saison (plus bas = meilleur) ; point plein = reglage adopte."""
    pts = []
    for i, n in enumerate(nights):
        b = (n.get("nba") or {}).get("base") if key == "nba" else n.get("base")
        a = (n.get("nba") or {}).get("adopted") if key == "nba" else n.get("adopted")
        if b: pts.append((i, n, b, a))
    if len(pts) < 2:
        return f'<div class="card"><h3>{title}</h3><p class="mu">Pas encore assez de nuits mesurées.</p></div>'
    seasons = sorted({s for _, _, b, _ in pts for s in b})[:2]
    vals = [b[s] for _, _, b, _ in pts for s in seasons if s in b]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or 0.001
    lo, hi = lo - pad, hi + pad
    W, H, L, R, T, B = 640, 210, 52, 14, 14, 26
    xs = lambda k: L + (W - L - R) * k / (len(pts) - 1)
    ys = lambda v: T + (H - T - B) * (v - lo) / (hi - lo)     # axe inverse : erreur faible EN HAUT, la courbe monte quand ca s'ameliore
    g = ""
    for j in range(4):   # quadrillage discret
        v = lo + (hi - lo) * j / 3
        g += (f'<line x1="{L}" x2="{W - R}" y1="{ys(v):.1f}" y2="{ys(v):.1f}" class="gr"/>'
              f'<text x="{L - 6}" y="{ys(v) + 4:.1f}" class="ax" text-anchor="end">{_fr(v, 3)}</text>')
    step = max(1, len(pts) // 6)
    for k, (_, n, _, _) in enumerate(pts):
        if k % step == 0 or k == len(pts) - 1:
            g += f'<text x="{xs(k):.1f}" y="{H - 6}" class="ax" text-anchor="middle">{_e(n["at"][8:10])}/{_e(n["at"][5:7])}</text>'
    for s, col in zip(seasons, (C1, C2)):
        line = [(xs(k), ys(b[s])) for k, (_, _, b, _) in enumerate(pts) if s in b]
        g += f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in line)}" fill="none" stroke="{col}" stroke-width="2"/>'
        for k, (_, n, b, a) in enumerate(pts):
            if s not in b: continue
            tip = f"{_when(n['at'])} — {s} : {_fr(b[s], 5)}" + (f" — adopté : {_lab(a['param'])} {_v(a['from'])} → {_v(a['to'])}" if a else "")
            g += (f'<g><title>{_e(tip)}</title><circle cx="{xs(k):.1f}" cy="{ys(b[s]):.1f}" r="{5 if a else 3}" '
                  f'fill="{col if a else "#0f172a"}" stroke="{col}" stroke-width="2"/>'
                  f'<circle cx="{xs(k):.1f}" cy="{ys(b[s]):.1f}" r="11" fill="transparent"/></g>')
    first, last = pts[0][2], pts[-1][2]
    prog = [100 * (first[s] - last[s]) / first[s] for s in seasons if s in first and s in last]
    nad = sum(1 for _, _, _, a in pts if a)
    leg = "".join(f'<span><i style="background:{c}"></i>{_e(s)}</span>' for s, c in zip(seasons, (C1, C2)))
    avg = sum(prog) / len(prog) if prog else 0
    since = f'depuis le {pts[0][1]["at"][8:10]}/{pts[0][1]["at"][5:7]}'
    head = ("" if not prog else f"✅ erreur −{_fr(avg, 2)} % {since}" if avg > 0 else f"erreur +{_fr(-avg, 2)} % {since}")
    return (f'<div class="card"><h3>{title} <small>{nad} réglage(s) adopté(s)</small></h3>'
            f'<div class="big">{head}</div>'
            f'<svg viewBox="0 0 {W} {H}" class="ch" role="img" aria-label="{_e(title)}">{g}</svg>'
            f'<div class="leg">{leg}<span><i class="dot"></i>point plein = réglage adopté cette nuit</span>'
            '<span>axe inversé : la courbe monte quand le bot progresse</span></div>'
            f'<p class="mu">{unit_note}</p></div>')


# ─── Journal ─────────────────────────────────────────────────────────────────
def _gains(t):
    return "".join(f'<td class="{"ok" if v > 0 else "ko"}">{_sg(1000 * v, 2)}</td>' for v in t["gain"].values())


def _tried_table(tried, adopted, seasons):
    if not tried:
        return ""
    rows = ""
    for t in sorted(tried, key=lambda t: -sum(t["gain"].values())):
        won = adopted and t["param"] == adopted["param"] and t["to"] == adopted["to"]
        rows += (f'<tr{" class=win" if won else ""}><td>{_e(_lab(t["param"]))}<span class="tk">{_e(t["param"])}</span></td>'
                 f'<td>{_e(_v(t["from"]))} → {_e(_v(t["to"]))}</td>{_gains(t)}'
                 f'<td>{"✅ adopté" if won else "écarté"}</td></tr>')
    return ('<table><tr><th>Réglage essayé</th><th>valeur</th>' + "".join(f"<th>{_e(s)}</th>" for s in seasons)
            + '<th></th></tr>' + rows + '</table>')


def _sport_block(icon, name, part):
    if not part:
        return ""
    tried, a = part.get("tried"), part.get("adopted")
    seasons = list((part.get("base") or {}).keys()) or (list(tried[0]["gain"]) if tried else [])
    if a:
        head = (f'✅ adopté : <b>{_e(_lab(a["param"]))}</b> {_e(_v(a["from"]))} → {_e(_v(a["to"]))} '
                f'(gain {" / ".join(_sg(1000 * v) for v in a["gain"].values())} ‰)')
    elif tried:
        head = f'{len(tried)} réglages essayés, aucun n’améliore les deux saisons : rien ne change'
    elif part.get("base"):
        head = "réglages essayés, rien d’adopté (détail non archivé avant le 16/11)"
    else:
        head = "non jugé cette nuit"
    return (f'<div class="sb"><div class="sh">{icon} {name} : {head}</div>'
            + _tried_table(tried, a, seasons) + "</div>")


def _lab_block(lab):
    if not lab:
        return ""
    base = lab.get("base") or {}
    tried = lab.get("tried") or []
    a, rm = lab.get("adopted"), lab.get("removed")
    if a:
        head = (f'✅ adoptée : <b>{_e(a["label"])}</b> · réussite {_fr(a["before"]["acc"], 1)} % → {_fr(a["after"]["acc"], 1)} %'
                f' · picks {_e(_v(a["before"]["picks"].get("hit")))} % → {_e(_v(a["after"]["picks"].get("hit")))} %')
    elif rm:
        head = f'🗑️ retirée (n’aide plus) : <b>{_e(rm)}</b>'
    elif tried:
        head = f'{len(tried)} analyse(s) testée(s), aucune assez sûre pour être adoptée'
    else:
        head = "rien à tester cette nuit (catalogue déjà essayé récemment)"
    if base:
        head += (f'<br><span class="mu">modèle en service : bon côté {_fr(base.get("acc", 0), 1)} % · picks '
                 f'{_e(_v((base.get("picks") or {}).get("hit")))} % gagnés (ROI {_e(_v((base.get("picks") or {}).get("roi")))} %)'
                 f' · {lab.get("n", "?")} props rejoués</span>')
    rows = "".join(
        f'<tr{" class=win" if a and t["name"] == a["name"] else ""}><td>{_e(t["label"])}</td>'
        f'<td class="{"ok" if t["gain"] > 0 else "ko"}">{_sg(1000 * t["gain"], 2)}</td>'
        f'<td>{_fr(100 * t["p"], 0)} %</td><td>{_e(_v(t.get("acc", "")))} %</td>'
        f'<td>{"✅ sûr" if t.get("ok") else "trop incertain" if t["gain"] > 0 else "nuit au modèle"}</td></tr>'
        for t in sorted(tried, key=lambda t: -t["gain"]))
    tab = ('<table><tr><th>Analyse essayée</th><th>gain ‰</th><th>risque hasard</th><th>bon côté</th><th></th></tr>'
           + rows + '</table>') if rows else ""
    return f'<div class="sb"><div class="sh">🧪 Labo d’analyses NBA : {head}</div>{tab}</div>'


def _night(n, open_=False):
    f, nb, lab = n, n.get("nba"), n.get("lab")
    tags = []
    for ic, part in (("⚽", f if (f.get("base") or f.get("tried")) else None), ("🏀", nb)):
        if not part: continue
        tags.append(f'{ic} ✅' if part.get("adopted") else f'{ic} {len(part.get("tried") or []) or "–"}')
    if lab:
        tags.append("🧪 ✅" if lab.get("adopted") else f'🧪 {len(lab.get("tried") or [])}')
    notes = [x for x in (n.get("notes") or []) if x]
    h2h = (n.get("h2h") or {}).get("mesure") or {}
    h2h_txt = ""
    if h2h:
        h2h_txt = ('<div class="sb"><div class="sh">🤝 Poids du face-à-face remesuré : '
                   + ", ".join(f'{_e(s)} {_fr(r["w"], 2)} ({r["n"]} cas)' for s, r in sorted(h2h.items()) if "w" in r)
                   + "</div></div>")
    body = (_sport_block("⚽", "Simulation foot", f if (f.get("base") or f.get("tried")) else None)
            + _sport_block("🏀", "Simulation NBA", nb) + _lab_block(lab) + h2h_txt
            + (f'<ul class="nt">{"".join(f"<li>{_e(x)}</li>" for x in notes[:12])}</ul>' if notes else ""))
    return (f'<details{" open" if open_ else ""}><summary><b>{_when(n.get("at"))}</b>'
            f'<span class="tg">{" · ".join(tags)}</span></summary>{body}</details>')


# ─── Page ────────────────────────────────────────────────────────────────────
def build():
    R = _load("data/improve_report.json", {})
    P = _load("data/model_params.json", {})
    LAB = _load("data/nba_lab.json", {})
    nights = list(R.get("nights") or [])
    last = R.get("last") or {}
    if nights and last and nights[-1].get("at") == last.get("at"):   # la derniere nuit a le detail complet
        n = dict(nights[-1])
        n.setdefault("tried", last.get("tried"))
        if last.get("nba"): n["nba"] = dict(n.get("nba") or {}, tried=last["nba"].get("tried"))
        n.setdefault("lab", last.get("lab")); n.setdefault("h2h", last.get("h2h"))
        nights[-1] = n

    charts = (_chart(nights, "nba", "🏀 Simulation NBA : erreur sur les vraies lignes de props",
                     "Erreur (log-loss) du P(over) simulé sur ~48 000 props réels (2025-26 ESPN BET, 2026-27 DraftKings). Les "
                     "petites variations sans point plein viennent des nouveaux matchs ajoutés chaque nuit à l’échantillon. "
                     "Le marché sans marge est à 0,686 : c’est la cible à passer.")
              + _chart(nights, "foot", "⚽ Simulation foot : erreur sur deux saisons de matchs",
                       "Erreur de prévision (log-loss des scores) des matchs rejoués sur 2024-25 et 2025-26."))

    # en service
    hist = P.get("history") or []
    adopted = "".join(f'<tr><td>{_when(h["at"], False)}</td><td>{"🏀" if h.get("sport") == "nba" else "⚽"}</td>'
                      f'<td>{_e(_lab(h["param"]))}<span class="tk">{_e(h["param"])}</span></td>'
                      f'<td>{_e(_v(h["from"]))} → {_e(_v(h["to"]))}</td>'
                      f'<td class="ok">{" / ".join(_sg(1000 * v) for v in (h.get("gain") or {}).values())}</td></tr>'
                      for h in reversed(hist))
    from_lab = "".join(f'<tr><td>{_when(h["at"], False)}</td><td>🧪</td><td>{_e(h.get("adopted") or "")}'
                       f'{" (retirée : " + _e(h["removed"]) + ")" if h.get("removed") else ""}</td>'
                       f'<td colspan="2">{_e(_v((h.get("before") or {}).get("acc")))} % → {_e(_v((h.get("after") or {}).get("acc")))} %</td></tr>'
                       for h in reversed(LAB.get("history") or []))
    active = LAB.get("active") or []
    h2h = {k: v for k, v in (P.get("h2h_w") or {}).items() if not k.startswith("_")}
    calib = ((last.get("nba") or {}).get("calib") or {}).get("test") or {}
    service = (
        '<div class="card"><h3>⚙️ En service aujourd’hui</h3>'
        + (f'<table><tr><th>adopté le</th><th></th><th>réglage</th><th>valeur</th><th>gain ‰</th></tr>{adopted}{from_lab}</table>'
           if adopted or from_lab else '<p class="mu">Aucun réglage adopté pour l’instant.</p>')
        + '<ul class="nt">'
        + f'<li>🧪 Analyses ajoutées au modèle de props NBA : {", ".join(_e(a) for a in active) if active else "aucune pour l’instant"}</li>'
        + (f'<li>🎯 Recalage des props NBA sur le marché : erreur {_fr(calib["marche"], 4)} (marché seul) → '
           f'{_fr(calib["cale"], 4)} (marché + simulation)</li>' if calib.get("marche") else "")
        + (f'<li>🤝 Poids du face-à-face par stat : {", ".join(f"{_e(k)} {_fr(v, 2)}" for k, v in sorted(h2h.items()))}</li>' if h2h else "")
        + '</ul></div>')

    journal = "".join(_night(n, open_=(i == 0)) for i, n in enumerate(reversed(nights)))
    stamp = last.get("at") or ""
    return f"""<div class="bt"><style>
.bt{{--bcard:#0f172a;--bline:#1e293b;--btx:#e2e8f0;--bmu:#94a3b8;--bok:#4ade80;--bko:#f87171;color:var(--btx);font-size:14px;line-height:1.45}}
.bt *{{box-sizing:border-box}}
.bt h2{{font-size:19px;margin:4px 0 2px}}.bt h3{{margin:0 0 8px;font-size:15px}}.bt h3 small,.bt .mu{{color:var(--bmu);font-weight:400;font-size:12.5px}}
.bt .card{{background:var(--bcard);border:1px solid var(--bline);border-radius:12px;padding:14px;margin:12px 0}}
.bt .big{{font-size:17px;font-weight:700;margin:2px 0 6px}}
.bt .ch{{width:100%;height:auto;display:block}}.bt .gr{{stroke:#1e293b;stroke-width:1}}.bt .ax{{fill:var(--bmu);font-size:11px}}
.bt .leg{{display:flex;flex-wrap:wrap;gap:14px;color:var(--bmu);font-size:12px;margin-top:4px}}
.bt .leg i{{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}}
.bt .leg i.dot{{width:9px;height:9px;border-radius:50%;background:var(--bmu)}}
.bt details{{background:var(--bcard);border:1px solid var(--bline);border-radius:12px;margin:8px 0}}
.bt summary{{cursor:pointer;padding:11px 14px;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}}
.bt .tg{{color:var(--bmu)}}.bt .sb{{padding:4px 14px 10px}}.bt .sh{{margin:6px 0}}
.bt .tw{{overflow-x:auto}}.bt table{{width:100%;border-collapse:collapse;font-size:12.5px}}
.bt th,.bt td{{padding:5px 8px;border-top:1px solid var(--bline);text-align:left;white-space:nowrap}}.bt th{{color:var(--bmu);font-weight:500}}
.bt td.ok{{color:var(--bok)}}.bt td.ko{{color:var(--bko)}}.bt tr.win td{{background:#0f2a1c}}
.bt .tk{{display:block;color:#64748b;font-size:11px}}.bt .nt{{color:var(--bmu);margin:6px 0;padding-left:18px}}
.bt .grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:760px){{.bt .grid{{grid-template-columns:1fr}}}}
.bt .grid .card{{margin:0}}.bt .grid>*,.bt .card,.bt details,.bt .tw{{min-width:0;max-width:100%}}
@media(max-width:560px){{.bt th,.bt td{{padding:5px 6px}}.bt summary{{padding:10px 12px}}.bt .sb{{padding:4px 10px 10px}}}}
</style>
<h2>🤖 Nuits du bot d’amélioration</h2>
<p class="mu">Dernière nuit : {_when(stamp)}. Chaque nuit (vers 2 h 15, heure de Paris), le bot rejoue des matchs déjà joués
avec la simulation, sans rien savoir du résultat, essaie des réglages voisins et des analyses nouvelles, et ne garde
que ce qui prévoit mieux. Les matchs récents comptent plus dans le jugement du labo.</p>
<div class="grid">{charts}</div>
{service}
<h3 style="margin:18px 0 4px">📒 Journal des nuits</h3>
<p class="mu">Gains en millièmes (‰) d’erreur de prévision : <span style="color:var(--bok)">vert = mieux</span>,
<span style="color:var(--bko)">rouge = moins bien</span>. Un réglage n’est adopté que s’il améliore les deux saisons
(une seule pourrait être de la chance). « Risque hasard » = probabilité que le gain d’une analyse soit dû au hasard (≤ 1 % exigé).
Le détail essai par essai est archivé depuis le 16/11.</p>
{journal}
</div>"""


def fragment():
    return build().replace("<table>", '<div class="tw"><table>').replace("</table>", "</table></div>")


def write():
    """Fragment de la page principale (Aujourd'hui › Bot de nuit, charge a la demande) + ancienne adresse redirigee."""
    FRAG.parent.mkdir(parents=True, exist_ok=True)
    FRAG.write_text(fragment(), encoding="utf-8")
    OUT.write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=./#bot">'
                   '<title>Nuits du bot</title><a href="./#bot">Suivi du bot : page principale, Aujourd’hui › Bot de nuit</a>',
                   encoding="utf-8")
    return FRAG


if __name__ == "__main__":
    print(write())
