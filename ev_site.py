"""
ev_site.py — Carte « Value bets & cote juste » des vues Picks (foot et NBA), donnees ev_scan.py.

- value bets ouverts (cote francaise au-dessus de la cote juste Pinnacle) et leur suivi (ROI, CLV) ;
- calculateur : le visiteur choisit un match et une issue, tape la cote proposee (boostee ou non) ->
  esperance, cote minimale, mise conseillee (1/4 de Kelly sur 100 EUR). Tout se calcule dans la page.
"""
import html
import json
from datetime import datetime, timedelta, timezone

import ev_scan

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Paris")
except Exception:
    TZ = timezone(timedelta(hours=1))


def _e(s):
    return html.escape(str(s if s is not None else ""))


def _when(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ).strftime("%d/%m %H:%M")
    except Exception:
        return ""


def build_ev_card(sport):
    st = ev_scan.load()
    picks = sorted((p for p in st["picks"].values() if p["sport"] == sport and not p.get("result")), key=lambda p: p["commence"])
    rows = "".join(
        f'<tr><td class="n eb-dim">{_when(p["commence"])}</td><td><b>{_e(p["home"])} - {_e(p["away"])}</b> '
        f'<span class="eb-dim">{_e(p["league"])}</span></td><td><b>{_e(p["label"])}</b></td><td>{_e(p["book_name"])}</td>'
        f'<td class="n"><b>{p["price"]:.2f}</b></td><td class="n">{1 / p["fair"]:.2f}</td>'
        f'<td class="n eb-acc">+{100 * p["ev"]:.1f} %</td></tr>' for p in picks)
    sm = ev_scan.summary(st)
    track = (f'Suivi : {sm["n"]} value bets réglés ({sm["w"]}-{sm["l"]}, {sm["units"]:+.1f} u)'
             + (f', CLV {100 * sm["clv"]:+.1f} %' if sm["clv"] is not None else "") + "." if sm["n"] else
             "Suivi : aucun value bet réglé pour l’instant.").replace(".", ",", 0)
    table = (f'<div class="eb-scroll"><table class="eb-tbl"><tr><th>Coup d’envoi</th><th>Match</th><th>Pari</th><th>Bookmaker</th>'
             f'<th>Cote</th><th>Cote juste</th><th>Espérance</th></tr>{rows}</table></div>' if rows else
             '<div class="eb-empty">Aucune cote française au-dessus de la cote juste en ce moment (cas normal : leur '
             'marge est de 5 à 14 %). Vérifie plutôt les cotes boostées avec le calculateur.</div>')
    fair = sorted((v for v in (st.get("fair") or {}).values() if v["sport"] == sport), key=lambda v: v["commence"])
    data = [{"m": f'{_when(v["commence"])} · {v["home"]} - {v["away"]} ({v["league"]})', "l": v["lines"]} for v in fair]
    js_data = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    calc = ('<div class="eb-evc">'
            '<select class="eb-evm" onchange="ebEvFill(this)"><option value="">Choisis un match…</option>'
            + "".join(f'<option value="{i}">{_e(d["m"])}</option>' for i, d in enumerate(data)) + '</select>'
            '<select class="eb-evo" onchange="ebEvCalc(this)"></select>'
            '<input class="eb-evi" type="number" step="0.01" min="1.01" placeholder="Cote proposée" oninput="ebEvCalc(this)">'
            '<div class="eb-evr eb-meta"></div></div>'
            f'<script type="application/json" class="eb-evd">{js_data}</script>') if data else \
        '<div class="eb-empty">Cotes justes indisponibles (prochain relevé dans quelques heures).</div>'
    scanned = st.get("scanned_at")
    return ('<div class="eb-card"><h3 class="eb-h">Value bets <small>cote française au-dessus de la cote juste Pinnacle</small></h3>'
            f'{table}<p class="eb-note">{track} Probabilité « juste » = cote Pinnacle (le bookmaker le plus précis) sans '
            'sa marge. Validé sur l’historique : parier quand un bookmaker paie plus que cette probabilité bat la clôture '
            '(CLV +6 à +9 % sur les plus/moins 2,5 buts, deux saisons). '
            + (f'Dernier relevé : {_when(scanned)}.' if scanned else "") + '</p></div>'
            '<div class="eb-card"><h3 class="eb-h">Calculateur de cote <small>ta cote (boostée ?) vaut-elle le coup ?</small></h3>'
            f'{calc}<p class="eb-note">Espérance = probabilité juste × cote − 1. Positive : le pari est rentable à long '
            'terme (une cote boostée l’est souvent). Mise conseillée : 1/4 de Kelly sur une bankroll de 100 €, plafonnée à 5 €.</p></div>'
            + EV_SCRIPT)


EV_SCRIPT = """<script>
(function(){
  if(window.ebEvFill)return;
  function data(el){return JSON.parse(el.closest('.eb-card').querySelector('.eb-evd').textContent);}
  window.ebEvFill=function(sel){
    var card=sel.closest('.eb-card'),o=card.querySelector('.eb-evo'),d=data(sel)[sel.value];
    o.innerHTML=d?d.l.map(function(x,i){return '<option value="'+i+'">'+x[0]+' (juste '+x[1].toFixed(2)+')</option>';}).join(''):'';
    ebEvCalc(sel);
  };
  window.ebEvCalc=function(el){
    var card=el.closest('.eb-card'),m=card.querySelector('.eb-evm').value,o=card.querySelector('.eb-evo').value;
    var odd=parseFloat(card.querySelector('.eb-evi').value),r=card.querySelector('.eb-evr');
    var d=data(el)[m];if(!d||o===''){r.innerHTML='';return;}
    var fair=d.l[+o][1],p=1/fair;
    if(!(odd>1)){r.innerHTML='Cote juste : <b>'+fair.toFixed(2)+'</b> — tape la cote proposée.';return;}
    var ev=p*odd-1,k=(p*odd-1)/(odd-1),stake=Math.min(Math.max(0,0.25*k*100),5);
    r.innerHTML='Cote juste <b>'+fair.toFixed(2)+'</b> · espérance <b class="'+(ev>=0?'eb-acc':'eb-neg')+'">'+(ev>=0?'+':'')+(100*ev).toFixed(1).replace('.',',')+' %</b>'+
      (ev>0?' · mise conseillée <b>'+stake.toFixed(2).replace('.',',')+' €</b> pour 100 €':' · à éviter (en dessous de la cote juste)');
  };
})();
</script>"""
