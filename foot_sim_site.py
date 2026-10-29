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
            f'{side(g["away"]["name"], g["xg"][1], g["goals"][1], ai)}</div></div>')


def _kpis(g):
    p = g["p"]
    return ('<div class="eb-g3">' + _kpi(f'Victoire {_e(g["home"]["name"])}', p["1"]) + _kpi("Match nul", p["X"])
            + _kpi(f'Victoire {_e(g["away"]["name"])}', p["2"]) + '</div>'
            '<div class="eb-g3">' + _kpi("Plus de 2,5 buts", p["over"]["2.5"]) + _kpi("Les deux équipes marquent", p["btts"])
            + _kpi("Au moins un carton rouge", p["red"]) + '</div>')


def _video(g, gid):
    rp = g.get("replay")
    if not rp or not rp.get("events"): return ""
    title = json.dumps({"home": g["home"]["name"], "away": g["away"]["name"],
                        "foot": "Le match type parmi 10 000 simulations : score le plus fréquent du résultat le plus probable"},
                       ensure_ascii=False)
    data = json.dumps(rp, ensure_ascii=False, separators=(",", ":"))
    n_goals = sum(e["kind"] in ("but", "csc") for e in rp["events"])
    return (f'<div class="eb-card eb-video"><div class="eb-ctrl"><h3 class="eb-h eb-h-nomb">Le match type en vidéo '
            f'<small>{rp["final"][0]}-{rp["final"][1]} · {len(rp["events"])} actions, {n_goals} but{"s" if n_goals > 1 else ""}</small></h3>'
            f'<button class="eb-more" onclick="ebFootVideo(this)">▶ Regarder</button></div>'
            f'<div class="eb-video-host sp-foot-host"></div>'
            f'<script type="application/json" class="sp-foot-data">{data.replace("</", "<\\/")}</script>'
            f'<script type="application/json" class="sp-foot-title">{title.replace("</", "<\\/")}</script>'
            '<p class="eb-note">Un vrai scénario simulé de bout en bout : chaque tir est un tir réel passé du joueur '
            '(sa position, son xG). Ce n’est pas un pronostic de score exact.</p></div>')


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
        rows += (f'<tr><td>{_when(g["ko"], now)}</td><td><b>{_e(g["home"]["name"])} - {_e(g["away"]["name"])}</b> '
                 f'<span class="eb-dim">{_e(LIG.get(g["league"], ""))}</span></td>'
                 f'<td><b>{_e(pk["label"])}</b></td><td class="n">{pk["dec"]:.2f}</td>'
                 f'<td class="n">{_pct(pk["p_sim"])}</td><td class="n">{_pct(pk["p_mkt"])}</td>'
                 f'<td class="n eb-acc">+{100 * (pk["p_sim"] - pk["p_mkt"]):.0f} pts</td></tr>')
    table = (f'<div class="eb-scroll"><table class="eb-tbl"><tr><th>Coup d’envoi</th><th>Match</th><th>Pari</th><th>Cote</th>'
             f'<th>Simu</th><th>Bookmaker</th><th>Écart</th></tr>{rows}</table></div>' if rows else
             '<div class="eb-empty">Aucun pick foot : aucun match ne remplit la règle validée dans les 48 h. '
             'C’est un choix, pas un oubli.</div>')
    return (f'<div class="eb-card"><h3 class="eb-h">Picks foot</h3>{table}<p class="eb-note">{PICKS_NOTE}'
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
                    f'<div class="eb-scroll"><table class="eb-tbl"><tr><th>Match</th><th>1 / N / 2</th><th>Buts att.</th><th>Score</th>'
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
            f'<section class="eb-detail{" on" if i == 0 else ""}" data-id="{gid}">{_header(g, now)}{_livecard(g)}{_kpis(g)}{_video(g, gid)}'
            f'{_crisis(g)}{_scorers(g)}{_scores(g)}{_markets(g)}'
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
    from nba_sim_site import PROPS_SCRIPT
    return (f'<!--eb:start-->{sim_view}{picks_view}{hist_view}' + EB_SCRIPT + PROPS_SCRIPT + site_live.LIVE_SCRIPT
            + _player_script() + '<!--eb:end-->')


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
