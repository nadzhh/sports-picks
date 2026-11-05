"""
site_today.py — Onglet « Aujourd'hui » (demande de Wilson, 05/11/2026 : « améliorer le frontend », point 2).

Constat : pour savoir quoi jouer ce soir, il fallait ouvrir au moins 4 onglets, et les picks du jour du canal
Telegram (paliers value / safe / meilleur prix, picks_du_jour.py) n'apparaissaient nulle part sur le site.
Cette page les regroupe : picks du jour par palier (recalcules a chaque mise a jour, avec les cotes du moment),
matchs des 24 prochaines heures (probabilites de la simulation, lien vers la fiche), resume de la Bankroll IA.

Comme les sections Simulation : ecrite dans today.html par la veille (nba_live.publish, a chaque publication) et par
le cron (generate_site) ; index.html en garde une copie de secours remplacee si today.html est plus recent.
"""
import html
from datetime import datetime, timedelta, timezone

import picks_du_jour as PJ

FRAGMENT = "today.html"
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Paris")
except Exception:
    TZ = timezone(timedelta(hours=1))
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _fr(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def _pc(x):
    return f"{round(100 * x)} %"


def _t(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def _hm(iso):
    try:
        return _t(iso).astimezone(TZ).strftime("%H:%M")
    except Exception:
        return ""


def _day(iso, now):
    d = _t(iso).astimezone(TZ).date()
    n = now.astimezone(TZ).date()
    return "" if d == n else "demain " if d == n + timedelta(days=1) else f"{d:%d/%m} "


def _games():
    sims = (PJ._load("data/foot_sim.json", {}) or {}).get("games") or []
    nba = (PJ._load("data/nba_sim.json", {}) or {}).get("games") or []
    return sims, nba


def _link(x, sims, nba):
    """(onglet, identifiant) de la fiche du match d'un pick, ou None."""
    try:
        ko = _t(x["ko"])
        if x["kind"] == "prop" or x.get("src") == "prop":
            return ("nba", x["match_id"])
        home, away = (x["res"] or {}).get("home"), (x["res"] or {}).get("away")
        if x["sport"] == "foot":
            g = next((g for g in sims if abs(_t(g["ko"]) - ko) <= timedelta(hours=3)
                      and PJ._same(g["home"]["name"], home) + PJ._same(g["away"]["name"], away) >= 1.3), None)
            return ("foot", g["id"]) if g else None
        g = next((g for g in nba if abs(_t(g["date"]) - ko) <= timedelta(hours=3)
                  and PJ._same(g["home"].get("name"), home) + PJ._same(g["away"].get("name"), away) >= 1.3), None)
        return ("nba", g["gid"]) if g else None
    except Exception:
        return None


def _go(link, label="Fiche"):
    if not link: return ""
    return f'<button class="td-go" onclick="spToday(\'{link[0]}\',\'{_e(link[1])}\')">{label} →</button>'


def _pick_row(x, sims, nba):
    ico = "⚽" if x["sport"] == "foot" else "🏀"
    kind = x.get("src") if x["kind"] == "safe" else x["kind"]
    ref = (f"juste Pinnacle {_fr(1 / x['p_fair'])}" if kind in ("value", "best") else f"marché {_pc(x['p_fair'])}")
    if x.get("p_sim") is not None: ref += f" · ma simu {_pc(x['p_sim'])}"
    ev = (f'<span class="eb-acc">espérance <b>{100 * x["ev"]:+.0f} %</b></span>' if x["ev"] >= 0
          else f'<span class="eb-dim">coût attendu <b>{_fr(100 * -x["ev"], 1)} %</b></span>')
    return (f'<div class="td-pick"><div class="td-l1"><span>{ico} <b>{_e(x["match"])}</b> '
            f'<span class="eb-dim">· {_hm(x["ko"])} · {_e(x["league"])}</span></span>{_go(_link(x, sims, nba))}</div>'
            f'<div class="td-l2"><b>{_e(x["label"])}</b> <span class="td-odds">@ {_fr(x["price"])}</span> '
            f'<span class="eb-dim">{_e(x["book"])}</span></div>'
            f'<div class="td-l3">gagné {_pc(x["p"])} <span class="eb-dim">({ref})</span> · {ev}</div></div>')


def _tier_stats(kinds):
    s = PJ.stats(kinds)
    if not s: return '<p class="eb-note">Bilan affiché dès les premiers paris réglés.</p>'
    u = _fr(s["units"], 1)
    return (f'<p class="eb-note">Bilan depuis le {s["since"][8:]}/{s["since"][5:7]} : {s["w"]}-{s["n"] - s["w"]}, '
            f'{u if s["units"] < 0 else "+" + u} u (ROI {100 * s["roi"]:+.0f} %).</p>')


def _picks_card(now, sims, nba):
    value = PJ.select(now)
    safe = PJ.safe_picks(now, taken={x["match_id"] for x in value})
    best = PJ.best_price(now, taken={x["match_id"] for x in value + safe})
    out = ('<div class="eb-card"><h3 class="eb-h">🎯 Picks du jour <small>24 prochaines heures · probabilités recalées '
           'sur les résultats réels</small></h3>')
    out += '<div class="td-tier">Value <span class="eb-tag acc">espérance positive</span></div>'
    out += ("".join(_pick_row(x, sims, nba) for x in value) if value else
            '<p class="td-none">Aucun pari ne bat la marge du bookmaker en ce moment. Mieux vaut ne rien jouer que jouer '
            'un pari perdant sur la durée.</p>')
    out += _tier_stats(("value", "prop"))
    if safe:
        out += ('<div class="td-tier">🛡️ Safe <span class="eb-tag">cote ≥ 1,40 · gagné ≥ 2 fois sur 3</span></div>'
                + "".join(_pick_row(x, sims, nba) for x in safe)
                + '<p class="eb-note">Safe ne veut pas dire sûr : ils se perdent environ 1 fois sur 3.</p>' + _tier_stats(("safe",)))
    if best:
        m = PJ.fr_margin()
        out += ('<div class="td-tier">💡 Meilleurs prix <span class="eb-tag">pas de la value : marge la plus faible</span></div>'
                + "".join(_pick_row(x, sims, nba) for x in best)
                + (f'<p class="eb-note">Un pari français moyen coûte {_fr(100 * -m, 1)} % aujourd’hui ; ceux-ci coûtent '
                   'moins, et ma simulation est d’accord.</p>' if m else "") + _tier_stats(("best",)))
    return out + ('<p class="eb-note">Recalculés à chaque mise à jour avec les cotes du moment ; le canal Telegram poste '
                  'la sélection de 20 h. Vérifie la cote chez ton bookmaker avant de jouer.</p></div>')


def _matches_card(now, sims, nba):
    rows = []
    for g in sims:
        ko = _t(g["ko"])
        if not -timedelta(hours=2) <= ko - now <= timedelta(hours=24): continue
        p = g.get("p") or {}
        pr = (f'1 {_pc(p["1"])} · X {_pc(p["X"])} · 2 {_pc(p["2"])}' if all(k in p for k in ("1", "X", "2")) else "")
        rows.append((ko, "⚽", g.get("league_name", ""), f'{g["home"]["name"]} - {g["away"]["name"]}', pr, ("foot", g["id"])))
    for g in nba:
        ko = _t(g["date"])
        if not -timedelta(hours=3) <= ko - now <= timedelta(hours=24): continue
        ph = (g.get("team") or {}).get("p_home_win")
        pr = f'{g["home"]["abbr"]} {_pc(ph)} · {g["away"]["abbr"]} {_pc(1 - ph)}' if ph is not None else ""
        rows.append((ko, "🏀", "NBA", f'{g["away"]["name"]} @ {g["home"]["name"]}', pr, ("nba", g["gid"])))
    if not rows:
        return ('<div class="eb-card"><h3 class="eb-h">📅 Matchs du jour</h3><div class="eb-empty">Aucun match suivi '
                'dans les 24 prochaines heures.</div></div>')
    body = ""
    for ko, ico, lg, name, pr, link in sorted(rows, key=lambda r: r[0]):
        live = ko <= now
        when = ('<span class="td-live">en cours</span>' if live else
                f'{_day(ko.isoformat(), now)}{ko.astimezone(TZ):%H:%M}')
        body += (f'<div class="td-m"><span class="td-when">{when}</span><span class="td-mn">{ico} <b>{_e(name)}</b>'
                 f'<span class="eb-dim"> · {_e(lg)}</span><br><span class="td-pr">{pr}</span></span>{_go(link, "Simu")}</div>')
    n_f = sum(r[1] == "⚽" for r in rows)
    return (f'<div class="eb-card"><h3 class="eb-h">📅 Matchs du jour <small>{n_f} foot · {len(rows) - n_f} NBA · '
            f'probabilités de ma simulation</small></h3>{body}</div>')


def _bank_card():
    try:
        import nba_bankroll as BK
        import nba_bankroll_site as BS
        st = BK.load()
        eq, start = BK.equity(st), st["start"]
    except Exception:
        return ""
    open_ = [b for b in st["bets"] if b["status"] == "open"]
    done = [b for b in st["bets"] if b["status"] in ("won", "lost")]
    w = sum(b["status"] == "won" for b in done)
    rows = "".join(f'<div class="td-bet"><span>{"<br>".join(BS._leg(l) for l in b["legs"])}</span>'
                   f'<span class="td-odds">{BS._eur(b["stake"])} @ {_fr(b["odds"])}</span></div>' for b in open_)
    return ('<div class="eb-card"><h3 class="eb-h">🤖 Bankroll IA <small>mes paris, gérés seul</small></h3><div class="eb-stats">'
            + BS._stat("Capital", BS._eur(eq), f'{BS._pct(eq / start - 1, True)} depuis {BS._eur(start)}',
                       "eb-acc" if eq >= start else "eb-neg")
            + BS._stat("En jeu", BS._eur(sum(b["stake"] for b in open_)), f'{len(open_)} pari{"s" if len(open_) > 1 else ""} en cours')
            + BS._stat("Paris réglés", f'{w}-{len(done) - w}') + '</div>'
            + (f'<div class="td-bets">{rows}</div>' if rows else '<p class="td-none">Aucun pari en cours.</p>')
            + '<button class="td-go td-more" onclick="spNav(\'iabank\')">Toute la Bankroll IA →</button></div>')


CSS = """<style>
.td-head{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap;margin:0 0 14px}
.td-head h2{font-size:22px;font-weight:600;margin:0;letter-spacing:-.4px}
.td-grid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:16px;align-items:start}
.td-col{display:flex;flex-direction:column;gap:16px;min-width:0}
.td-tier{font-weight:600;font-size:15px;margin:16px 0 4px}
.td-tier:first-of-type{margin-top:0}
.td-pick{padding:10px 0;border-top:1px solid var(--line)}
.td-l1{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:13.5px}
.td-l2{font-size:15px;margin:4px 0 2px}
.td-l3{font-size:12.5px;color:var(--mu)}
.td-odds{font-family:var(--mono);font-weight:600;color:var(--acc);white-space:nowrap}
.td-none{color:var(--mu);font-size:13.5px;margin:6px 0}
.td-go{border:1px solid var(--line2);background:var(--chip);color:var(--tx);border-radius:8px;padding:5px 10px;font:600 12px var(--sans);cursor:pointer;white-space:nowrap;flex-shrink:0}
.td-go:hover{border-color:var(--acc);color:var(--acc)}
.td-more{margin-top:12px}
.td-m{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid var(--line);font-size:13.5px}
.td-m:first-of-type{border-top:0}
.td-when{font-family:var(--mono);font-size:12px;color:var(--mu);min-width:62px}
.td-mn{flex:1;min-width:0}
.td-pr{font-family:var(--mono);font-size:11.5px;color:var(--mu)}
.td-live{color:var(--loss);font-weight:700}
.td-bets{margin-top:12px}
.td-bet{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-top:1px solid var(--line);font-size:13px}
@media (max-width:900px){.td-grid{grid-template-columns:1fr}}
</style>"""

SCRIPT = """<script>
window.spToday=function(sp,id){
  if(sp==='foot'){spNav('foot','footsim');if(window.ebPick)ebPick('foot2',id);}
  else{spNav('basket','nbasim');if(window.ebPick)ebPick('nba',id);}
  setTimeout(function(){var d=document.querySelector('[data-eb="'+(sp==='foot'?'foot2':'nba')+'"] .eb-detail.on');
    if(d)d.scrollIntoView({behavior:'smooth',block:'start'});},120);
};
</script>"""


def build_today_section(now=None):
    now = now or datetime.now(timezone.utc)
    sims, nba = _games()
    loc = now.astimezone(TZ)
    try:
        picks = _picks_card(now, sims, nba)
    except Exception as e:
        print(f"  [!] Aujourd'hui : picks du jour indisponibles ({type(e).__name__}: {e})")
        picks = '<div class="eb-card"><h3 class="eb-h">🎯 Picks du jour</h3><div class="eb-empty">Indisponibles pour le moment.</div></div>'
    stamp = now.isoformat(timespec="seconds")
    return (f'<!--today:{stamp}--><!--eb:start--><div class="eb">'
            f'<div class="td-head"><h2>{JOURS[loc.weekday()].capitalize()} {loc.day} {MOIS[loc.month - 1]}</h2>'
            f'<span class="eb-meta">mis à jour à {loc:%H:%M}</span></div>'
            f'<div class="td-grid"><div class="td-col">{picks}{_bank_card()}</div>'
            f'<div class="td-col">{_matches_card(now, sims, nba)}</div></div></div>'
            + CSS + SCRIPT + '<!--eb:end-->')


def write_fragment(sec=None):
    with open(FRAGMENT, "w", encoding="utf-8") as f:
        f.write(sec if sec is not None else build_today_section())


def build_today_page():
    """(bloc pour index.html, contenu de today.html) : copie de secours + chargeur."""
    sec = build_today_section()
    v = sec[len("<!--today:"):sec.index("-->")]
    return f'<div id="eb-today-wrap" data-v="{_e(v)}">{sec}</div>' + LOADER, sec


LOADER = """<script>
(function(){
  var wrap=document.getElementById('eb-today-wrap');if(!wrap||window.ebTodayLive)return;
  var last=0;
  function run(el){el.querySelectorAll('script:not([type])').forEach(function(s){var n=document.createElement('script');
    n.textContent=s.textContent;s.parentNode.replaceChild(n,s);});}
  window.ebTodayLive=function(){
    last=Date.now();
    fetch('""" + FRAGMENT + """?t='+Date.now(),{cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.text();})
      .then(function(h){var m=/^<!--today:([^>]*)-->/.exec(h);
        if(!m||m[1]<=(wrap.getAttribute('data-v')||''))return;
        wrap.innerHTML=h;wrap.setAttribute('data-v',m[1]);run(wrap);})
      .catch(function(){});
  };
  ebTodayLive();
  document.addEventListener('visibilitychange',function(){
    if(document.visibilityState==='visible'&&Date.now()-last>120000)ebTodayLive();});
})();
</script>"""
