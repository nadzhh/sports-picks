"""
nba_chart.py — Graphique joueur interactif des fiches props (Wilson, 17/11/2026 : « j'aime beaucoup leur affichage »,
capture StatsHub : barres par match vertes / rouges selon la ligne, minutes, onglets de stats, L5 / L10 / L15 / L20,
ligne et nombre de matchs reglables).

Rendu cote navigateur (un seul moteur) a partir d'un JSON compact pose sur l'element : html(...) renvoie le bloc,
le script (pose une fois, garde par window.spPC) dessine tous les .pc-chart presents ou ajoutes ensuite
(MutationObserver : fragments recharges par la veille). Minutes dans une bande a part sous les barres (pas de
second axe vertical superpose).
"""
import html as H
import json

COLS = ("PTS", "REB", "AST", "3PM", "3PA", "FGA", "STL", "BLK", "TO")
_CACHE = {}


def logs(years):
    """{athlete_id: [[date, adv, dom(1/0), resultat, minutes, PTS, REB, AST, 3PM, 3PA, FGA, STL, BLK, TO, saison]]}
    du plus recent au plus ancien (cache par saisons)."""
    key = tuple(years)
    if key in _CACHE: return _CACHE[key]
    import nba_boxdb as B
    out = {}
    for y in years:
        for g in B.load_season(y).values():
            if g.get("type") not in (None, "regular", "postseason", "playoffs"): continue
            for side, opp in (("home", "away"), ("away", "home")):
                T, O = g.get(side) or {}, g.get(opp) or {}
                res = ""
                if T.get("pts") is not None and O.get("pts") is not None:
                    res = f'{"V" if T["pts"] > O["pts"] else "D"} {T["pts"]}-{O["pts"]}'
                for r in T.get("players") or []:
                    if r.get("dnp") or not r.get("MIN"): continue
                    out.setdefault(str(r["id"]), []).append(
                        [g["date"], O.get("abbr", "?"), 1 if side == "home" else 0, res, round(float(r["MIN"]))]
                        + [r.get(k, 0) or 0 for k in COLS] + [y])
    for v in out.values(): v.sort(key=lambda x: x[0], reverse=True)
    _CACHE.clear(); _CACHE[key] = out
    return out


def html(pid, name, stat, line, over, opp, season, lines=None, price=None, n=30):
    """Bloc du graphique (vide si pas d'historique). lines : {stat: ligne du soir} pour les autres onglets ;
    price : texte de la cote (ex. « DK -112 · ≈ 1,52 en France »)."""
    L = logs((season - 1, season)).get(str(pid)) or []
    if not L: return ""
    j = {"name": name, "stat": stat, "line": float(line), "over": bool(over), "opp": opp, "season": season,
         "lines": {k: float(v) for k, v in (lines or {}).items()}, "price": price or "",
         # les n derniers matchs + les face-a-face plus anciens (tuile H2H)
         "g": L[:n] + [r for r in L[n:] if r[1] == opp]}
    return f'<div class="pc-chart" data-j="{H.escape(json.dumps(j, ensure_ascii=False, separators=(",", ":")))}"></div>'


def assets():
    """Style + script, a poser une fois par page ou fragment (le script ne s'installe qu'une fois)."""
    return CSS + JS


def dedupe(page):
    """Garde le premier bloc style + script d'une page assemblee a partir de plusieurs fiches."""
    a = CSS + JS
    i = page.find(a)
    return page if i < 0 else page[:i + len(a)] + page[i + len(a):].replace(a, "")


