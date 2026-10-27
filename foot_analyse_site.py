"""
foot_analyse_site.py — Onglet « Analyse Foot » : liste des matchs a gauche, fiche du match a droite.

Donnees : champ "analyse" de data/matches.json (foot_analyse.py) — buts attendus
lam_h / lam_a (loi de Poisson independante), 1X2 mi-temps, BTTS, reperes du
championnat, buteurs/passeurs probables, value bets — et pre_match_form (forme, classement).

Le rendu initial est le calcul EXACT de la loi de Poisson ; le bouton « Lancer la
simulation » rejoue le match N fois dans le navigateur avec les memes lam_h / lam_a
(Monte-Carlo), donc les chiffres bougent seulement du bruit d'echantillonnage.

Styles : composants .eb-* de site_theme.py. Visibilite par classes uniquement
(optimize_html convertit les styles en ligne en classes).
"""
import html
import math
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    TZ_PARIS = ZoneInfo("Europe/Paris")
except Exception:
    TZ_PARIS = timezone(timedelta(hours=1))

JOURS = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]
FORM = {"W": ("v", "V"), "D": ("n", "N"), "L": ("d", "D")}
MAX_GOALS = 10


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _pct(x, d=1):
    return f"{100 * x:.{d}f}".replace(".", ",") + " %"


def when(ts, now=None):
    """'Auj. 21:00', 'Dem. 18:30' ou 'Sam. 21:00' (heure de Paris)."""
    if not ts: return ""
    dt = datetime.fromtimestamp(ts, TZ_PARIS)
    now = now or datetime.now(TZ_PARIS)
    if dt.date() == now.date(): day = "Auj."
    elif dt.date() == now.date() + timedelta(days=1): day = "Dem."
    else: day = JOURS[dt.weekday()]
    return f"{day} {dt:%H:%M}"


def _pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def poisson_view(lh, la):
    """Probabilites exactes : 1X2, plus de 2,5 buts, BTTS, 6 scores les plus probables, total 0..7+."""
    ph = [_pmf(k, lh) for k in range(MAX_GOALS + 1)]
    pa = [_pmf(k, la) for k in range(MAX_GOALS + 1)]
    grid = {(i, j): ph[i] * pa[j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1)}
    win = sum(p for (i, j), p in grid.items() if i > j)
    draw = sum(p for (i, j), p in grid.items() if i == j)
    tot = [0.0] * 8
    for (i, j), p in grid.items():
        tot[min(i + j, 7)] += p
    return {
        "ph": win, "pd": draw, "pa": max(0.0, 1 - win - draw),
        "o25": 1 - sum(tot[:3]), "btts": (1 - ph[0]) * (1 - pa[0]),
        "scores": sorted(grid.items(), key=lambda x: -x[1])[:6], "tot": tot,
    }


def _form_html(form):
    cells = "".join(f'<i class="{FORM[r][0]}">{FORM[r][1]}</i>' for r in (form or []) if r in FORM)
    return f'<div class="eb-form" title="du plus ancien au plus récent">{cells}</div>' if cells else ""


def _book_1x2(m):
    """Cotes bookmaker 1X2 si disponibles (match_odds au format Bovada/FotMob), sinon None."""
    for mk in m.get("match_odds") or []:
        if mk.get("marketName") != "Full time": continue
        by = {c.get("side") or ("draw" if c.get("name") == "Draw" else None): c.get("cote") for c in mk.get("choices", [])}
        if by.get("home") and by.get("draw") and by.get("away"):
            return by["home"], by["draw"], by["away"]
    return None


def _odd(p):
    return f"{1 / p:.2f}" if p > 0.005 else "–"


def _item(m, mid, first):
    a = m["analyse"]
    v = poisson_view(a["ft_1x2"]["lam_h"], a["ft_1x2"]["lam_a"])
    book = _book_1x2(m)
    if book:
        chips = "".join(f'<span class="eb-chip">{float(c):.2f}</span>' for c in book)
    else:
        chips = "".join(f'<span class="eb-chip" title="cote juste du modèle">{_odd(p)}</span>'
                        for p in (v["ph"], v["pd"], v["pa"]))
    return (f'<button class="eb-item{" on" if first else ""}" data-id="{mid}" onclick="ebPick(\'foot\',\'{mid}\')">'
            f'<div class="eb-item-top"><span>{_e(m.get("league"))}</span><span>{when(m.get("start_ts"))}</span></div>'
            f'<div class="eb-item-t">{_e(m["home"])} – {_e(m["away"])}</div>'
            f'<div class="eb-chips">{chips}</div></button>')


