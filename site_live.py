"""
site_live.py — Scores et stats en direct dans le navigateur (API ESPN, CORS ouvert), foot et NBA.

Le site est statique (GitHub Pages) : c'est la page qui interroge ESPN toutes les 30 s, seulement si
l'onglet est visible et qu'un match suivi est affiche. Marquage :
  - badge(kind, slug, eid)       : pastille de la liste des matchs (🔴 62' 1-0 / Q3 4:12 88-91 / Final)
  - card(kind, slug, eid, picks) : carte « En direct » de la fiche (masquee avant le coup d'envoi)
kind : "soccer" (slug eng.1, ita.1...) ou "nba" (slug "nba"). picks (NBA) : [[id, nom, stat, sens, ligne]]
pour suivre l'avancement de chaque selection.
"""
import html
import json


def badge(kind, slug, eid):
    if not eid: return ""
    return f'<span class="eb-live" data-live="{kind}|{slug}|{html.escape(str(eid))}"></span>'


def card(kind, slug, eid, picks=None):
    if not eid: return ""
    pk = html.escape(json.dumps(picks or [], ensure_ascii=False), quote=True)
    return f'<div class="eb-card eb-livecard eb-off" data-livecard="{kind}|{slug}|{html.escape(str(eid))}" data-picks="{pk}"></div>'


