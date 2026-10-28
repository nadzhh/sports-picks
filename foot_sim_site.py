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


def _item(g, gid, now, first):
    p = g["p"]
    chips = "".join(f'<span class="eb-chip"><em>{k}</em>{_pct(p[k])}</span>' for k in ("1", "X", "2"))
    return (f'<button class="eb-item{" on" if first else ""}" data-id="{gid}" onclick="ebPick(\'foot2\',\'{gid}\')">'
            f'<div class="eb-item-top"><span>{_e(LIG.get(g["league"], g["league_name"]))}</span><span>{_when(g["ko"], now)}</span></div>'
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


def build_foot_sim_section(sim=None):
    sim = sim if sim is not None else load_sim()
    games = sorted((sim or {}).get("games") or [], key=lambda g: g["ko"])
    if not games:
        return ('<!--eb:start--><div class="eb"><div class="eb-card eb-empty">Aucun match du Top 5 européen ou de coupe '
                'd’Europe dans les 48 prochaines heures.</div></div><!--eb:end-->')
    now = datetime.now(TZ)
    ids = [_e(g["id"]) for g in games]
    items = "".join(_item(g, gid, now, i == 0) for i, (g, gid) in enumerate(zip(games, ids)))
    details = "".join(
        f'<section class="eb-detail{" on" if i == 0 else ""}" data-id="{gid}">{_header(g, now)}{_kpis(g)}{_video(g, gid)}'
        f'{_crisis(g)}{_scorers(g)}{_scores(g)}{_markets(g)}'
        + (f'<div class="eb-card"><p class="eb-note">{BACKTEST_NOTE}</p></div>' if BACKTEST_NOTE else "") + '</section>'
        for i, (g, gid) in enumerate(zip(games, ids)))
    from nba_sim_site import PROPS_SCRIPT
    return ('<!--eb:start--><div class="eb" data-eb="foot2"><div class="eb-layout">'
            f'<aside class="eb-side"><div class="eb-label">Matchs à venir · {len(games)}</div><div class="eb-list">{items}</div></aside>'
            f'<div class="eb-main">{details}</div></div></div>' + EB_SCRIPT + PROPS_SCRIPT + _player_script() + '<!--eb:end-->')


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
        wrap.innerHTML=h;wrap.setAttribute('data-v',m[1]);run(wrap);
        if(id&&wrap.querySelector('.eb-item[data-id="'+id+'"]')&&window.ebPick)ebPick('foot2',id);})
      .catch(function(){});
  };
  ebFootLive();
  document.addEventListener('visibilitychange',function(){
    if(document.visibilityState==='visible'&&Date.now()-last>120000)ebFootLive();});
})();
</script>"""
