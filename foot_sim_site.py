"""
foot_sim_site.py — Onglet « Simulation Foot » : liste des matchs a gauche, fiche du match a droite.

Donnees : data/foot_sim.json (foot_sim_run.py, 10 000 simulations minute par minute par match).
Styles : composants .eb-* de site_theme.py ; JS de selection partage (foot_analyse_site.SCRIPT).
Video du match type : foot_replay_player.js (canvas), donnees embarquees dans la fiche.
"""
import html
import json
from datetime import datetime, timedelta, timezone

from foot_analyse_site import SCRIPT as EB_SCRIPT
import site_live

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Paris")
except Exception:
    TZ = timezone(timedelta(hours=1))

SIM_PATH = "data/foot_sim.json"
JOURS = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]
LIG = {47: "Premier League", 87: "LaLiga", 54: "Bundesliga", 55: "Serie A", 53: "Ligue 1",
       42: "Ligue des champions", 73: "Ligue Europa"}
POS = {"GK": "gardien", "DEF": "défenseur", "MID": "milieu", "ATT": "attaquant"}
ABS = {"injury": "blessé", "suspension": "suspendu", "internationalDuty": "en sélection"}
_BACK = [("Back in training", "de retour à l’entraînement"), ("Doubtful", "incertain"), ("Unknown", "date inconnue"),
         ("Early ", "début "), ("Mid ", "mi-"), ("Late ", "fin "), ("January", "janvier"), ("February", "février"),
         ("March", "mars"), ("April", "avril"), ("May", "mai"), ("June", "juin"), ("July", "juillet"), ("August", "août"),
         ("September", "septembre"), ("October", "octobre"), ("November", "novembre"), ("December", "décembre")]


def _back(txt):
    for a, b in _BACK:
        txt = txt.replace(a, b)
    return txt
PICK_RULE_TXT = ""   # renseigne si un marche est valide sur deux saisons (foot_sim_run.FOOT_PICK)
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
JOURS_L = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _n(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


BACKTEST_NOTE = ("Validé sur toute la saison 2025-26 de Premier League, Liga, Bundesliga et Serie A (~1 450 matchs), "
                 "chaque match simulé avec les seuls matchs précédents : sur les plus/moins 2,5 buts, le simulateur est "
                 "presque au niveau des cotes de clôture Pinnacle, la référence des parieurs (et meilleur en Serie A) ; "
                 "sur le 1X2 il reste derrière le marché, qui intègre des informations que les statistiques n’ont pas. "
                 "Les nuls sont bien calibrés (26 % simulés, 26 % réels). Outil d’analyse, pas un conseil de pari.")


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _pct(x, d=0):
    return f"{100 * x:.{d}f}".replace(".", ",") + " %"


def _when(iso, now):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ)
    except Exception:
        return ""
    day = "Auj." if dt.date() == now.date() else ("Dem." if dt.date() == now.date() + timedelta(days=1) else JOURS[dt.weekday()])
    return f"{day} {dt:%H:%M}"


