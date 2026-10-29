"""
nba_bankroll_site.py — Onglet « Bankroll IA » (data/nba_bankroll.json, gere par nba_bankroll / nba_live).

Comme la section Simulation NBA : la page est ecrite dans nba_bankroll.html (publie par la veille a
chaque pari place ou regle) ; index.html en garde une copie de secours remplacee si plus recente.
"""
import html
from datetime import datetime, timedelta, timezone

import nba_bankroll as BK

FRAGMENT = "nba_bankroll.html"
UNIT = {"AST": "passes", "3PM": "paniers à 3 pts", "STL": "interceptions", "BLK": "contres"}
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Paris")
except Exception:
    TZ = timezone(timedelta(hours=1))


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _eur(x, sign=False):
    s = f"{x:+.2f}" if sign else f"{x:.2f}"
    return s.replace(".", ",").replace("-", "−") + " €"


def _pct(x, sign=False):
    s = f"{100 * x:+.1f}" if sign else f"{100 * x:.0f}"
    return s.replace(".", ",").replace("-", "−") + " %"


def _when(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ).strftime("%d/%m %H:%M")
    except Exception:
        return ""


def _leg(l):
    res = l.get("result")
    mark = {"win": " ✓", "loss": " ✗", "void": " (remboursé)", "push": " (nul)"}.get(res, "")
    if l.get("ev_key"):   # value bet (cote francaise au-dessus de la cote juste Pinnacle)
        return (f'<b>{_e(l["name"])}</b> <span class="eb-dim">{_e(l["team"])}</span> · {_e(l["stat"])} chez {_e(l["opp"])} '
                f'@ {l["dec"]:.2f}{mark} <span class="eb-tag acc">value</span>')
    act = f' <span class="eb-dim">réel {l["actual"]:g}</span>' if l.get("actual") is not None else ""
    return (f'<b>{_e(l["name"])}</b> <span class="eb-dim">{_e(l["team"])} vs {_e(l["opp"])}</span> · '
            f'{"Over" if l["side"] == "o" else "Under"} {l["line"]:g} {_e(UNIT.get(l["stat"], l["stat"]))} '
            f'@ {l["dec"]:.2f}{mark}{act}')


def _curve(state):
    """Courbe du capital (SVG) : un point par pari regle, dans l'ordre de reglement."""
    pts, eq = [state["start"]], state["start"]
    for b in sorted((b for b in state["bets"] if b["status"] != "open"), key=lambda b: b.get("settled_at", "")):
        eq += b.get("pnl", 0.0); pts.append(eq)
    if len(pts) < 2:
        return '<div class="eb-empty">La courbe apparaîtra après les premiers paris réglés.</div>'
    lo, hi = min(pts + [state["start"]]), max(pts + [state["start"]])
    span = max(hi - lo, 1.0)
    W, H = 600, 160
    xy = [(i * W / (len(pts) - 1), H - 10 - (v - lo) / span * (H - 20)) for i, v in enumerate(pts)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(xy))
    y0 = H - 10 - (state["start"] - lo) / span * (H - 20)
    col = "var(--acc)" if pts[-1] >= state["start"] else "var(--loss)"
    return (f'<svg class="eb-bk-curve" viewBox="0 0 {W} {H}" preserveAspectRatio="none" role="img" '
            f'aria-label="Évolution du capital">'
            f'<line x1="0" y1="{y0:.1f}" x2="{W}" y2="{y0:.1f}" stroke="var(--line2)" stroke-dasharray="4 4"/>'
            f'<path d="{path}" fill="none" stroke="{col}" stroke-width="2.5" vector-effect="non-scaling-stroke"/></svg>')


def _stat(label, value, sub="", cls=""):
    return (f'<div class="eb-stat"><span class="eb-kpi-l">{label}</span><span class="eb-stat-v {cls}">{value}</span>'
            + (f'<span class="eb-kpi-s">{sub}</span>' if sub else "") + '</div>')


def _bet_row(b, open_):
    legs = "<br>".join(_leg(l) for l in b["legs"])
    kind = '<span class="eb-tag acc">combiné</span>' if b["kind"] == "combine" else ""
    why = (f'{"cote juste Pinnacle " + format(1 / b["p"], ".2f") + " · " if b["kind"] == "value" else ""}proba estimée {_pct(b["p"])} · espérance {_pct(b["ev"], True)}'
           + (f' · simu {_pct(b["legs"][0]["p_sim"])} vs bookmaker {_pct(b["legs"][0]["p_mkt"])}' if len(b["legs"]) == 1 else ""))
    if open_:
        res = f'<td class="n">{_eur(b["stake"] * b["odds"])}</td>'
    else:
        cls = {"won": "acc", "lost": "neg"}.get(b["status"], "")
        lab = {"won": "Gagné", "lost": "Perdu", "void": "Remboursé"}.get(b["status"], b["status"])
        res = (f'<td><span class="eb-tag {cls}">{lab}</span></td>'
               f'<td class="n {"eb-acc" if b["pnl"] > 0 else "eb-neg" if b["pnl"] < 0 else ""}">{_eur(b["pnl"], True)}</td>')
    return (f'<tr><td class="n eb-dim">{_when(b["placed_at"])}</td><td class="eb-bk-legs">{legs} {kind}'
            f'<div class="eb-dim eb-bk-why">{why}</div></td><td class="n"><b>{b["odds"]:.2f}</b></td>'
            f'<td class="n">{_eur(b["stake"])}</td>{res}</tr>')