LIVE_SCRIPT = r"""<script>
(function(){
  if(window.spLive)return;
  var API='https://site.api.espn.com/apis/site/v2/sports/';
  function path(k){return k[0]==='nba'?'basketball/nba':'soccer/'+k[1];}
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function J(u){return fetch(u,{cache:'no-store'}).then(function(r){if(!r.ok)throw 0;return r.json();});}
  function visible(el){return el.offsetParent!==null;}
  function clock(k,st){
    var t=st.type||{};
    if(t.state==='post')return 'Final';
    if(t.state!=='in')return '';
    if(k[0]==='nba')return (t.shortDetail||('Q'+st.period+' '+st.displayClock));
    return st.displayClock||t.shortDetail||'';
  }
  function badgeTxt(k,e){
    var c=e.competitions[0],st=c.status||e.status,s=(st.type||{}).state;
    if(s==='pre')return null;
    var h=c.competitors.filter(function(x){return x.homeAway==='home';})[0],a=c.competitors.filter(function(x){return x.homeAway==='away';})[0];
    var sc=k[0]==='nba'?(a.score+'-'+h.score):(h.score+'-'+a.score);
    return {t:(s==='in'?'🔴 ':'')+clock(k,st)+' · '+sc,s:s};
  }
  function footCard(s){
    var h=s.header.competitions[0],cs=h.competitors,st=h.status,state=(st.type||{}).state;
    var home=cs.filter(function(x){return x.homeAway==='home';})[0],away=cs.filter(function(x){return x.homeAway==='away';})[0];
    var tm={};((s.boxscore||{}).teams||[]).forEach(function(t){var m={};(t.statistics||[]).forEach(function(x){m[x.name]=x.displayValue;});tm[t.team.displayName]=m;});
    var H=tm[home.team.displayName]||{},A=tm[away.team.displayName]||{};
    var rows=[['Possession','possessionPct','%'],['Tirs','totalShots',''],['Tirs cadrés','shotsOnTarget',''],['Corners','wonCorners',''],
              ['Arrêts','saves',''],['Fautes','foulsCommitted',''],['Cartons jaunes','yellowCards',''],['Cartons rouges','redCards','']];
    var stat=rows.map(function(r){return '<div class="eb-row"><span>'+esc(H[r[1]]||'–')+r[2]+'</span><span class="eb-lv-m">'+r[0]+'</span><span>'+esc(A[r[1]]||'–')+r[2]+'</span></div>';}).join('');
    var ev=(s.keyEvents||[]).filter(function(e){var t=(e.type||{}).type||'';return /goal|card|penalty|own/i.test(t)||e.scoringPlay;}).map(function(e){
      var who=(e.participants||[]).map(function(p){return p.athlete&&p.athlete.displayName;}).filter(Boolean)[0]||'';
      var t=(e.type||{}).text||'',ic=e.scoringPlay?'⚽':(/red/i.test(t)?'🟥':(/yellow/i.test(t)?'🟨':'•'));
      return '<li><span class="eb-dim">'+esc((e.clock||{}).displayValue||'')+'</span> '+ic+' '+esc(who)+' <span class="eb-dim">'+esc((e.team||{}).displayName||'')+'</span></li>';}).join('');
    return '<div class="eb-ctrl"><h3 class="eb-h eb-h-nomb">'+(state==='in'?'🔴 En direct':'Terminé')+' <small>'+esc(clock(['soccer'],st))+'</small></h3>'+
      '<span class="eb-lv-score">'+esc(home.team.displayName)+' <b>'+esc(home.score)+' - '+esc(away.score)+'</b> '+esc(away.team.displayName)+'</span></div>'+
      '<div class="eb-list2 eb-lv-stats">'+stat+'</div>'+(ev?'<ul class="eb-ev">'+ev+'</ul>':'')+
      '<p class="eb-note">Données ESPN, rafraîchies toutes les 30 s quand cette page est ouverte.</p>';
  }
  var KEY={AST:'assists',STL:'steals',BLK:'blocks',REB:'rebounds',PTS:'points','3PM':'threePointFieldGoalsMade-threePointFieldGoalsAttempted'};
  function nbaCard(s,picks){
    var h=s.header.competitions[0],cs=h.competitors,st=h.status,state=(st.type||{}).state;
    var home=cs.filter(function(x){return x.homeAway==='home';})[0],away=cs.filter(function(x){return x.homeAway==='away';})[0];
    var val={},mins={};
    ((s.boxscore||{}).players||[]).forEach(function(t){(t.statistics||[]).forEach(function(g){var keys=g.keys||[];
      (g.athletes||[]).forEach(function(a){var id=a.athlete.id,m={};keys.forEach(function(k,i){m[k]=a.stats[i];});val[id]=m;});});});
    var rows=(picks||[]).map(function(p){
      var m=val[p[0]],raw=m?m[KEY[p[2]]]:null,v=raw==null?null:parseFloat(String(raw).split('-')[0]);
      var over=p[3]==='o',line=p[4],ok=v==null?null:(over?v>line:v<line),done=state==='post';
      var tag=v==null?'<span class="eb-tag">pas entré</span>':(done?(ok?'<span class="eb-tag acc">gagné</span>':'<span class="eb-tag neg">perdu</span>'):
        (over?(v>line?'<span class="eb-tag acc">validé</span>':'<span class="eb-tag">en cours</span>'):(v>line?'<span class="eb-tag neg">perdu</span>':'<span class="eb-tag">en cours</span>')));
      return '<tr><td>'+esc(p[1])+'</td><td>'+(over?'Over ':'Under ')+line+' '+esc(p[2])+'</td><td class="n"><b>'+(v==null?'–':v)+'</b></td><td class="n">'+esc(m?m.minutes:'–')+'</td><td>'+tag+'</td></tr>';}).join('');
    var tm={};((s.boxscore||{}).teams||[]).forEach(function(t){var m={};(t.statistics||[]).forEach(function(x){m[x.name]=x.displayValue;});tm[t.team.abbreviation]=m;});
    var A=tm[away.team.abbreviation]||{},H=tm[home.team.abbreviation]||{};
    var rs=[['Tirs','fieldGoalsMade-fieldGoalsAttempted'],['3 pts','threePointFieldGoalsMade-threePointFieldGoalsAttempted'],['Rebonds','totalRebounds'],['Passes','assists'],['Pertes','turnovers']];
    var stat=rs.map(function(r){return '<div class="eb-row"><span>'+esc(A[r[1]]||'–')+'</span><span class="eb-lv-m">'+r[0]+'</span><span>'+esc(H[r[1]]||'–')+'</span></div>';}).join('');
    return '<div class="eb-ctrl"><h3 class="eb-h eb-h-nomb">'+(state==='in'?'🔴 En direct':'Terminé')+' <small>'+esc(clock(['nba'],st))+'</small></h3>'+
      '<span class="eb-lv-score">'+esc(away.team.abbreviation)+' <b>'+esc(away.score)+' - '+esc(home.score)+'</b> '+esc(home.team.abbreviation)+'</span></div>'+
      '<div class="eb-list2 eb-lv-stats">'+stat+'</div>'+
      (rows?'<h3 class="eb-h eb-ev-t">Sélections du simulateur</h3><div class="eb-scroll"><table class="eb-tbl"><tr><th>Joueur</th><th>Pari</th><th>Actuel</th><th>Min</th><th></th></tr>'+rows+'</table></div>':'')+
      '<p class="eb-note">Données ESPN, rafraîchies toutes les 30 s quand cette page est ouverte.</p>';
  }
  function tick(){
    if(document.visibilityState!=='visible')return;
    var els=[].slice.call(document.querySelectorAll('[data-live]')).filter(function(el){return el.closest('[id^=sport-]')&&visible(el.closest('[id^=sport-]'));});
    if(!els.length)return;
    var groups={};els.forEach(function(el){var k=el.getAttribute('data-live').split('|');groups[k[0]+'|'+k[1]]=k;});
    Object.keys(groups).forEach(function(g){var k=groups[g];
      J(API+path(k)+'/scoreboard').then(function(d){(d.events||[]).forEach(function(e){
        var b=badgeTxt(k,e),sel='[data-live="'+g+'|'+e.id+'"]';
        document.querySelectorAll(sel).forEach(function(el){el.textContent=b?b.t:'';el.className='eb-live'+(b?' '+b.s:'');});
        if(!b)return;
        document.querySelectorAll('[data-livecard="'+g+'|'+e.id+'"]').forEach(function(c){
          c.classList.remove('eb-off');if(!visible(c))return;
          J(API+path(k)+'/summary?event='+e.id).then(function(s){
            c.innerHTML=k[0]==='nba'?nbaCard(s,JSON.parse(c.getAttribute('data-picks')||'[]')):footCard(s);}).catch(function(){});});
      });}).catch(function(){});});
  }
  window.spLive=tick;
  setInterval(tick,30000);
  document.addEventListener('visibilitychange',tick);
  document.addEventListener('click',function(e){if(e.target.closest&&e.target.closest('.eb-item,.sport-btn,.sub-tg'))setTimeout(tick,60);});
  setTimeout(tick,800);
})();
</script>"""