CSS = """<style>
.pc-chart{margin:8px 0 6px;font-size:12px;color:var(--tx,#e2e8f0)}
.pc-tabs{display:flex;gap:2px;overflow-x:auto;border-bottom:1px solid var(--line,#1e293b);margin-bottom:8px;scrollbar-width:none}
.pc-tabs button{flex:0 0 auto;background:none;border:0;color:var(--mu,#94a3b8);padding:6px 9px;border-radius:7px 7px 0 0;cursor:pointer;font:inherit}
.pc-tabs button.on{background:rgba(149,224,74,.12);color:var(--tx,#e2e8f0);font-weight:700}
.pc-hd{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-bottom:8px}
.pc-hd div{background:rgba(148,163,184,.07);border-radius:8px;padding:5px 7px}
.pc-hd small{display:block;color:var(--mu,#94a3b8);font-size:10.5px}.pc-hd b{font-size:15px}
.pc-ok{color:#4ade80}.pc-ko{color:#f87171}
.pc-ctl{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.pc-seg{display:inline-flex;background:rgba(148,163,184,.08);border-radius:8px;padding:2px}
.pc-seg button{background:none;border:0;color:var(--mu,#94a3b8);padding:4px 9px;border-radius:6px;cursor:pointer;font:inherit}
.pc-seg button.on{background:var(--panel2,#1e293b);color:var(--tx,#e2e8f0);font-weight:700}
.pc-seg span{padding:4px 6px;min-width:34px;text-align:center;font-weight:700}
.pc-plot{position:relative;display:flex;align-items:flex-end;gap:3px;height:150px;padding:16px 34px 0 0;border-bottom:1px solid var(--line,#1e293b)}
.pc-col{flex:1 1 0;min-width:0;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;cursor:pointer;position:relative}
.pc-col i{display:block;width:100%;max-width:30px;border-radius:4px 4px 0 0;min-height:3px}
.pc-col.ok i{background:rgba(74,222,128,.55)}.pc-col.ko i{background:rgba(248,113,113,.55)}
.pc-col.sel i,.pc-col:hover i{filter:brightness(1.35)}
.pc-col em{font-style:normal;font-size:10.5px;color:var(--tx,#e2e8f0);margin-bottom:2px}
.pc-ln{position:absolute;left:0;right:34px;border-top:1.5px dashed #eab308;pointer-events:none}
.pc-ln span{position:absolute;right:-34px;top:-9px;background:rgba(234,179,8,.15);color:#facc15;font-size:10.5px;padding:1px 4px;border-radius:4px}
.pc-min{position:relative;height:26px;margin-right:34px}
.pc-min svg{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
.pc-min .pc-mlab{position:absolute;right:-34px;top:6px;color:#60a5fa;font-size:10px}
.pc-x{display:flex;gap:3px;margin-right:34px}
.pc-x div{flex:1 1 0;min-width:0;text-align:center;color:var(--mu,#94a3b8);font-size:9.5px;line-height:1.25;overflow:hidden;white-space:nowrap}
.pc-x b{display:block;color:var(--tx,#e2e8f0);font-size:10px;font-weight:600}
.pc-tip{min-height:18px;margin:6px 0 2px;color:var(--mu,#94a3b8);font-size:11.5px}
.pc-tip b{color:var(--tx,#e2e8f0)}
.pc-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(68px,1fr));gap:5px;margin-top:6px}
.pc-tiles div{border:1px solid var(--line,#1e293b);border-radius:8px;text-align:center;padding:4px 2px}
.pc-tiles small{display:block;color:var(--mu,#94a3b8);font-size:10.5px}.pc-tiles b{font-size:13px}.pc-tiles span{display:block;font-size:10.5px}
.pc-leg{display:flex;gap:12px;color:var(--mu,#94a3b8);font-size:10.5px;margin-top:4px}
.pc-leg i{display:inline-block;width:12px;height:0;border-top:2px dashed #eab308;vertical-align:middle;margin-right:4px}
.pc-leg i.m{border-top:2px solid #60a5fa}
@media (max-width:520px){.pc-hd b{font-size:13.5px}.pc-col em{font-size:9.5px}.pc-x div{font-size:8.5px}.pc-x b{font-size:9px}}
</style>"""