def load_sim(path=SIM_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _badge(g):
    e = g.get("espn") or {}
    return site_live.badge("soccer", e.get("slug"), e.get("id"))


def _item(g, gid, now, first):
    p = g["p"]
    chips = "".join(f'<span class="eb-chip"><em>{k}</em>{_pct(p[k])}</span>' for k in ("1", "X", "2"))
    return (f'<button class="eb-item{" on" if first else ""}" data-id="{gid}" onclick="ebPick(\'foot2\',\'{gid}\')">'
            f'<div class="eb-item-top"><span>{_e(LIG.get(g["league"], g["league_name"]))} {_badge(g)}</span><span>{_when(g["ko"], now)}</span></div>'
            f'<div class="eb-item-t">{_e(g["home"]["name"])} - {_e(g["away"]["name"])}</div>'
            f'<div class="eb-chips">{chips}</div></button>')


def _kpi(label, p, sub=""):
    return (f'<div class="eb-card eb-kpi"><span class="eb-kpi-l">{label}</span>'
            f'<span class="eb-kpi-v">{_pct(p, 1)}</span><span class="eb-bar"><i style="--w:{100 * p:.1f}%"></i></span>'
            + (f'<span class="eb-kpi-s">{sub}</span>' if sub else "") + '</div>')


def _header(g, now):
    hi, ai = g["home_info"], g["away_info"]
    lt = lambda i: {"officielle": "composition officielle", "probable": "composition probable",
                    "dernier onze": "dernier onze aligné", "estimée": "composition estimée"}.get(i["lineup"], "composition inconnue")

    def side(name, xg, goals, info):
        return (f'<div class="eb-team"><span class="eb-team-n">{_e(name)}</span>'
                f'<span class="eb-team-sub">xG attendus : <b>{xg:.2f}</b> · buts : <b>{goals:.2f}</b></span>'
                f'<span class="eb-team-sub">{lt(info)}</span></div>')
    return (f'<div class="eb-card"><div class="eb-meta">{_e(LIG.get(g["league"], g["league_name"]))} · {_when(g["ko"], now)}</div>'
            f'<div class="eb-vs">{side(g["home"]["name"], g["xg"][0], g["goals"][0], hi)}<span class="eb-vs-x">-</span>'
            f'{side(g["away"]["name"], g["xg"][1], g["goals"][1], ai)}</div>'
            f'<button class="eb-more eb-ask" data-ko="{_e(g["ko"])}" '
            f'data-live-key="soccer|{_e((g.get("espn") or {}).get("slug") or "")}|{_e((g.get("espn") or {}).get("id") or "")}" onclick="window.ebAnalyse&&ebAnalyse(\'foot\',\'{_e(g["id"])}\','
            f'\'{_e(g["home"]["name"] + " - " + g["away"]["name"]).replace(chr(39), "&#39;")}\',this)">💬 Analyse approfondie avec Claude</button></div>')


def _kpis(g):
    p = g["p"]
    return ('<div class="eb-g3">' + _kpi(f'Victoire {_e(g["home"]["name"])}', p["1"]) + _kpi("Match nul", p["X"])
            + _kpi(f'Victoire {_e(g["away"]["name"])}', p["2"]) + '</div>'
            '<div class="eb-g3">' + _kpi("Plus de 2,5 buts", p["over"]["2.5"]) + _kpi("Les deux équipes marquent", p["btts"])
            + _kpi("Au moins un carton rouge", p["red"]) + '</div>')


def _args(g):
    """Tirs et buts attendus, avec leurs arguments (forme recente pour / contre, absences) : demande de Wilson
    (04/11/2026, « nombre de tirs > arguments, nombre de buts > arguments »)."""
    f = g.get("form") or {}
    fh, fa = f.get("home"), f.get("away")
    sh = g.get("shots")
    if not (fh and fa) and not sh: return ""
    h, a = _e(g["home"]["name"]), _e(g["away"]["name"])
    n1 = lambda x: f"{x:.1f}".replace(".", ",")
    out = []
    if sh:
        (hs, ht), (as_, at) = sh["home"], sh["away"]
        out.append(f'<h3 class="eb-h">Tirs <small>simulés · arguments</small></h3><ul class="eb-why-l">'
                   f'<li>Tirs attendus : <b>{h} {n1(hs)}</b> ({n1(ht)} cadrés) · <b>{a} {n1(as_)}</b> ({n1(at)} cadrés), '
                   f'soit <b>{n1(hs + as_)}</b> tirs et <b>{n1(ht + at)}</b> cadrés dans le match.</li>')
        for (name, fo, opp_name, fopp, sim, xg) in ((h, fh, a, fa, hs, g["xg"][0]), (a, fa, h, fh, as_, g["xg"][1])):
            if not (fo and fopp): continue
            ref = (fo["sh_f"] + fopp["sh_a"]) / 2
            verdict = ("au-dessus" if sim > ref + 1 else "en dessous" if sim < ref - 1 else "dans la ligne")
            out.append(f'<li><b>{name}</b> tire <b>{n1(fo["sh_f"])}</b> fois par match ({n1(fo["sot_f"])} cadrés, xG '
                       f'{_n(fo.get("xg_f", 0))}) sur ses {fo["n"]} derniers matchs ; {opp_name} en concède '
                       f'<b>{n1(fopp["sh_a"])}</b> (xG {_n(fopp.get("xg_a", 0))}). Simulé : <b>{n1(sim)}</b>, {verdict} de ces '
                       f'repères ({n1(ref)})'
                       + (f' : les tirs découlent de l’xG attendu ce soir (<b>{_n(xg)}</b>), qui tient compte de la composition, '
                          'des absences et de la défense adverse, et de la qualité habituelle de ses tirs.'
                          if verdict != "dans la ligne" else ".") + '</li>')
        out.append('</ul>')
    if fh and fa:
        gh, ga_ = g["goals"]
        tot = gh + ga_
        p = g["p"]
        out.append(f'<h3 class="eb-h eb-ev-t">Buts <small>simulés · arguments</small></h3><ul class="eb-why-l">'
                   f'<li>Buts attendus : <b>{h} {_n(gh)}</b> · <b>{a} {_n(ga_)}</b> (total {_n(tot)}) · plus de 2,5 : '
                   f'<b>{_pct(p["over"]["2.5"])}</b> · les deux marquent : <b>{_pct(p["btts"])}</b>.</li>')
        for (name, fo, opp_name, fopp, side) in ((h, fh, a, fa, "home"), (a, fa, h, fh, "away")):
            info = g.get(side + "_info") or {}
            delta = ((info.get("lineup_mult") or 1.0) - 1) * 100
            ab = [x["name"] for x in info.get("absences") or [] if x.get("impact", 0) >= 0.05]
            out.append(f'<li><b>{name}</b> : {n1(fo["gf"])} marqués / {n1(fo["ga"])} encaissés par match '
                       f'<span class="eb-dim">(xG {_n(fo.get("xg_f", 0))} / {_n(fo.get("xg_a", 0))})</span> ; plus de 2,5 buts '
                       f'dans {round(fo["o25"] * fo["n"])}/{fo["n"]}, les deux marquent {round(fo["btts"] * fo["n"])}/{fo["n"]} ; '
                       f'forme {" ".join(fo.get("last") or [])}'
                       + (f' ; attaque du soir <b class="{"eb-neg" if delta < 0 else "eb-acc"}">{"+" if delta > 0 else "−"}{abs(delta):.0f} %</b>'
                          + (f' ({_e(", ".join(ab))} absent{"s" if len(ab) > 1 else ""})' if ab and delta < 0 else
                             " (onze plus fort que d’habitude)" if delta > 0 else "") if abs(delta) >= 3 else "")
                       + '.</li>')
        ref = (fh["gf"] + fh["ga"] + fa["gf"] + fa["ga"]) / 2
        out.append(f'<li>Leurs matchs récents comptent en moyenne <b>{n1(ref)}</b> buts ; la simulation en attend <b>{n1(tot)}</b>'
                   + (" : plus fermé que d’habitude (niveau des attaques du soir, défenses adverses)." if tot < ref - 0.3 else
                      " : plus ouvert que d’habitude." if tot > ref + 0.3 else ".") + '</li></ul>')
    return ('<div class="eb-card">' + "".join(out) + '<p class="eb-note">Forme : derniers matchs présents dans la base '
            '(championnat et coupes d’Europe suivis). Un tir cadré = arrêt du gardien ou but (les tirs contrés n’en sont pas).</p></div>')


def _style_tags(p):
    """Traits de jeu marquants (seuils autour des moyennes de la base : contre-attaque 8 % des tirs, coups de pied
    arretes 25 %, domination 50 %, distance de tir ~17 m, xG par tir ~0,10)."""
    t = []
    if p["dom"] >= 0.58: t.append(f"domine ses matchs ({round(100 * p['dom'])} % des tirs)")
    elif p["dom"] <= 0.43: t.append(f"subit souvent, bloc plus bas ({round(100 * p['dom'])} % des tirs)")
    if p["fb"] >= 0.11: t.append(f"aime jouer en contre ({round(100 * p['fb'])} % de ses tirs)")
    if p["sp"] >= 0.30: t.append(f"dangereuse sur coups de pied arrêtés ({round(100 * p['sp'])} %)")
    if p["dist"] >= 18.5: t.append(f"frappe souvent de loin ({p['dist']:.0f} m en moyenne)".replace(".", ","))
    if p["xgps"] >= 0.125: t.append(f"tirs de grande qualité (xG {p['xgps']:.2f} par tir)".replace(".", ","))
    if p["sha"] <= 10.5: t.append(f"concède peu de tirs ({p['sha']:.1f})".replace(".", ","))
    elif p["sha"] >= 14.5: t.append(f"laisse beaucoup tirer ({p['sha']:.1f} tirs concédés)".replace(".", ","))
    if p["back3"] >= 0.6: t.append("défense à trois")
    return t


def _tactics(g):
    """Profil tactique des deux equipes (15 derniers matchs de la base) et effet sur les tirs attendus."""
    st = g.get("style") or {}
    if not any(st.values()): return ""
    n1 = lambda x: f"{x:.1f}".replace(".", ",")
    cols = ""
    for side, opp in (("home", "away"), ("away", "home")):
        p, o, name = st.get(side), st.get(opp), _e(g[side]["name"])
        if not p:
            cols += f'<div><div class="eb-meta">{name}</div><p class="eb-note">Moins de 5 matchs dans la base : profil indisponible.</p></div>'
            continue
        tags = "".join(f'<span class="eb-tag">{_e(x)}</span> ' for x in _style_tags(p))
        rows = (f'<li>Schéma habituel <b>{_e(p.get("formation") or "?")}</b> · {p["n"]} derniers matchs</li>'
                f'<li>Tirs : <b>{n1(p["sh"])}</b> par match ({n1(p["so"])} cadrés), en concède <b>{n1(p["sha"])}</b></li>'
                f'<li>Qualité : xG {p["xgps"]:.2f} par tir, à {p["dist"]:.0f} m en moyenne · contres {round(100 * p["fb"])} % · '
                f'coups de pied arrêtés {round(100 * p["sp"])} %</li>'.replace(".", ","))
        sh = g.get("shots") or {}
        if o and sh.get("sim"):
            sim_v = sh["sim"][side][0]; fin = sh[side][0]; hab = (p["sh"] + o["sha"]) / 2
            rows += (f'<li>Tirs ce soir : simulation <b>{n1(sim_v)}</b> (xG du soir, rapport de force, composition) · habitudes '
                     f'<b>{n1(hab)}</b> (tire {n1(p["sh"])}, {_e(g[opp]["name"])} en concède {n1(o["sha"])}) → '
                     f'<b>{n1(fin)}</b> retenus</li>')
        cols += f'<div><div class="eb-meta">{name}</div><div>{tags}</div><ul class="eb-inj-l">{rows}</ul></div>'
    return ('<div class="eb-card"><h3 class="eb-h">Profil tactique <small>façon de jouer et effet sur le match</small></h3>'
            f'<div class="eb-inj">{cols}</div><p class="eb-note">Mesuré sur 759 matchs passés : la simulation tient déjà compte du '
            'rapport de force (un outsider qui joue en contre tire bien moins : réel / simulé = 1,00). Ce qu’elle ne voyait pas, '
            'ce sont les habitudes de VOLUME (équipes qui frappent de loin, blocs bas qui laissent tirer) : mélangées à 40 %, '
            'elles améliorent la prévision des tirs hors échantillon (log-loss −2,2 %). Le style ne change pas les buts '
            'attendus (pas de gain mesuré).</p></div>')


def _shots_card(g):
    """Lignes de tirs / tirs cadres (equipe et match) : probabilite recalee sur le backtest et cote juste."""
    L = (g.get("shots") or {}).get("lines")
    if not L: return ""
    h, a = _e(g["home"]["name"]), _e(g["away"]["name"])
    sh = g["shots"]
    rows = [(f"Tirs {h}", sh["home"][0], L.get("home_sh")), (f"Tirs cadrés {h}", sh["home"][1], L.get("home_so")),
            (f"Tirs {a}", sh["away"][0], L.get("away_sh")), (f"Tirs cadrés {a}", sh["away"][1], L.get("away_so")),
            ("Tirs du match ⚠", sh["home"][0] + sh["away"][0], L.get("tot_sh")),
            ("Tirs cadrés du match ⚠", sh["home"][1] + sh["away"][1], L.get("tot_so"))]
    n1 = lambda x: f"{x:.1f}".replace(".", ",")
    body = "".join(
        f'<div class="eb-row" style="flex-wrap:wrap"><span><b>{lab}</b> <span class="eb-dim">· attendu {n1(m)}</span></span><span>'
        + " · ".join(f'+{l.replace(".", ",")} <b>{_pct(p)}</b> <span class="eb-dim">@{1 / max(p, 0.01):.2f}</span>'
                     for l, p in (ln or {}).items()) + '</span></div>'
        for lab, m, ln in rows if ln)
    return ('<div class="eb-card"><h3 class="eb-h">Tirs et tirs cadrés <small>probabilité de dépasser la ligne · cote juste</small></h3>'
            f'<div class="eb-list2">{body}</div><p class="eb-note">Simulation mélangée aux habitudes de volume des deux équipes '
            '(voir « Profil tactique »), puis recalée sur un backtest de 759 matchs (sept. 2025 - oct. 2026, sans information '
            'postérieure au coup d’envoi) : par équipe, probabilités justes à 3-7 points près sur la période de validation ; '
            '⚠ cumuls du match moins fiables (5 à 8 points, la saison en cours tire un peu plus). Une cote du bookmaker au-dessus de la cote juste '
            'est une piste, pas une garantie. Tir cadré = arrêt du gardien ou but.</p></div>')


def _shots_hist_card(g):
    """Historique des tirs L5 / L10 / L20 de chaque equipe, aux lignes du soir, brut et ajuste a l'adversaire."""
    H, L = g.get("shots_hist") or {}, (g.get("shots") or {}).get("lines") or {}
    if not any(H.get(s) for s in ("home", "away")): return ""
    n1 = lambda x: f"{x:.1f}".replace(".", ",")
    cols = ""
    for s, o in (("home", "away"), ("away", "home")):
        h = H.get(s)
        if not h: continue
        G = h["games"]
        def hits(i, line, n):
            k = G[:n]
            return f'{sum(1 for x in k if x[i] > line)}/{len(k)}'
        def avg(i, n):
            k = G[:n]
            return sum(x[i] for x in k) / len(k)
        rows = ""
        for key, lab, i_raw, i_adj in ((s + "_sh", "tirs", 3, 5), (s + "_so", "tirs cadrés", 4, None)):
            for ln in (L.get(key) or {}):
                v = float(ln)
                raw = " · ".join(f"L{n} <b>{hits(i_raw, v, n)}</b>" for n in (5, 10, 20))
                adj = (" · ".join(f"L{n} {hits(i_adj, v, n)}" for n in (5, 10, 20))) if i_adj else ""
                rows += (f'<div class="eb-row" style="flex-wrap:wrap"><span>+{ln.replace(".", ",")} {lab}</span><span>{raw}'
                         + (f' <span class="eb-dim">· ajusté {adj}</span>' if adj else "") + '</span></div>')
        conc = h["opp_conc"]
        strength = ("concède beaucoup" if conc >= h["ref"] * 1.08 else "concède peu" if conc <= h["ref"] * 0.92 else "est dans la moyenne")
        means = (f'Moyenne tirs : L5 <b>{n1(avg(3, 5))}</b> · L10 <b>{n1(avg(3, 10))}</b> · L20 <b>{n1(avg(3, 20))}</b> '
                 f'<span class="eb-dim">(ajusté : {n1(avg(5, 5))} · {n1(avg(5, 10))} · {n1(avg(5, 20))})</span><br>'
                 f'Moyenne cadrés : L5 <b>{n1(avg(4, 5))}</b> · L10 <b>{n1(avg(4, 10))}</b> · L20 <b>{n1(avg(4, 20))}</b>')
        last = "".join(f'<tr><td>{x[0][8:]}/{x[0][5:7]}</td><td>{_e(x[1])} <span class="eb-dim">{x[2]} · {x[7]}</span></td>'
                       f'<td class="n"><b>{x[3]}</b></td><td class="n">{x[4]}</td><td class="n">{n1(x[5])}</td>'
                       f'<td class="n eb-dim">{"–" if x[6] is None else n1(x[6])}</td></tr>' for x in G)
        data = json.dumps({"g": [[x[0], x[1], x[2], x[3], x[4], x[5], x[7]] for x in G],
                           "L": {"sh": list(L.get(s + "_sh") or {}), "so": list(L.get(s + "_so") or {})}},
                          ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        chart = (f'<div class="eb-shc" data-k="sh" data-n="10"><script type="application/json">{data}</script>'
                 '<div class="eb-shc-ctl"></div><div class="eb-shc-sum"></div><div class="eb-shc-plot"></div>'
                 '<div class="eb-shc-x"></div><div class="eb-shc-cap eb-dim"></div></div>')
        cols += (f'<div><div class="eb-meta">{_e(g[s]["name"])} <span class="eb-dim">· face à {_e(g[o]["name"])} '
                 f'qui {strength} ({n1(conc)} tirs/match, moyenne {n1(h["ref"])})</span></div>{chart}'
                 f'<p class="eb-note" style="margin:6px 0">{means}</p><div class="eb-list2">{rows}</div>'
                 f'<details class="eb-det"><summary>Les {len(G)} derniers matchs</summary><div class="eb-scroll"><table class="eb-tbl">'
                 '<tr><th>Date</th><th>Adversaire</th><th>Tirs</th><th>Cadrés</th><th>Ajusté</th><th>Adv. concède</th></tr>'
                 f'{last}</table></div></details></div>')
    return ('<div class="eb-card"><h3 class="eb-h">Historique des tirs <small>L5 · L10 · L20, toutes compétitions, aux lignes du soir</small></h3>'
            f'<div class="eb-inj">{cols}</div><p class="eb-note">« Ajusté » : chaque match est corrigé de la force de l’adversaire '
            '(tirs qu’il concédait alors) puis ramené à l’adversaire du soir, à moitié (meilleur réglage au test). Mesuré sur 6 139 '
            'matchs : la L20 prévoit mieux que la L5 (erreur moyenne 4,1 contre 4,4 tirs) et l’ajustement gagne environ 3 % ; '
            'sur les tirs cadrés il n’apporte rien, d’où le brut seul. Ce sont des repères : la probabilité de la carte '
            '« Tirs et tirs cadrés » (simulation recalée) reste la référence.</p></div>')


def _video(g, gid):
    rp = g.get("replay")
    if not rp or not rp.get("events"): return ""
    title = json.dumps({"home": g["home"]["name"], "away": g["away"]["name"],
                        "foot": "Un match parmi 10 000 simulations : résultat tiré au sort selon les probabilités"},
                       ensure_ascii=False)
    data = json.dumps(rp, ensure_ascii=False, separators=(",", ":"))
    n_goals = sum(e["kind"] in ("but", "csc") for e in rp["events"])
    return (f'<div class="eb-card eb-video"><div class="eb-ctrl"><h3 class="eb-h eb-h-nomb">Le match type en vidéo '
            f'<small>{rp["final"][0]}-{rp["final"][1]} · {len(rp["events"])} actions, {n_goals} but{"s" if n_goals > 1 else ""}</small></h3>'
            f'<button class="eb-more" onclick="ebFootVideo(this)">▶ Regarder</button></div>'
            f'<div class="eb-video-host sp-foot-host"></div>'
            f'<script type="application/json" class="sp-foot-data">{data.replace("</", "<\\/")}</script>'
            f'<script type="application/json" class="sp-foot-title">{title.replace("</", "<\\/")}</script>'
            '<p class="eb-note">Le match en entier (~9 min) : les 22 joueurs avec leur numéro, placés selon le vrai schéma '
            'de leur équipe (FotMob), bloc qui monte ou se replie, passes, pressing. Chaque tir est un tir réel passé du joueur '
            '(sa position, son xG). Résultat tiré au sort selon les probabilités des simulations : l’outsider apparaît gagnant '
            'aussi souvent qu’il gagne. Ce n’est pas un pronostic de score exact.</p></div>')


def _crisis(g):
    cols = ""
    for side in ("home", "away"):
        i, name = g[side + "_info"], g[side]["name"]
        mult = i.get("lineup_mult") or 1.0
        delta = (mult - 1) * 100
        head = (f'Attaque du soir : <b class="{"eb-neg" if delta < -2 else "eb-acc" if delta > 2 else ""}">'
                f'{"+" if delta > 0 else "−" if delta < 0 else ""}{abs(delta):.0f} %</b> par rapport à d’habitude '
                f'<span class="eb-dim">(xG {i["npxg_base"]:.2f} → {i["npxg"]:.2f})</span>')
        ab = "".join(f'<li><b>{_e(a["name"])}</b> <span class="eb-dim">({_e(POS.get(a.get("pos"), ""))}, '
                     f'{_e(ABS.get(a.get("type"), a.get("type") or "absent"))}{", retour : " + _e(_back(a["back"])) if a.get("back") else ""})</span> : '
                     f'{_pct(a["impact"])} de l’attaque habituelle</li>' for a in i.get("absences") or [])
        if not ab: ab = '<li class="eb-dim">Aucune absence qui pèse sur l’attaque.</li>'
        xi = ", ".join(_e(n) for n in i.get("xi") or [])
        cols += (f'<div><div class="eb-meta">{_e(name)}</div><ul class="eb-inj-l"><li>{head}</li>{ab}</ul>'
                 + (f'<p class="eb-note">Onze ({_e(i["lineup"] or "?")}) : {xi}</p>' if xi else "") + '</div>')
    return ('<div class="eb-card"><h3 class="eb-h">Gestion des crises <small>absences, composition et leur effet chiffré</small></h3>'
            f'<div class="eb-inj">{cols}</div><p class="eb-note">Impact d’un absent = sa part de l’xG de l’équipe quand il joue × '
            'ses minutes habituelles. L’attaque du soir additionne les parts de l’effectif réellement disponible '
            '(les remplaçants comptent avec leur propre niveau).</p></div>')


def _scorers(g):
    rows = ""
    for side in ("home", "away"):
        for s in g[side + "_info"].get("scorers") or []:
            rows += (f'<tr class="eb-prop" data-i="0"><td data-v="{_e(s["name"])}">{_e(s["name"])} <span class="eb-dim">'
                     f'{_e(g[side]["name"])}{"" if s["starter"] else " · remplaçant"}</span></td>'
                     f'<td class="n" data-v="{s["p_goal"]:.4f}"><b>{_pct(s["p_goal"])}</b></td>'
                     f'<td class="n" data-v="{s["shots"]:.2f}">{s["shots"]:.1f}</td><td class="n" data-v="{s["sot"]:.2f}">{s["sot"]:.1f}</td></tr>')
    if not rows: return ""
    head = "".join(f'<th class="eb-sort" onclick="ebSort(this)">{h}</th>' for h in ("Joueur", "Marque", "Tirs", "Cadrés"))
    return (f'<div class="eb-card"><h3 class="eb-h">Buteurs <small>probabilité de marquer (penalties compris) et tirs '
            f'moyens</small></h3><div class="eb-scroll"><table class="eb-tbl" data-all="1"><tr>{head}</tr>{rows}</table></div></div>')


def _players_card(g):
    """Tous les joueurs : moyennes des simulations (sur les matchs ou il joue) et saison reelle, en blocs repliables."""
    if not any((g.get(s + "_info") or {}).get("players") for s in ("home", "away")): return ""
    from nba_sim_site import PL_CSS
    n1 = lambda x, d=1: f"{x:.{d}f}".replace(".", ",")
    sim = sea = ""
    for side in ("home", "away"):
        pl = (g.get(side + "_info") or {}).get("players") or []
        for label, grp in (("titulaires", [p for p in pl if p["starter"]]), ("remplaçants", [p for p in pl if not p["starter"]])):
            if not grp: continue
            sec = f'<tr class="sec"><td colspan="9">{_e(g[side]["name"])} · {label}</td></tr>'
            sim += sec; sea += sec
            for p in grp:
                pos = f' <span class="eb-dim">{_e(p["pos"])}</span>'
                td = lambda v, txt: f'<td class="n" data-v="{v}">{txt}</td>'   # data-v : valeur de tri (ebSortPl)
                sim += (f'<tr><td data-v="{_e(p["name"])}">{_e(p["name"])}{pos}</td>' + td(p["p_play"], _pct(p["p_play"])) + td(p["min"], f'{p["min"]:.0f}')
                        + td(p["p_goal"], f'<b>{_pct(p["p_goal"])}</b>') + td(p["goals"], n1(p["goals"], 2)) + td(p["shots"], n1(p["shots"]))
                        + td(p["sot"], n1(p["sot"])) + td(p["p_yellow"], _pct(p["p_yellow"])) + td(p["p_red"], _pct(p["p_red"], 1)) + '</tr>')
                s = p.get("season") or {}
                sea += (f'<tr><td data-v="{_e(p["name"])}">{_e(p["name"])}{pos}</td>' + td(s.get("app", 0), s.get("app", 0)) + td(s.get("min", 0), s.get("min", 0))
                        + td(s.get("g", 0), f'<b>{s.get("g", 0)}</b>') + td(s.get("sh", 0), s.get("sh", 0)) + td(s.get("so", 0), s.get("so", 0))
                        + td(s.get("y", 0), s.get("y", 0)) + td(s.get("r", 0), s.get("r", 0)) + '<td></td></tr>')
    th = lambda x: f'<th class="eb-sort" onclick="ebSortPl(this)" title="Trier">{x}</th>' if x else "<th></th>"
    h1 = "".join(th(x) for x in ("Joueur", "Joue", "Min", "Marque", "Buts", "Tirs", "Cadrés", "Jaune", "Rouge"))
    h2 = "".join(th(x) for x in ("Joueur", "Matchs", "Min", "Buts", "Tirs", "Cadrés", "Jaunes", "Rouges", ""))
    n = g.get("n_sims") or 10000
    return ('<div class="eb-card"><h3 class="eb-h">Joueurs</h3>' + PL_CSS
            + f'<details class="eb-det"><summary>Moyennes des {n:,} simulations'.replace(",", " ")
            + '<small>sur les matchs où il joue</small></summary>'
            '<p class="eb-note eb-note-top">Joue = part des simulations où il entre en jeu · Marque = probabilité de marquer s’il joue '
            '(penalties compris) · tirs cadrés recalés (backtest) · cartons : taux du joueur sur ses matchs passés ramené à ses '
            'minutes simulées (validé sur 71 962 apparitions : fiabilité moyenne, ±1-3 pts).</p>'
            f'<div class="eb-scroll"><table class="eb-tbl eb-stk"><tr>{h1}</tr>{sim}</table></div></details>'
            '<details class="eb-det"><summary>Saison réelle<small>totaux, compétitions suivies (championnat + coupes d’Europe)</small></summary>'
            f'<div class="eb-scroll"><table class="eb-tbl eb-stk"><tr>{h2}</tr>{sea}</table></div></details>'
            '<p class="eb-note">Passes décisives : pas encore dans la base de matchs (FotMob), à ajouter.</p></div>')


def _scores(g):
    cells = "".join(f'<div class="eb-sc"><b>{h}-{a}</b><span>{_pct(p, 1)}</span></div>' for h, a, p in g["scores"])
    hist = g["goal_hist"]
    mx = max(hist) or 1
    bars = "".join(f'<div class="eb-gh"><i style="--h:{100 * v / mx:.0f}%"></i><span>{_pct(v)}</span><em>{k if k < 7 else "7+"}</em></div>'
                   for k, v in enumerate(hist))
    return (f'<div class="eb-card"><h3 class="eb-h">Scores exacts les plus probables</h3><div class="eb-scg">{cells}</div>'
            f'<h3 class="eb-h eb-ev-t">Nombre de buts dans le match</h3><div class="eb-ghs">{bars}</div></div>')


def _markets(g):
    p, h, a = g["p"], g["home"]["name"], g["away"]["name"]
    rows = [(f"{_e(h)} ou nul", p["1"] + p["X"]), (f"{_e(a)} ou nul", p["2"] + p["X"]), ("Pas de match nul", p["1"] + p["2"])]
    rows += [(f"Plus de {l.replace('.', ',')} buts", v) for l, v in p["over"].items()]
    rows += [(f"Mi-temps : {_e(h)} devant", p["ht"]["1"]), ("Mi-temps : égalité", p["ht"]["X"]), (f"Mi-temps : {_e(a)} devant", p["ht"]["2"]),
             (f"{_e(h)} ne prend pas de but", p["clean"][0]), (f"{_e(a)} ne prend pas de but", p["clean"][1])]
    body = "".join(f'<div class="eb-row"><span>{t}</span><span>{_pct(v, 1)}</span></div>' for t, v in rows)
    return f'<div class="eb-card"><h3 class="eb-h">Autres issues</h3><div class="eb-list2">{body}</div></div>'


# Graphique match par match de la carte « Historique des tirs » (Wilson, 05/11/2026 : « les graphes L10 des tirs »).
# Barres du plus ancien (gauche) au plus recent (droite), vert = au-dessus de la ligne du soir, rouge = en dessous.
SHOTS_SCRIPT = r"""<style>
.eb-shc{margin:10px 0 4px}
.eb-shc-ctl{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.eb-shc-ctl .eb-seg{margin:0;padding:3px}
.eb-shc-ctl .eb-seg button{padding:5px 10px;font-size:12.5px}
.eb-shc-sum{font-size:13.5px;margin-bottom:6px}
.eb-shc-plot{position:relative;display:flex;align-items:flex-end;gap:4px;height:150px;padding-right:40px;border-bottom:1px solid var(--line)}
.eb-shc-b{flex:1;min-width:0;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;cursor:pointer}
.eb-shc-b em{font-style:normal;font-family:var(--mono);font-size:10.5px;color:var(--mu);margin-bottom:3px;white-space:nowrap}
.eb-shc-b i{display:block;width:100%;max-width:34px;min-height:2px;border-radius:4px 4px 0 0;background:var(--line2)}
.eb-shc-b.ok i{background:var(--acc)}
.eb-shc-b.ko i{background:var(--loss);opacity:.8}
.eb-shc-b.sel em{color:var(--tx);font-weight:700}
.eb-shc-ln{position:absolute;left:0;right:0;border-top:2px dashed var(--warn);pointer-events:none}
.eb-shc-ln span{position:absolute;right:0;top:-9px;font:600 11px var(--mono);color:var(--warn);background:var(--panel);padding:0 4px;border-radius:4px}
.eb-shc-x{display:flex;gap:4px;margin-top:4px;padding-right:40px}
.eb-shc-x span{flex:1;min-width:0;text-align:center;font:10px var(--mono);color:var(--mu);overflow:hidden;white-space:nowrap}
.eb-shc-cap{font-size:12.5px;min-height:18px;margin-top:4px}
@media (max-width:520px){.eb-shc-plot{height:130px;gap:2px}.eb-shc-x{gap:2px}.eb-shc-b em{font-size:9.5px}}
</style><script>
(function(){
  function f1(x){ return (Math.round(x * 10) / 10).toFixed(1).replace('.', ','); }
  function q(s){ return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
  function ab(n){ return String(n || '?').replace(/^(FC|AS|AC|SS|SSC|RC|RCD|OGC|SC|US|CA|CD|UD|SD|VfB|VfL|TSG|SV|AFC|1\. ?FC|FK|SK|PSV|AZ)\s+/i, '').slice(0, 3).toUpperCase(); }
  function seg(k, opts, cur){
    return '<div class="eb-seg">' + opts.map(function(o){
      return '<button' + (String(o[0]) === String(cur) ? ' class="on"' : '') + ' onclick="ebShots(this,\'' + k + '\',\'' + o[0] + '\')">' + o[1] + '</button>';
    }).join('') + '</div>';
  }
  window.ebShots = function(el, k, v){
    var c = el.closest('.eb-shc'); c.setAttribute('data-' + k, v);
    if(k === 'k') c.removeAttribute('data-l');
    ebShR(c);
  };
  window.ebShTap = function(b){
    var c = b.closest('.eb-shc');
    c.querySelectorAll('.eb-shc-b.sel').forEach(function(x){ x.classList.remove('sel'); });
    b.classList.add('sel'); c.querySelector('.eb-shc-cap').textContent = b.getAttribute('data-t');
  };
  window.ebShR = function(c){
    var d; try { d = JSON.parse(c.querySelector('script').textContent); } catch(e){ return; }
    var k = c.getAttribute('data-k') || 'sh', n = +c.getAttribute('data-n') || 10;
    var col = {sh: 3, so: 4, adj: 5}[k] || 3, G = d.g.slice(0, n).reverse();
    if(!G.length) return;
    var V = G.map(function(x){ return x[col]; }), avg = V.reduce(function(a, b){ return a + b; }, 0) / V.length;
    var lines = (k === 'so' ? d.L.so : d.L.sh) || [], li = c.getAttribute('data-l');
    if(li === null || +li >= lines.length){      // par defaut : la ligne du soir la plus proche de la moyenne
      li = 0; lines.forEach(function(l, i){ if(Math.abs(+l - avg) < Math.abs(+lines[li] - avg)) li = i; });
      c.setAttribute('data-l', li);
    }
    var line = lines.length ? +lines[+li] : null;
    var top = Math.max.apply(null, V.concat(line == null ? [] : [line])) * 1.25 || 1;
    var over = line == null ? 0 : V.filter(function(v){ return v > line; }).length;
    var lab = {sh: 'tirs', so: 'tirs cadrés', adj: 'tirs ajustés'}[k];
    c.querySelector('.eb-shc-ctl').innerHTML =
      seg('k', [['sh', 'Tirs'], ['so', 'Cadrés'], ['adj', 'Ajustés']], k)
      + seg('n', [[5, 'L5'], [10, 'L10'], [20, 'L20']], n)
      + (lines.length ? seg('l', lines.map(function(l, i){ return [i, '+' + l.replace('.', ',')]; }), li) : '');
    c.querySelector('.eb-shc-sum').innerHTML = 'L' + n + (G.length < n ? ' (' + G.length + ' matchs)' : '') + ' : '
      + (line == null ? '' : '<b>' + over + '/' + G.length + '</b> au-dessus de ' + f1(line) + ' · ')
      + 'moyenne <b>' + f1(avg) + '</b> ' + lab;
    c.querySelector('.eb-shc-plot').innerHTML = G.map(function(x, i){
      var v = V[i], cls = line == null ? '' : (v > line ? ' ok' : ' ko');
      var t = x[0].slice(8) + '/' + x[0].slice(5, 7) + ' · ' + x[1] + ' (' + (x[2] === 'D' ? 'dom.' : 'ext.') + ', ' + x[6] + ') · '
        + x[3] + ' tirs, ' + x[4] + ' cadrés, ajusté ' + f1(x[5]);
      return '<div class="eb-shc-b' + cls + '" title="' + q(t) + '" data-t="' + q(t) + '" onclick="ebShTap(this)"><em>'
        + (k === 'adj' ? f1(v) : v) + '</em><i style="height:' + (100 * v / top).toFixed(1) + '%"></i></div>';
    }).join('') + (line == null ? '' : '<div class="eb-shc-ln" style="bottom:' + (100 * line / top).toFixed(1) + '%"><span>' + f1(line) + '</span></div>');
    c.querySelector('.eb-shc-x').innerHTML = G.map(function(x){ return '<span>' + (G.length <= 10 ? q(ab(x[1])) : '') + '</span>'; }).join('');
    c.querySelector('.eb-shc-cap').textContent = 'Touchez une barre pour le détail du match · ← plus ancien, plus récent →';
  };
  document.querySelectorAll('.eb-shc').forEach(function(c){ ebShR(c); });
})();
</script>"""


VIDEO_SCRIPT = """<script>
window.ebFootVideo=function(btn){
  var card=btn.closest('.eb-video'),host=card.querySelector('.sp-foot-host');if(host.getAttribute('data-ready'))return;
  host.setAttribute('data-ready','1');btn.classList.add('eb-off');
  var d=JSON.parse(card.querySelector('.sp-foot-data').textContent),t=JSON.parse(card.querySelector('.sp-foot-title').textContent);
  SPFoot.open(host,d,t);var p=host.querySelector('[data-a=play]');if(p)p.click();
};
</script>"""


def _player_script():
    try:
        with open("foot_replay_player.js", encoding="utf-8") as f:
            return "<script>" + f.read() + "</script>" + VIDEO_SCRIPT
    except OSError:
        return ""


def _livecard(g):
    e = g.get("espn") or {}
    return site_live.card("soccer", e.get("slug"), e.get("id"))


PICKS_NOTE = ("Un pick foot n’est proposé que sur un marché où le simulateur a battu les cotes de clôture Pinnacle "
              "<b>sur deux saisons séparées</b>. Testé le 29/10/2026 sur ~2 600 matchs du Top 5 : sur le 1X2, suivre le "
              "simulateur contre le marché perd partout (−15 à −20 %) ; sur les plus/moins 2,5 buts, la Serie A semblait "
              "gagnante en 2025-26 (+19 à +28 %) mais perd en 2025 (−3 à −12 %) : c’était du hasard. Aucun marché validé "
              "à ce jour, donc aucun pick. Pas de pick vaut mieux qu’un mauvais pick.")


def _picks_view(games):
    now = datetime.now(TZ)
    rows = ""
    for g in games:
        pk = g.get("pick")
        if not pk: continue
        rows += (f'<tr data-np="foot|{_e(g["id"])}|{_e(pk["label"])}" data-npl="⚽ {_e(g["home"]["name"])} - {_e(g["away"]["name"])} · '
                 f'{_e(pk["label"])} @ {pk["dec"]:.2f}"><td>{_when(g["ko"], now)}</td><td><b>{_e(g["home"]["name"])} - {_e(g["away"]["name"])}</b> '
                 f'<span class="eb-dim">{_e(LIG.get(g["league"], ""))}</span></td>'
                 f'<td><b>{_e(pk["label"])}</b></td><td class="n">{pk["dec"]:.2f}</td>'
                 f'<td class="n">{_pct(pk["p_sim"])}</td><td class="n">{_pct(pk["p_mkt"])}</td>'
                 f'<td class="n eb-acc">+{100 * (pk["p_sim"] - pk["p_mkt"]):.0f} pts</td></tr>')
    table = (f'<div class="eb-scroll"><table class="eb-tbl eb-cards"><tr><th>Coup d’envoi</th><th>Match</th><th>Pari</th><th>Cote</th>'
             f'<th>Simu</th><th>Bookmaker</th><th>Écart</th></tr>{rows}</table></div>' if rows else
             '<div class="eb-empty">Aucun pick foot : aucun match ne remplit la règle validée dans les 48 h. '
             'C’est un choix, pas un oubli.</div>')
    import ev_site
    return ev_site.build_ev_card("foot") + (f'<div class="eb-card"><h3 class="eb-h">Picks du simulateur</h3>{table}<p class="eb-note">{PICKS_NOTE}'
            + (f' Règle en vigueur : {_e(PICK_RULE_TXT)}' if PICK_RULE_TXT else "") + '</p></div>')


def _history_view():
    import foot_sim_bilan as FB
    h = FB.load()
    ds = [d for d, (t, done) in FB.days(h).items() if done][:14]
    if not ds:
        return ('<div class="eb-card eb-empty">Premier bilan après les premiers matchs terminés : chaque match simulé est '
                'archivé avant le coup d’envoi puis comparé au résultat.</div>')
    items, details = "", ""
    for i, d in enumerate(ds):
        rep_ = FB.day_report(d, h)
        sm = rep_["sum"]
        dt = datetime.strptime(d, "%Y-%m-%d")
        items += (f'<button class="eb-item{" on" if i == 0 else ""}" data-id="fday-{d}" onclick="ebPick(\'foothist\',\'fday-{d}\')">'
                  f'<div class="eb-item-top"><span>Bilan</span><span>{JOURS[dt.weekday()]} {dt:%d/%m}</span></div>'
                  f'<div class="eb-item-t">Favori trouvé {sm["hit"]}/{sm["n"]}</div>'
                  f'<div class="eb-chips"><span class="eb-chip"><em>Proba du vrai résultat</em>{_pct(sm["p_true"])}</span></div></button>')
        rows = ""
        for g in rep_["games"]:
            f = g.get("final")
            fav = max(("1", "X", "2"), key=lambda k: g["p"][k])
            res = None if not f else ("1" if f[0] > f[1] else ("X" if f[0] == f[1] else "2"))
            tag = ('<span class="eb-tag">à venir</span>' if not f else
                   f'<span class="eb-tag {"acc" if res == fav else "neg"}">{"trouvé" if res == fav else "raté"}</span>')
            o25 = "" if not f else ("✓" if (g["p"]["o25"] >= 0.5) == (sum(f) > 2.5) else "✗")
            rp = ' <span class="eb-tag" title="Simulation rejouée après coup : état du modèle d’avant le jour du match, composition officielle">rejouée</span>' if g.get("replayed") else ""
            rows += (f'<tr><td><b>{_e(g["home"])} - {_e(g["away"])}</b> <span class="eb-dim">{_e(LIG.get(g["league"], ""))}</span>{rp}</td>'
                     f'<td class="n">{_pct(g["p"]["1"])} / {_pct(g["p"]["X"])} / {_pct(g["p"]["2"])}</td>'
                     f'<td class="n">{g["goals"][0]:.1f}-{g["goals"][1]:.1f}</td>'
                     f'<td class="n">{"<b>" + str(f[0]) + "-" + str(f[1]) + "</b>" if f else "–"}</td>'
                     f'<td class="n">{(str(g["xg"][0]) + "-" + str(g["xg"][1])) if g.get("xg") else "–"} → '
                     f'{(str(g["xg_real"][0]) + "-" + str(g["xg_real"][1])) if g.get("xg_real") else "–"}</td>'
                     f'<td class="n">{_pct(g["p"]["o25"])} {o25}</td><td>{tag}</td></tr>')
        scor = [f'{_e(nm)} ({_pct(p)})' for g in rep_["games"] for nm, p in g.get("scorers") or []
                if p >= 0.35 and nm in (g.get("goalscorers") or [])]
        facts = (f'<ul class="eb-why-l"><li>Favori du simulateur trouvé <b>{sm["hit"]} fois sur {sm["n"]}</b> ; probabilité '
                 f'moyenne donnée au vrai résultat <b>{_pct(sm["p_true"])}</b> (33 % = hasard).</li>'
                 f'<li>Buts : <b>{_n(sm["goals_sim"])}</b> attendus par match, <b>{_n(sm["goals_real"])}</b> réels · xG '
                 f'{_n(sm["xg_sim"])} attendus, {_n(sm["xg_real"])} réels.</li>'
                 f'<li>Plus/moins 2,5 buts du bon côté : {sm["o_hit"]}/{sm["n"]}.</li>'
                 + (f'<li>Buteurs annoncés (35 % et plus) qui ont marqué : {", ".join(scor)}.</li>' if scor else "")
                 + '</ul>')
        details += (f'<section class="eb-detail{" on" if i == 0 else ""}" data-id="fday-{d}">'
                    f'<div class="eb-card"><div class="eb-meta">Bilan · {JOURS_L[dt.weekday()]} {dt.day} {MOIS[dt.month - 1]} · '
                    f'{rep_["done"]}/{rep_["total"]} matchs terminés</div><h3 class="eb-h eb-bil-t">Ce que le simulateur avait prévu, ce qui s’est passé</h3>{facts}</div>'
                    f'<div class="eb-card"><h3 class="eb-h">Matchs <small>1 / N / 2 simulés · buts attendus · score · xG attendus → réels · +2,5</small></h3>'
                    f'<div class="eb-scroll"><table class="eb-tbl eb-cards"><tr><th>Match</th><th>1 / N / 2</th><th>Buts att.</th><th>Score</th>'
                    f'<th>xG</th><th>+2,5</th><th>Favori</th></tr>{rows}</table></div></div>'
                    '<div class="eb-card"><h3 class="eb-h">Comment l’améliorer</h3><ul class="eb-why-l">'
                    + "".join(f"<li>{_e(t)}</li>" for t in FB.insights(h)) + '</ul></div></section>')
    return ('<div class="eb-layout">'
            f'<aside class="eb-side"><div class="eb-label">Bilan des journées · {len(ds)}</div><div class="eb-list">{items}</div></aside>'
            f'<div class="eb-main">{details}</div></div>')


def build_foot_sim_section(sim=None):
    sim = sim if sim is not None else load_sim()
    games = sorted((sim or {}).get("games") or [], key=lambda g: g["ko"])
    now = datetime.now(TZ)
    if games:
        ids = [_e(g["id"]) for g in games]
        items = "".join(_item(g, gid, now, i == 0) for i, (g, gid) in enumerate(zip(games, ids)))
        details = "".join(
            f'<section class="eb-detail{" on" if i == 0 else ""}" data-id="{gid}">{_header(g, now)}{_livecard(g)}{_kpis(g)}{_tactics(g)}{_args(g)}{_shots_card(g)}{_shots_hist_card(g)}{_video(g, gid)}'
            f'{_crisis(g)}{_scorers(g)}{_players_card(g)}{_scores(g)}{_markets(g)}'
            + (f'<div class="eb-card"><p class="eb-note">{BACKTEST_NOTE}</p></div>' if BACKTEST_NOTE else "") + '</section>'
            for i, (g, gid) in enumerate(zip(games, ids)))
        sim_view = ('<div class="eb eb-view on" data-view="sim" data-eb="foot2"><div class="eb-layout">'
                    f'<aside class="eb-side"><div class="eb-label">Matchs · {len(games)}</div><div class="eb-list">{items}</div></aside>'
                    f'<div class="eb-main">{details}</div></div></div>')
    else:
        sim_view = ('<div class="eb eb-view on" data-view="sim" data-eb="foot2"><div class="eb-card eb-empty">Aucun match du '
                    'Top 5 européen ou de coupe d’Europe dans les 48 prochaines heures.</div></div>')
    picks_view = f'<div class="eb eb-view" data-view="picks" data-eb="footpick">{_picks_view(games)}</div>'
    hist_view = f'<div class="eb eb-view" data-view="hist" data-eb="foothist">{_history_view()}</div>'
    from nba_sim_site import PROPS_SCRIPT, ASK_SCRIPT
    return (f'<!--eb:start-->{sim_view}{picks_view}{hist_view}' + EB_SCRIPT + PROPS_SCRIPT + ASK_SCRIPT + site_live.LIVE_SCRIPT
            + _player_script() + SHOTS_SCRIPT + '<!--eb:end-->')


FRAGMENT = "foot_sim.html"


def build_foot_sim_page():
    """(bloc pour index.html, contenu de foot_sim.html) : copie de secours + chargeur (meme principe que
    la section NBA : la veille publie foot_sim.html seul, la page prend la version la plus recente)."""
    sim = load_sim() or {}
    sec = f'<!--foot-sim:{_e(sim.get("generated_at", ""))}-->' + build_foot_sim_section(sim)
    v = sim.get("generated_at", "")
    return f'<div id="eb-foot-wrap" data-v="{_e(v)}">{sec}</div>' + LOADER, sec


def write_fragment(sec=None):
    with open(FRAGMENT, "w", encoding="utf-8") as f:
        f.write(sec if sec is not None else build_foot_sim_page()[1])


LOADER = """<script>
(function(){
  var wrap=document.getElementById('eb-foot-wrap');if(!wrap||window.ebFootLive)return;
  var last=0;
  function run(el){el.querySelectorAll('script:not([type])').forEach(function(s){var n=document.createElement('script');
    n.textContent=s.textContent;s.parentNode.replaceChild(n,s);});}
  window.ebFootLive=function(){
    last=Date.now();
    fetch('""" + FRAGMENT + """?t='+Date.now(),{cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.text();})
      .then(function(h){var m=/^<!--foot-sim:([^>]*)-->/.exec(h);
        if(!m||m[1]<=(wrap.getAttribute('data-v')||''))return;
        var on=wrap.querySelector('.eb-item.on'),id=on&&on.getAttribute('data-id');
        wrap.innerHTML=h;wrap.setAttribute('data-v',m[1]);run(wrap);if(window.spReapply)spReapply();
        if(id&&wrap.querySelector('.eb-item[data-id="'+id+'"]')&&window.ebPick)ebPick('foot2',id);})
      .catch(function(){});
  };
  ebFootLive();
  document.addEventListener('visibilitychange',function(){
    if(document.visibilityState==='visible'&&Date.now()-last>120000)ebFootLive();});
})();
</script>"""