RULES = [
    "<b>Value bets</b> (seul avantage validé sur deux saisons) : quand Winamax, Betclic, Unibet ou PMU paient plus que "
    "la cote juste de Pinnacle (marge retirée), je mise 1/4 de Kelly, 2 % du capital au plus. Rare : les bookmakers "
    "français prennent 5 à 14 % de marge.",
    "<b>Discipline</b> : je juge l’avantage sur toutes les sélections du simulateur. Si les cotes obtenues ne battent "
    "pas la clôture (CLV négative), les paris ordinaires s’arrêtent : je ne garde que les paris nés d’une "
    "<b>information fraîche</b> (un joueur du match change de statut et je resimule avant que la cote bouge), "
    "à demi-mise et 3 % du capital par soirée maximum. Écart de 20 points ou plus avec le bookmaker : jamais "
    "(c’est presque toujours une info qui me manque).",
    "Je ne parie que là où un avantage est démontré : la sélection du simulateur (passes, paniers à 3 pts, "
    "interceptions, contres), validée en aveugle sur trois échantillons de vraies cotes, prise à la <b>première cote vue</b>.",
    "Je ne crois pas le simulateur sur parole : ma probabilité garde {lam} de son écart avec le bookmaker "
    "(calibré sur l’historique). Pas d’espérance d’au moins {ev} → pas de pari.",
    "Mise : {kel} de Kelly sur cette probabilité prudente, au plus {mb} du capital par pari, {mg} par match "
    "(paris liés) et {mn} par soirée. Un seul pari par joueur.",
    "Un pari ne s’annule pas : tant qu’une info peut changer le rôle du joueur (coéquipier important "
    "incertain, rapport officiel pas encore rempli), <b>j’attends</b>. Dès que le statut tombe, le match est "
    "resimulé et je décide avec l’info confirmée. À {wu} min du match, je tranche avec la dernière simulation. "
    "Si le joueur parié ne joue pas, le pari est remboursé.",
    "Combiné : au plus un par soirée, deux matchs différents, seulement si son espérance dépasse celle des "
    "simples et {cev} ; mise ≤ {cm} du capital (variance et marge du bookmaker multipliées).",
]