JS = r"""<script>
(function(){
if(window.spPC) return;
var ST=[['PTS','Points',['PTS']],['REB','Rebonds',['REB']],['AST','Passes',['AST']],['PRA','PRA',['PTS','REB','AST']],
 ['PR','Pts+Reb',['PTS','REB']],['PA','Pts+Pas',['PTS','AST']],['RA','Reb+Pas',['REB','AST']],['3PM','3 pts',['3PM']],
 ['3PA','Tirs à 3',['3PA']],['FGA','Tirs',['FGA']],['STL','Interc.',['STL']],['BLK','Contres',['BLK']],['TO','Pertes',['TO']]];
var C={PTS:5,REB:6,AST:7,'3PM':8,'3PA':9,FGA:10,STL:11,BLK:12,TO:13};
function def(s){for(var i=0;i<ST.length;i++)if(ST[i][0]===s)return ST[i];return ST[0];}
function val(g,s){return def(s)[2].reduce(function(a,k){return a+(g[C[k]]||0);},0);}
function fr(x,d){return (+x).toFixed(d).replace('.',',');}
function e(t){return String(t).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function med(a){if(!a.length)return 0;var b=a.slice().sort(function(x,y){return x-y;}),m=b.length>>1;return b.length%2?b[m]:(b[m-1]+b[m])/2;}
function render(el){
  var J=el._j||(el._j=JSON.parse(el.getAttribute('data-j')));
  var S=el._s||(el._s={stat:J.stat,line:J.line,n:(el.clientWidth&&el.clientWidth<520)?10:20,loc:'all',sel:-1});
  var over=S.stat===J.stat?J.over:true;
  var all=J.g.filter(function(g){return S.loc==='all'||(S.loc==='h'?g[2]===1:g[2]===0);});
  var G=all.slice(0,S.n);
  var win=function(v){return over?v>S.line:v<S.line;};
  var vals=G.map(function(g){return val(g,S.stat);});
  var hit=vals.filter(win).length, avg=vals.length?vals.reduce(function(a,b){return a+b;},0)/vals.length:0;
  var mins=G.map(function(g){return g[4];}), mavg=mins.length?mins.reduce(function(a,b){return a+b;},0)/mins.length:0;
  var top=Math.max.apply(null,vals.concat([S.line,1]))*1.15;
  var h='<div class="pc-tabs">'+ST.map(function(s){return '<button data-a="st" data-v="'+s[0]+'" class="'+(s[0]===S.stat?'on':'')+'">'+s[1]+'</button>';}).join('')+'</div>';
  var pc=vals.length?Math.round(100*hit/vals.length):0;
  h+='<div class="pc-hd"><div><small>Moyenne</small><b>'+fr(avg,1)+'</b></div>'
    +'<div><small>Réussite '+(over?'plus':'moins')+' de '+fr(S.line,1)+'</small><b class="'+(pc>=50?'pc-ok':'pc-ko')+'">'+pc+' % <span style="font-size:11px">('+hit+'/'+vals.length+')</span></b></div>'
    +'<div><small>Minutes moy.</small><b>'+fr(mavg,1)+'</b></div>'
    +'<div><small>'+(S.stat===J.stat&&J.price?'Cote':'Médiane')+'</small><b style="font-size:12px">'+(S.stat===J.stat&&J.price?e(J.price):fr(med(vals),1))+'</b></div></div>';
  h+='<div class="pc-ctl"><div class="pc-seg"><button data-a="l" data-v="-0.5">−</button><span>Ligne '+fr(S.line,1)+'</span><button data-a="l" data-v="0.5">+</button></div>'
    +'<div class="pc-seg"><button data-a="n" data-v="-5">−</button><span>'+S.n+' matchs</span><button data-a="n" data-v="5">+</button></div>'
    +'<div class="pc-seg">'+[['all','Dom. + ext.'],['h','Domicile'],['a','Extérieur']].map(function(o){return '<button data-a="loc" data-v="'+o[0]+'" class="'+(S.loc===o[0]?'on':'')+'">'+o[1]+'</button>';}).join('')+'</div></div>';
  var cols='',xs='',pts=[];
  G.forEach(function(g,i){var v=vals[i];
    cols+='<div class="pc-col '+(win(v)?'ok':'ko')+(S.sel===i?' sel':'')+'" data-a="tip" data-v="'+i+'"><em>'+v+'</em><i style="height:'+(100*v/top).toFixed(1)+'%"></i></div>';
    xs+='<div><b>'+(g[2]?'':'@')+e(g[1])+'</b>'+g[0].slice(8,10)+'/'+g[0].slice(5,7)+'</div>';
    pts.push(((i+0.5)/G.length*100).toFixed(2)+','+(26-22*g[4]/48).toFixed(1));});
  h+='<div class="pc-plot"><div class="pc-ln" style="bottom:'+(100*S.line/top).toFixed(1)+'%"><span>'+fr(S.line,1)+'</span></div>'+cols+'</div>';
  h+='<div class="pc-min"><svg viewBox="0 0 100 26" preserveAspectRatio="none"><polyline points="'+pts.join(' ')+'" fill="none" stroke="#60a5fa" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg><span class="pc-mlab">min</span></div>';
  h+='<div class="pc-x">'+xs+'</div>';
  var t=S.sel>=0&&G[S.sel]?G[S.sel]:null;
  h+='<div class="pc-tip">'+(t?(t[2]?'vs ':'@ ')+'<b>'+e(t[1])+'</b> · '+t[0].slice(8,10)+'/'+t[0].slice(5,7)+'/'+t[0].slice(0,4)+(t[3]?' · '+e(t[3]):'')+' · <b>'+t[4]+' min</b> · <b>'+val(t,S.stat)+' '+e(def(S.stat)[1].toLowerCase())+'</b>':'Touche une barre pour le détail du match.')+'</div>';
  function tile(lab,L){var n=L.length,k=L.filter(function(g){return win(val(g,S.stat));}).length,p=n?Math.round(100*k/n):0;
    return '<div><small>'+lab+'</small><b class="'+(n&&p>=50?'pc-ok':'pc-ko')+'">'+k+'/'+n+'</b><span class="'+(n&&p>=50?'pc-ok':'pc-ko')+'">'+(n?p+' %':'–')+'</span></div>';}
  var m5=all.slice(0,5).map(function(g){return g[4];}),m20=all.slice(0,20).map(function(g){return g[4];});
  var a5=m5.length?m5.reduce(function(a,b){return a+b;},0)/m5.length:0,a20=m20.length?m20.reduce(function(a,b){return a+b;},0)/m20.length:0;
  h+='<div class="pc-tiles">'+tile('L5',all.slice(0,5))+tile('L10',all.slice(0,10))+tile('L15',all.slice(0,15))+tile('L20',all.slice(0,20))
    +tile('Saison',all.filter(function(g){return g[14]===J.season;}))+tile('H2H '+e(J.opp),J.g.filter(function(g){return g[1]===J.opp;}))
    +'<div><small>Min L5</small><b>'+fr(a5,0)+'</b><span style="color:var(--mu,#94a3b8)">'+(a5-a20>=0?'+':'−')+fr(Math.abs(a5-a20),1)+' vs L20</span></div></div>';
  h+='<div class="pc-leg"><span><i></i>ligne</span><span><i class="m"></i>minutes jouées</span><span>barre verte = pari gagnant</span></div>';
  el.innerHTML=h; el.setAttribute('data-r','1');
}
function act(ev){var b=ev.target.closest('[data-a]'),el=ev.currentTarget;if(!b)return;ev.stopPropagation();var S=el._s,J=el._j,v=b.getAttribute('data-v'),a=b.getAttribute('data-a');
  if(a==='st'){S.stat=v;S.sel=-1;var L=v===J.stat?J.line:(J.lines[v]!=null?J.lines[v]:null);
    if(L==null){var vv=J.g.slice(0,10).map(function(g){return val(g,v);});L=Math.floor(med(vv))+0.5;}S.line=L;}
  else if(a==='l'){S.line=Math.max(0.5,S.line+parseFloat(v));}
  else if(a==='n'){S.n=Math.max(5,Math.min(J.g.length,S.n+parseInt(v,10)));S.sel=-1;}
  else if(a==='loc'){S.loc=v;S.sel=-1;}
  else if(a==='tip'){S.sel=parseInt(v,10);}
  render(el);}
function scan(root){(root||document).querySelectorAll('.pc-chart:not([data-r])').forEach(function(el){try{render(el);el.addEventListener('click',act);}catch(x){}});}
window.spPC={render:render,scan:scan};
scan();
var pend=false;new MutationObserver(function(){if(pend)return;pend=true;requestAnimationFrame(function(){pend=false;scan();});})
  .observe(document.documentElement,{childList:true,subtree:true});
})();
</script>"""