def _kpi(label, p, key, sub=""):
    return (f'<div class="eb-card eb-kpi"><span class="eb-kpi-l">{label}</span>'
            f'<span class="eb-kpi-v" data-k="{key}">{_pct(p)}</span>'
            f'<span class="eb-bar"><i data-kb="{key}" style="--w:{100 * p:.1f}%"></i></span>'
            + (f'<span class="eb-kpi-s">{sub}</span>' if sub else "") + '</div>')


def _detail(m, mid, first):
    a = m["analyse"]
    lh, la = a["ft_1x2"]["lam_h"], a["ft_1x2"]["lam_a"]
    v = poisson_view(lh, la)
    form = m.get("_form") or m.get("pre_match_form") or {}
    fh, fa = form.get("homeTeam") or {}, form.get("awayTeam") or {}
    pos = lambda f: f' · <b>{f["position"]}e</b>' if f.get("position") else ""
    book = _book_1x2(m)
    odds = (lambda i, p: f"cote book {float(book[i]):.2f} · juste {_odd(p)}") if book else (lambda i, p: f"cote juste {_odd(p)}")

    header = (
        '<div class="eb-card">'
        f'<div class="eb-meta">{_e(m.get("league"))} · {when(m.get("start_ts"))}</div>'
        '<div class="eb-vs">'
        f'<div class="eb-team"><span class="eb-team-n">{_e(m["home"])}</span>{_form_html(fh.get("form"))}'
        f'<span class="eb-team-sub">xG attendus : <b>{lh:.2f}</b>{pos(fh)}</span></div>'
        '<span class="eb-vs-x">VS</span>'
        f'<div class="eb-team"><span class="eb-team-n">{_e(m["away"])}</span>{_form_html(fa.get("form"))}'
        f'<span class="eb-team-sub">xG attendus : <b>{la:.2f}</b>{pos(fa)}</span></div>'
        '</div></div>'
    )
    controls = (
        '<div class="eb-card"><div class="eb-ctrl">'
        '<label class="eb-field"><span>Simulations</span><select class="eb-input" data-n>'
        '<option value="1000">1 000</option><option value="10000" selected>10 000</option>'
        '<option value="100000">100 000</option></select></label>'
        f'<button class="eb-btn" onclick="ebFootSim(\'{mid}\',this)">Lancer la simulation</button>'
        '<span class="eb-ctrl-info" data-info>Calcul exact · loi de Poisson</span>'
        '</div></div>'
    )
    k1 = (f'<div class="eb-g3">{_kpi("Victoire " + _e(m["home"]), v["ph"], "ph", odds(0, v["ph"]))}'
          f'{_kpi("Match nul", v["pd"], "pd", odds(1, v["pd"]))}'
          f'{_kpi("Victoire " + _e(m["away"]), v["pa"], "pa", odds(2, v["pa"]))}</div>')
    tiles = "".join(f'<div class="eb-tile"><b>{i}-{j}</b><small>{_pct(p)}</small></div>' for (i, j), p in v["scores"])
    k2 = (f'<div class="eb-g3">{_kpi("Plus de 2,5 buts", v["o25"], "o25", "cote juste " + _odd(v["o25"]))}'
          f'{_kpi("Les deux équipes marquent", v["btts"], "btts", "cote juste " + _odd(v["btts"]))}'
          f'<div class="eb-card eb-kpi"><span class="eb-kpi-l">Scores les plus probables</span>'
          f'<div class="eb-tiles" data-scores>{tiles}</div></div></div>')
    top = max(v["tot"]) or 1
    labels = ["0", "1", "2", "3", "4", "5", "6", "7+"]
    bars = "".join(f'<div class="eb-hb"><em data-t="{i}">{_pct(p)}</em><i data-tb="{i}" style="--h:{100 * p / top:.1f}%"></i></div>'
                   for i, p in enumerate(v["tot"]))
    hist = (f'<div class="eb-card"><h3 class="eb-h">Distribution du total de buts</h3>'
            f'<div class="eb-hist" data-hist>{bars}</div>'
            f'<div class="eb-hx">{"".join(f"<span>{l}</span>" for l in labels)}</div></div>')

    # Mi-temps, buteurs, reperes du championnat
    ht = a.get("ht_1x2") or {}
    ht_rows = "".join(f'<div class="eb-row"><span>{lab}</span><span>{ht.get(k, 0)} %</span></div>'
                      for lab, k in ((f"{_e(m['home'])} mène", "home_pct"), ("Égalité", "draw_pct"),
                                     (f"{_e(m['away'])} mène", "away_pct")))
    ht_card = f'<div class="eb-card"><h3 class="eb-h">À la mi-temps</h3>{ht_rows}</div>'
    scorers = [(p["player"], p["pct"], m["home"]) for p in a.get("top_buteurs_home") or []] + \
              [(p["player"], p["pct"], m["away"]) for p in a.get("top_buteurs_away") or []]
    passers = [(p["player"], p["pct"], m["home"]) for p in a.get("top_passeurs_home") or []] + \
              [(p["player"], p["pct"], m["away"]) for p in a.get("top_passeurs_away") or []]
    def plist(rows):
        return "".join(f'<div class="eb-row"><span>{_e(n)} <span class="eb-dim">· {_e(t)}</span></span><span>{pc} %</span></div>'
                       for n, pc, t in sorted(rows, key=lambda r: -r[1])[:5]) or '<div class="eb-row"><span class="eb-dim">Pas assez de données</span><span></span></div>'
    perf_card = (f'<div class="eb-card"><h3 class="eb-h">Buteurs probables</h3>{plist(scorers)}'
                 + (f'<h3 class="eb-h eb-h-mt">Passeurs probables</h3>{plist(passers)}' if passers else "")
                 + '</div>')
    ls = a.get("league_stats") or {}
    lg_rows = "".join(f'<div class="eb-row"><span>{lab}</span><span>{ls[k]} %</span></div>'
                      for lab, k in (("Plus de 2,5 buts", "ft_over_25"), ("Plus de 1,5 but", "ft_over_15"),
                                     ("Les deux marquent", "btts"), ("But en 1re mi-temps", "ht_over_05")) if ls.get(k) is not None)
    lg_card = (f'<div class="eb-card"><h3 class="eb-h">Repères {_e(a.get("league_name") or m.get("league"))}</h3>'
               f'{lg_rows or "<div class=eb-row><span class=eb-dim>Indisponible</span><span></span></div>"}</div>')
    extra = f'<div class="eb-g3">{ht_card}{perf_card}{lg_card}</div>'

    vb = a.get("value_bets") or []
    value = ""
    if vb:
        rows = "".join(
            f'<tr><td>{_e(b.get("marche"))}</td><td class="n">{b.get("cote")}</td><td class="n">{b.get("p_modele")} %</td>'
            f'<td class="n">{b.get("p_avg")} %</td><td class="n {"eb-acc" if (b.get("value") or 0) > 100 else "eb-dim"}">{b.get("value")}</td>'
            f'<td class="eb-dim">{_e(b.get("book"))}</td></tr>' for b in vb)
        value = (f'<div class="eb-card"><h3 class="eb-h">Value bets <small>value = proba moyenne × cote (&gt; 100 = valeur)</small></h3>'
                 '<div class="eb-scroll"><table class="eb-tbl"><tr><th>Marché</th><th>Cote</th><th>Modèle</th><th>Moyenne</th>'
                 f'<th>Value</th><th>Book</th></tr>{rows}</table></div></div>')
    return (f'<section class="eb-detail{" on" if first else ""}" data-id="{mid}" data-lh="{lh}" data-la="{la}">'
            f'{header}{controls}{k1}{k2}{hist}{extra}{value}'
            '<p class="eb-note">Modèle : buts de chaque équipe tirés selon une loi de Poisson (forme des 5 derniers matchs). '
            'Hypothèse d\'indépendance entre les deux équipes : les nuls et les petits scores sont légèrement sous-estimés.</p>'
            '</section>')