def build_bankroll_section(state=None):
    state = state if state is not None else BK.load()
    p = BK.PRM
    eq, start = BK.equity(state), state["start"]
    open_ = [b for b in state["bets"] if b["status"] == "open"]
    done = [b for b in state["bets"] if b["status"] in ("won", "lost")]
    stakes = sum(b["stake"] for b in done)
    w = sum(b["status"] == "won" for b in done)
    peak, dd, cur = start, 0.0, start
    for b in sorted((b for b in state["bets"] if b["status"] != "open"), key=lambda b: b.get("settled_at", "")):
        cur += b.get("pnl", 0.0); peak = max(peak, cur); dd = max(dd, (peak - cur) / peak)
    stats = (_stat("Capital", _eur(eq), f'{_pct(eq / start - 1, True)} depuis {_eur(start)}',
                   "eb-acc" if eq >= start else "eb-neg")
             + _stat("En jeu", _eur(sum(b["stake"] for b in open_)), f'{len(open_)} pari{"s" if len(open_) > 1 else ""} en cours')
             + _stat("Paris réglés", f'{w}-{len(done) - w}', f'ROI {_pct((eq - start) / stakes, True) if stakes else "–"} sur {_eur(stakes)} misés')
             + _stat("Pire baisse", _pct(dd), "depuis un plus haut"))
    md = state.get("mode") or {}
    mode_txt = {"normal": "normal", "prudent": "prudent (mises divisées par 2)", "pause": "info (paris ordinaires en pause)"}
    stats += _stat("Mode", mode_txt.get(md.get("mode"), "normal").split(" ")[0],
                   (f'CLV {_pct(md["clv"], True)} sur {md["n"]} sélections' if md.get("n") else "CLV mesurée dès les premiers paris"),
                   "eb-neg" if md.get("mode") in ("prudent", "pause") else "eb-acc")
    rules = "".join(f"<li>{r}</li>" for r in RULES).format(
        lam=_pct(p["lambda"]), ev=_pct(p["min_ev"]), kel=f'{p["kelly"]:.2f}'.replace(".", ","), mb=_pct(p["max_bet"]),
        mg=_pct(p["max_game"]), mn=_pct(p["max_night"]), cev=_pct(p["combo_ev"]), cm=_pct(p["combo_max"]),
        wu=p["wait_until"])
    wait = sorted((state.get("waiting") or {}).values(), key=lambda w: w["date"])
    w_rows = "".join(
        f'<tr><td class="n eb-dim">{_when(w["date"])}</td><td class="eb-bk-legs"><b>{_e(w["name"])}</b> '
        f'<span class="eb-dim">{_e(w["team"])} vs {_e(w["opp"])}</span> · {"Over" if w["side"] == "o" else "Under"} '
        f'{w["line"]:g} {_e(UNIT.get(w["stat"], w["stat"]))} @ {w["dec"]:.2f}'
        f'<div class="eb-dim eb-bk-why">attend : {_e(" ; ".join(w["why"]))}</div></td>'
        f'<td class="n">{_pct(w["ev"], True)}</td></tr>' for w in wait)
    o_rows = "".join(_bet_row(b, True) for b in sorted(open_, key=lambda b: b["placed_at"], reverse=True))
    d_rows = "".join(_bet_row(b, False) for b in sorted((b for b in state["bets"] if b["status"] != "open"),
                                                        key=lambda b: b.get("settled_at", ""), reverse=True)[:60])
    head = '<tr><th>Placé</th><th>Pari</th><th>Cote</th><th>Mise</th>'
    return (f'<!--nba-bank:{_e(state.get("updated", ""))}-->'
            '<!--eb:start--><div class="eb eb-bk" data-eb="bank">'
            '<div class="eb-card"><div class="eb-meta">Bankroll IA · NBA · gérée seule par le simulateur</div>'
            '<h3 class="eb-h eb-bil-t">100 € de départ, aucune intervention humaine</h3>'
            f'<div class="eb-stats">{stats}</div>{_curve(state)}'
            '<p class="eb-note">Bankroll fictive, cotes DraftKings publiées par ESPN : aucun argent réel. '
            'Les cotes Winamax / Betclic peuvent différer.</p></div>'
            '<div class="eb-card"><h3 class="eb-h">Paris en cours</h3>'
            + (f'<div class="eb-scroll"><table class="eb-tbl">{head}<th>Gain possible</th></tr>{o_rows}</table></div>' if o_rows
               else '<div class="eb-empty">Aucun pari en cours : j’attends une cote qui en vaut la peine.</div>') + '</div>'
            + (f'<div class="eb-card"><h3 class="eb-h">En attente d’information <small>pari repéré, pas encore placé</small></h3>'
               f'<div class="eb-scroll"><table class="eb-tbl"><tr><th>Match</th><th>Pari envisagé</th><th>Espérance actuelle</th></tr>'
               f'{w_rows}</table></div></div>' if w_rows else "") +
            '<div class="eb-card"><h3 class="eb-h">Historique</h3>'
            + (f'<div class="eb-scroll"><table class="eb-tbl">{head}<th>Résultat</th><th>Gain</th></tr>{d_rows}</table></div>' if d_rows
               else '<div class="eb-empty">Pas encore de pari réglé.</div>') + '</div>'
            f'<div class="eb-card"><h3 class="eb-h">Comment je gère</h3><ul class="eb-why-l">{rules}</ul>'
            + (f'<p class="eb-note">{BK.CALIB_NOTE}</p>' if BK.CALIB_NOTE else "") + '</div>'
            '</div><!--eb:end-->')


def write_fragment(sec=None):
    with open(FRAGMENT, "w", encoding="utf-8") as f:
        f.write(sec if sec is not None else build_bankroll_section())


def build_bankroll_page():
    """(bloc pour index.html, contenu de nba_bankroll.html) : copie de secours + chargeur."""
    sec = build_bankroll_section()
    v = sec[len("<!--nba-bank:"):sec.index("-->")]
    return f'<div id="eb-bank-wrap" data-v="{v}">{sec}</div>' + LOADER, sec


LOADER = """<script>
(function(){
  var wrap=document.getElementById('eb-bank-wrap');if(!wrap||window.ebBankLive)return;
  var last=0;
  window.ebBankLive=function(){
    last=Date.now();
    fetch('""" + FRAGMENT + """?t='+Date.now(),{cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.text();})
      .then(function(h){var m=/^<!--nba-bank:([^>]*)-->/.exec(h);
        if(m&&m[1]>(wrap.getAttribute('data-v')||'')){wrap.innerHTML=h;wrap.setAttribute('data-v',m[1]);}})
      .catch(function(){});
  };
  ebBankLive();
  document.addEventListener('visibilitychange',function(){
    if(document.visibilityState==='visible'&&Date.now()-last>120000)ebBankLive();});
})();
</script>"""