def build_foot_analyse_section(matches):
    analysed = sorted([m for m in matches or [] if (m.get("analyse") or {}).get("ft_1x2")],
                      key=lambda m: m.get("start_ts") or 0)
    if not analysed:
        return '<div class="eb"><div class="eb-card eb-empty">Aucune analyse foot pour le moment.</div></div>'
    ids = [_e(m.get("match_id") or m.get("id") or i) for i, m in enumerate(analysed)]
    items = "".join(_item(m, mid, i == 0) for i, (m, mid) in enumerate(zip(analysed, ids)))
    details = "".join(_detail(m, mid, i == 0) for i, (m, mid) in enumerate(zip(analysed, ids)))
    return ('<!--eb:start--><div class="eb" data-eb="foot"><div class="eb-layout">'
            f'<aside class="eb-side"><div class="eb-label">Matchs à venir · {len(analysed)}</div>'
            f'<div class="eb-list">{items}</div></aside>'
            f'<div class="eb-main">{details}</div></div></div>' + SCRIPT + '<!--eb:end-->')


# JS partage : selection d'un match (aussi utilise par nba_sim_site) + simulation foot.
SCRIPT = """<script>
(function(){
  if(window.ebPick)return;
  window.ebPick=function(scope,id){
    var root=document.querySelector('[data-eb="'+scope+'"]');if(!root)return;
    root.querySelectorAll('.eb-item').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-id')===id);});
    root.querySelectorAll('.eb-detail').forEach(function(d){d.classList.toggle('on',d.getAttribute('data-id')===id);});
    var d=root.querySelector('.eb-detail.on');
    if(d&&window.innerWidth<=1000)d.scrollIntoView({behavior:'smooth',block:'start'});
  };
  window.ebSeg=function(btn,pane){
    var card=btn.closest('.eb-card');
    card.querySelectorAll('.eb-seg button').forEach(function(b){b.classList.toggle('on',b===btn);});
    card.querySelectorAll('.eb-pane').forEach(function(p){p.classList.toggle('on',p.getAttribute('data-pane')===pane);});
  };
  function pois(l){var L=Math.exp(-l),k=0,p=1;do{k++;p*=Math.random();}while(p>L);return k-1;}
  function pct(x){return (100*x).toFixed(1).replace('.',',')+'\\u00a0%';}
  window.ebFootSim=function(id,btn){
    var d=document.querySelector('[data-eb="foot"] .eb-detail[data-id="'+id+'"]');if(!d)return;
    var n=+d.querySelector('[data-n]').value,lh=+d.getAttribute('data-lh'),la=+d.getAttribute('data-la');
    btn.disabled=true;btn.textContent='Simulation…';
    setTimeout(function(){
      var t0=performance.now(),w=0,dr=0,o25=0,bt=0,tot=[0,0,0,0,0,0,0,0],sc={};
      for(var s=0;s<n;s++){
        var h=pois(lh),a=pois(la);
        if(h>a)w++;else if(h===a)dr++;
        if(h+a>2)o25++;if(h>0&&a>0)bt++;
        tot[Math.min(h+a,7)]++;var key=h+'-'+a;sc[key]=(sc[key]||0)+1;
      }
      var ms=Math.round(performance.now()-t0),v={ph:w/n,pd:dr/n,pa:(n-w-dr)/n,o25:o25/n,btts:bt/n};
      Object.keys(v).forEach(function(k){
        var e=d.querySelector('[data-k="'+k+'"]');if(e)e.textContent=pct(v[k]);
        var b=d.querySelector('[data-kb="'+k+'"]');if(b)b.style.setProperty('--w',(100*v[k]).toFixed(1)+'%');
      });
      var top=Object.keys(sc).sort(function(x,y){return sc[y]-sc[x];}).slice(0,6);
      d.querySelector('[data-scores]').innerHTML=top.map(function(k){return '<div class="eb-tile"><b>'+k+'</b><small>'+pct(sc[k]/n)+'</small></div>';}).join('');
      var mx=Math.max.apply(null,tot)||1;
      tot.forEach(function(c,i){
        var e=d.querySelector('[data-t="'+i+'"]');if(e)e.textContent=pct(c/n);
        var b=d.querySelector('[data-tb="'+i+'"]');if(b)b.style.setProperty('--h',(100*c/mx).toFixed(1)+'%');
      });
      d.querySelector('[data-info]').textContent=n.toLocaleString('fr-FR').replace(/ /g,' ')+' simulations · '+ms+' ms';
      btn.disabled=false;btn.textContent='Relancer la simulation';
    },30);
  };
})();
</script>"""
