"""
site_search.py — Recherche globale du site (idee validee par Wilson, 16/11/2026 : « une barre de recherche globale
(equipe, joueur) qui filtre/affiche directement les matchs, props et picks lies, au lieu de scroller »).

Fonctionnement :
  * un bouton 🔍 « Rechercher » dans le menu principal (barre du bas sur telephone, 5e onglet ; en haut sur
    ordinateur, raccourci « / » ou Ctrl+K) ouvre un panneau avec le champ de recherche ;
  * la recherche se fait en JS sur le DOM deja present (cartes, lignes de tableaux) des onglets que le menu affiche :
    Aujourd'hui, Foot › Simulation/Picks/Historique/Analyse, Basket › Simulation/Picks/Historique, Bankroll IA.
    Pas d'index a maintenir : les sections rechargees en direct par la veille (today.html, nba_sim.html,
    foot_sim.html, nba_bankroll.html) sont relues automatiquement (index marque « perime » a chaque changement du DOM
    et reconstruit a la recherche suivante) ;
  * seule exception : Basket › Analyse (fiches joueurs, 16 Mo charges a la demande par site_lazy) n'est pas dans la
    page -> petit index JSON (noms des joueurs + match) embarque par site_lazy.split via analyse_script() ;
  * insensible aux accents et aux majuscules, a partir de 2 caracteres, tous les mots tapes doivent apparaitre ;
  * resultats regroupes (Matchs NBA, Props et joueurs NBA, Picks…) ; un clic ouvre le bon onglet (spNav), selectionne
    le match, deplie ce qu'il faut (details, onglet Joueurs/Props, « voir plus »), fait defiler et surligne l'element.

Aucune dependance externe. inject() est appele par generate_site.main() avant l'allegement (optimize_html).
"""
import html as _html
import json
import re

MARK = "<!--sp-search-->"

NAV_BTN = ('<button class="sport-btn sps-nav" type="button" id="nav-search" onclick="spSearch.open()" '
           'aria-label="Rechercher une équipe, un joueur, un match (raccourci /)" aria-haspopup="dialog">'
           '<span class="sp-ic">🔍</span> <span class="sp-lb">Rechercher</span><kbd class="sps-kbd">/</kbd></button>')

CSS = """<style id="sp-search-css">
.sps-nav{margin-left:auto}
.sps-kbd{font:500 11px var(--mono,monospace);color:var(--di,#6b7a70);border:1px solid var(--line2,#2a352c);border-radius:5px;padding:0 5px;margin-left:8px}
#sps[hidden]{display:none!important}
#sps{position:fixed;inset:0;z-index:10010;display:flex;justify-content:center;align-items:flex-start;padding:min(10vh,72px) 12px 12px;box-sizing:border-box}
.sps-back{position:absolute;inset:0;background:rgba(5,8,6,.72);-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)}
.sps-box{position:relative;width:min(680px,100%);max-height:100%;display:flex;flex-direction:column;background:var(--panel,#0f1411);
  border:1px solid var(--line2,#2a352c);border-radius:14px;box-shadow:0 18px 60px rgba(0,0,0,.55);overflow:hidden;color:var(--tx,#eef3ef)}
.sps-bar{display:flex;align-items:center;gap:8px;padding:10px 10px 10px 14px;border-bottom:1px solid var(--line,#1f2a22)}
.sps-bar input{flex:1;min-width:0;background:transparent;border:0;outline:0;color:var(--tx,#eef3ef);font:500 16px var(--sans,sans-serif);padding:6px 0}
.sps-bar input::-webkit-search-cancel-button{display:none}
.sps-x{flex:none;background:var(--chip,#1a221c);color:var(--mu,#9aa89e);border:1px solid var(--line,#1f2a22);border-radius:8px;
  min-width:36px;height:36px;font-size:15px;cursor:pointer}
.sps-x:hover{color:var(--tx,#eef3ef);border-color:var(--line2,#2a352c)}
.sps-res{overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;padding:6px 0 10px}
.sps-hint{padding:18px 16px;color:var(--mu,#9aa89e);font-size:13.5px;line-height:1.5}
.sps-cap{padding:4px 16px}
.sps-hint b{color:var(--tx,#eef3ef);font-weight:600}
.sps-gh{display:flex;justify-content:space-between;gap:8px;padding:12px 16px 4px;font:500 11.5px var(--mono,monospace);letter-spacing:.8px;
  text-transform:uppercase;color:var(--mu,#9aa89e)}
.sps-r{display:block;width:100%;box-sizing:border-box;text-align:left;background:transparent;border:0;border-left:3px solid transparent;
  color:var(--tx,#eef3ef);padding:8px 16px 8px 13px;cursor:pointer;font:inherit;line-height:1.35}
.sps-r:hover,.sps-r:focus{background:var(--chip,#1a221c);border-left-color:var(--acc,#95e04a);outline:0}
.sps-t{display:block;font-size:14px;font-weight:600;overflow-wrap:anywhere}
.sps-s{display:block;font:12px var(--mono,monospace);color:var(--mu,#9aa89e);margin-top:2px;overflow-wrap:anywhere}
.sps-m{display:block;font-size:12.5px;color:var(--mu,#9aa89e);margin-top:2px;overflow-wrap:anywhere}
.sps-r mark{background:var(--acc-soft,rgba(149,224,74,.18));color:var(--acc,#95e04a);border-radius:3px;padding:0 1px}
.sps-more{display:block;background:transparent;border:0;color:var(--acc,#95e04a);font:500 12.5px var(--sans,sans-serif);padding:4px 16px 6px;cursor:pointer}
.sps-more:hover,.sps-more:focus{text-decoration:underline;outline:0}
.sps-flash{outline:2px solid var(--acc,#95e04a)!important;outline-offset:3px;border-radius:8px;animation:sps-fl 2.4s ease-out}
.sps-flash>td{animation:sps-fl 2.4s ease-out}
@keyframes sps-fl{0%,35%{background-color:rgba(149,224,74,.22)}100%{background-color:transparent}}
.sps-tgt{scroll-margin-top:76px;scroll-margin-bottom:100px}
#sps-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:10011;background:var(--chip,#1a221c);color:var(--tx,#eef3ef);
  border:1px solid var(--line2,#2a352c);border-radius:10px;padding:9px 14px;font-size:13px;box-shadow:0 6px 24px rgba(0,0,0,.45)}
@media (max-width:720px){
  .sp-nav.sps-on{grid-template-columns:repeat(5,1fr)!important}
  .sps-nav{margin-left:0}
  .sps-kbd{display:none}
  #sps{padding:8px 8px calc(8px + env(safe-area-inset-bottom,0px))}
  .sps-box{max-height:calc(100dvh - 16px)}
  #sps-toast{bottom:calc(96px + env(safe-area-inset-bottom,0px))}
}
@media (hover:none){.sps-kbd{display:none}}
</style>"""

OVERLAY = """<div id="sps" hidden role="dialog" aria-modal="true" aria-label="Recherche">
  <div class="sps-back" onclick="spSearch.close()"></div>
  <div class="sps-box">
    <div class="sps-bar"><span aria-hidden="true">🔍</span>
      <input id="sps-q" type="search" placeholder="Équipe, joueur, match…" autocomplete="off" autocapitalize="off" autocorrect="off"
        spellcheck="false" enterkeyhint="search" aria-label="Rechercher une équipe, un joueur ou un match" aria-controls="sps-res">
      <button class="sps-x" type="button" onclick="spSearch.close()" aria-label="Fermer la recherche">✕</button></div>
    <div id="sps-res" class="sps-res" aria-live="polite"></div>
  </div>
</div>"""

SCRIPT = r"""<script>
(function(){
  if(window.spSearch) return;
  var D = document, MIN = 2, PER = 5, KEEP = 60;
  // Onglets que le menu affiche (spNav) : section -> menu / sous-menu ; Simulation, Picks et Historique de Foot et
  // Basket sont des vues (.eb-view[data-view]) de la meme section.
  var SECS = [
    {id: 'sport-today', main: 'today', sub: 'today', g: 'today'},
    {id: 'sport-nbasim', main: 'basket', sp: 'nba', views: {sim: 'nbasim', picks: 'nbapicks', hist: 'nbahist2'}},
    {id: 'sport-footsim', main: 'foot', sp: 'foot', views: {sim: 'footsim', picks: 'footpicks', hist: 'foothist2'}},
    {id: 'sport-footanalyse', main: 'foot', sub: 'footanalyse', g: 'footana'},
    {id: 'sport-iabank', main: 'iabank', sub: 'iabank', g: 'iabank'}
  ];
  var GROUPS = [['today', '🎯 Aujourd’hui · picks du jour'], ['nba-sim', '🏀 Matchs NBA'], ['nba-row', '🏀 Props et joueurs NBA'],
    ['nba-picks', '🏀 Picks NBA'], ['nba-fiche', '🏀 Fiches joueurs NBA (Analyse)'], ['foot-sim', '⚽ Matchs foot'],
    ['foot-row', '⚽ Props et joueurs foot'], ['foot-picks', '⚽ Picks foot'], ['footana', '⚽ Analyse foot'],
    ['iabank', '🤖 Bankroll IA'], ['nba-hist', '🏀 Historique NBA'], ['foot-hist', '⚽ Historique foot']];
  // Blocs qui representent un resultat : leur texte entier (textContent, sans redescendre dedans) ; .eb-card sert de
  // bloc de repli pour le texte qui n'est dans aucun de ces blocs (titre de carte…).
  var LEAF_TAG = {TR: 1, LI: 1, SUMMARY: 1, H3: 1, H4: 1};
  var LEAF_CLS = ['eb-item', 'td-pick', 'td-m', 'pe-card', 'vd-top', 'eb-kpi', 'eb-meta', 'eb-team', 'eb-vs', 'eb-row'];
  // Texte ignore : code, graphiques, boutons d'action, notes explicatives, cartes live masquees
  var SKIP_TAG = {SCRIPT: 1, STYLE: 1, TEMPLATE: 1, NOSCRIPT: 1, svg: 1, SVG: 1, SELECT: 1, TEXTAREA: 1, TH: 1, INPUT: 1};
  var SKIP_CLS = ['eb-note', 'legend', 'sp-lazy', 'eb-seg'];
  var DIRTY = 'script,style,svg,template,select,textarea,.eb-note,.eb-seg';   // bloc a parcourir finement

  function norm(s){
    return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
      .replace(/[\u2019\u2018`\u00b4]/g, "'").replace(/[\u2010-\u2015\u2212]/g, '-')
      .replace(/\u0153/g, 'oe').replace(/\u00e6/g, 'ae').replace(/\u00df/g, 'ss').replace(/\s+/g, ' ');
  }
  function esc(s){ return s.replace(/[&<>"]/g, function(c){ return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function txt(el){ return el ? el.textContent.replace(/\s+/g, ' ').trim() : ''; }
  function anyCls(cl, L){ for(var i = 0; i < L.length; i++) if(cl.contains(L[i])) return true; return false; }

  // ── Index : blocs de texte des sections (reconstruit apres tout changement du DOM) ──
  // Parcours de l'arbre jusqu'aux blocs seulement (16/11/2026, telephone simule x4 : closest() par noeud de texte
  // 3 s, parcours de tous les noeuds 1,6 s, parcours jusqu'aux blocs ~0,3 s).
  var idx = null, dirty = true;
  function build(){
    var out = [];
    SECS.forEach(function(S){
      var root = D.getElementById(S.id); if(!root) return;
      var map = new Map();
      function entry(el){
        var e = map.get(el);
        if(!e){ e = {el: el, p: [], s: S}; map.set(el, e); out.push(e); }
        return e;
      }
      function walk(el, cur){
        for(var c = el.firstChild; c; c = c.nextSibling){
          if(c.nodeType === 3){ if(cur && /\S/.test(c.nodeValue)) cur.p.push(c.nodeValue); continue; }
          if(c.nodeType !== 1) continue;
          var tag = c.tagName, cl = c.classList;
          if(SKIP_TAG[tag] || (tag === 'BUTTON' && !cl.contains('eb-item'))) continue;
          if(cl.length && (anyCls(cl, SKIP_CLS) || (cl.contains('eb-livecard') && cl.contains('eb-off')))) continue;
          if(LEAF_TAG[tag] || (cl.length && anyCls(cl, LEAF_CLS))){
            if(tag === 'TR' && c.firstElementChild && c.firstElementChild.tagName === 'TH') continue;   // en-tete de tableau
            // argumentaire depliable d'un pick : rattache a la ligne du pick
            var e = entry(tag === 'TR' && cl.contains('eb-why') && c.previousElementSibling ? c.previousElementSibling : c);
            if(c.querySelector(DIRTY)) walk(c, e); else flat(c, e, 2);
            continue;
          }
          walk(c, cl.contains('eb-card') ? entry(c) : cur);
        }
      }
      // texte d'un bloc : enfants et petits-enfants separes par une espace (cellules d'une ligne, morceaux d'une carte)
      function flat(el, e, d){
        for(var k = el.firstChild; k; k = k.nextSibling){
          if(k.nodeType === 3) e.p.push(k.nodeValue);
          else if(k.nodeType === 1){
            if(SKIP_TAG[k.tagName] || (k.tagName === 'BUTTON' && !k.classList.contains('eb-item'))) continue;   // « Simu → »…
            if(d > 1) flat(k, e, d - 1); else e.p.push(k.textContent);
          }
        }
      }
      walk(root, null);
    });
    out = out.filter(function(e){ e.t = e.p.join(' ').replace(/\s+/g, ' ').trim(); e.p = null; return !!e.t; });
    // normalisation en un seul appel (plus rapide que bloc par bloc) ; \u0001 ne bouge pas a la normalisation
    var N = norm(out.map(function(e){ return e.t; }).join('\u0001')).split('\u0001');
    out.forEach(function(e, i){ e.n = N[i]; });
    idx = out; dirty = false;
  }
  var wrap = D.querySelector('.container') || D.body;
  new MutationObserver(function(){ dirty = true; }).observe(wrap, {childList: true, subtree: true, characterData: true});

  // Fiches joueurs NBA (Basket › Analyse) : index embarque (site_lazy) tant que le fragment n'est pas dans la page
  function fiches(){
    var X = window.SP_SEARCH_PLAYERS;
    if(X && X.p) return X.p.map(function(p){ var g = X.g[p[1]] || ['', 0]; return {name: p[0], game: g[0], hist: !!g[1]}; });
    var out = [];
    D.querySelectorAll('#sport-analyse .player-analyse[data-player-name]').forEach(function(c){
      out.push({name: c.getAttribute('data-player-name'), game: '', hist: !!c.closest('#nba-history-block')});
    });
    return out;
  }
  function games(){ var X = window.SP_SEARCH_PLAYERS; return X && X.g ? X.g : []; }

  // ── Recherche ──
  function has(n, toks){ for(var i = 0; i < toks.length; i++) if(n.indexOf(toks[i]) < 0) return false; return true; }
  function classify(e){
    var S = e.s, el = e.el, view = null, gk;
    if(S.views){ var v = el.closest('.eb-view'); view = v && S.views[v.getAttribute('data-view')] ? v.getAttribute('data-view') : 'sim'; }
    var det = el.closest('.eb-detail[data-id]'), item = el.matches('.eb-item[data-id]') ? el : null;
    var host = det || item, scope = host ? host.closest('[data-eb]') : null;
    var row = !!det && el.tagName === 'TR';
    if(S.views) gk = S.sp + '-' + (view === 'sim' ? (row ? 'row' : 'sim') : view);
    else gk = S.g;
    return {g: gk, main: S.main, sub: S.views ? S.views[view] : S.sub,
            scope: scope, id: host && scope ? host.getAttribute('data-id') : null, merge: !!(host && scope) && !row};
  }
  var MI = null;   // cache par recherche : titre du match
  function matchInfo(scope, id){
    var key = scope.getAttribute('data-eb') + '|' + id;
    if(MI && MI[key]) return MI[key];
    var q = '[data-id="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]';
    var it = scope.querySelector('.eb-item' + q), det = scope.querySelector('.eb-detail' + q);
    var t = txt(it && it.querySelector('.eb-item-t')) || txt(det && det.querySelector('.eb-vs')) || txt(it).slice(0, 80);
    var s = txt(it && it.querySelector('.eb-item-top')) || txt(det && det.querySelector('.eb-meta'));
    var r = {item: it, det: det, t: t, s: s};
    if(MI) MI[key] = r;
    return r;
  }
  function search(q){
    var toks = norm(q).trim().split(' ').filter(Boolean);
    if(dirty || !idx) build();
    var G = {}, merged = {};
    MI = {};
    function add(gk, r){ var g = G[gk] || (G[gk] = {n: 0, list: [], seen: {}}); g.n++; if(g.list.length < KEEP) g.list.push(r); }
    idx.forEach(function(e){
      if(!has(e.n, toks)) return;
      if(!e.el.isConnected){ dirty = true; return; }
      var c = classify(e);
      if(c.merge){   // un seul resultat par match (liste + fiche du match)
        var k = c.scope.getAttribute('data-eb') + '|' + c.id, m = merged[k];
        var isItem = e.el.matches('.eb-item');
        if(!m){
          var mi = matchInfo(c.scope, c.id);
          m = merged[k] = {kind: 'match', el: e.el, main: c.main, sub: c.sub, scope: c.scope, id: c.id, det: mi.det,
                           t: mi.t || e.t.slice(0, 80), s: mi.s, m: '', title: has(norm(mi.t), toks)};
          add(c.g, m);
        }
        if(!isItem && !m.m && !m.title) m.m = e.t;     // extrait : ou le nom apparait dans la fiche du match
        if(m.el.matches('.eb-item') && !isItem && !m.title) m.el = e.el;
        return;
      }
      var g = G[c.g], key = e.n;
      if(g && g.seen[key]) return;                   // lignes identiques (meme pick affiche deux fois)
      var ctx = '';
      if(c.scope && c.id){ ctx = matchInfo(c.scope, c.id).t; }
      add(c.g, {kind: 'el', el: e.el, main: c.main, sub: c.sub, scope: c.scope, id: c.id, t: e.t, s: ctx});
      G[c.g].seen[key] = 1;
    });
    // Basket › Analyse : joueurs (nom) et matchs (equipes)
    fiches().forEach(function(f){
      if(!has(norm(f.name), toks)) return;
      add('nba-fiche', {kind: 'fiche', name: f.name, game: f.game, hist: f.hist, t: f.name,
                        s: 'Fiche joueur' + (f.game ? ' · ' + f.game : '') + (f.hist ? ' · historique 48 h' : '')});
    });
    var gseen = {};
    games().forEach(function(gm){
      if(gseen[gm[0] + gm[1]] || !has(norm(gm[0]), toks)) return;
      gseen[gm[0] + gm[1]] = 1;
      add('nba-fiche', {kind: 'fiche', game: gm[0], hist: !!gm[1], first: true, t: gm[0],
                        s: 'Fiches joueurs du match' + (gm[1] ? ' · historique 48 h' : '')});
    });
    Object.keys(G).forEach(function(k){   // matchs dont le titre correspond en premier
      G[k].list.forEach(function(r, i){ r._i = i; });
      G[k].list.sort(function(a, b){ return ((b.title ? 1 : 0) - (a.title ? 1 : 0)) || (a._i - b._i); });
    });
    MI = null;
    return {toks: toks, G: G};
  }

  // ── Affichage ──
  function snip(t, toks, max){
    var nn = '', map = [];
    for(var i = 0; i < t.length; i++){ var c = norm(t[i]); for(var k = 0; k < c.length; k++){ nn += c[k]; map.push(i); } }
    var rs = [];
    toks.forEach(function(tk){ var p = 0; while(tk && (p = nn.indexOf(tk, p)) >= 0){ rs.push([map[p], map[p + tk.length - 1] + 1]); p += tk.length; } });
    rs.sort(function(a, b){ return a[0] - b[0]; });
    var s = 0, e = t.length;
    if(t.length > max){
      var f = rs.length ? rs[0][0] : 0;
      s = Math.max(0, f - Math.floor(max / 3)); e = Math.min(t.length, s + max); s = Math.max(0, e - max);
      if(s > 0 && /[\udc00-\udfff]/.test(t[s])) s++;
      if(e < t.length && /[\ud800-\udbff]/.test(t[e - 1])) e--;
    }
    var out = s > 0 ? '…' : '', cur = s;
    rs.forEach(function(r){
      if(r[1] <= cur || r[0] >= e) return;
      var a = Math.max(r[0], cur), b = Math.min(r[1], e);
      out += esc(t.slice(cur, a)) + '<mark>' + esc(t.slice(a, b)) + '</mark>'; cur = b;
    });
    return out + esc(t.slice(cur, e)) + (e < t.length ? '…' : '');
  }
  var box, inp, res, last = null, open = {}, timer = 0;
  function render(){
    if(!res) return;
    var q = inp.value, nq = norm(q).trim();
    if(nq.length < MIN){
      res.innerHTML = '<div class="sps-hint">Tape au moins 2 lettres : une <b>équipe</b> (Celtics, Marseille…), un <b>joueur</b> '
        + '(Tatum, Mbappé…) ou un <b>match</b>. Les accents et majuscules ne comptent pas.</div>';
      last = null; return;
    }
    last = search(q);
    var html = '', any = false, n = 0;
    GROUPS.forEach(function(gd){
      var g = last.G[gd[0]]; if(!g || !g.n) return;
      any = true;
      var lim = open[gd[0]] ? KEEP : PER;
      html += '<div class="sps-g"><div class="sps-gh"><span>' + gd[1] + '</span><span>' + g.n + '</span></div>';
      g.list.slice(0, lim).forEach(function(r, i){
        var id = gd[0] + ':' + i, body;
        if(r.kind === 'el'){
          body = '<span class="sps-t">' + snip(r.t, last.toks, 130) + '</span>' + (r.s ? '<span class="sps-s">' + esc(r.s) + '</span>' : '');
        } else {
          body = '<span class="sps-t">' + snip(r.t, last.toks, 90) + '</span>' + (r.s ? '<span class="sps-s">' + esc(r.s) + '</span>' : '')
            + (r.m ? '<span class="sps-m">' + snip(r.m, last.toks, 110) + '</span>' : '');
        }
        html += '<button type="button" class="sps-r" data-r="' + id + '">' + body + '</button>';
        n++;
      });
      if(g.list.length > lim) html += '<button type="button" class="sps-more" data-g="' + gd[0] + '">+ ' + (g.n - lim) + ' autre' + (g.n - lim > 1 ? 's' : '') + '</button>';
      else if(g.n > g.list.length) html += '<div class="sps-hint sps-cap">… et ' + (g.n - g.list.length) + ' de plus : précise la recherche.</div>';
      html += '</div>';
    });
    res.innerHTML = any ? html : '<div class="sps-hint">Aucun résultat pour « <b>' + esc(q.trim()) + '</b> ». Essaie le nom de famille seul, '
      + 'ou le nom de l’équipe en anglais pour la NBA (Celtics, Lakers…).</div>';
  }
  function pick(id){
    if(!last) return null;
    var p = id.split(':'), g = last.G[p[0]];
    return g ? g.list[+p[1]] : null;
  }

  // ── Navigation vers un resultat ──
  function selectMatch(scope, id){
    scope.querySelectorAll('.eb-item[data-id]').forEach(function(b){ b.classList.toggle('on', b.getAttribute('data-id') === id); });
    scope.querySelectorAll('.eb-detail[data-id]').forEach(function(d){ d.classList.toggle('on', d.getAttribute('data-id') === id); });
  }
  function pane(p){
    var card = p.parentElement, name = p.getAttribute('data-pane');
    [].forEach.call(card.children, function(c){
      if(c.classList.contains('eb-pane')) c.classList.toggle('on', c === p);
      if(c.classList.contains('eb-seg')) [].forEach.call(c.querySelectorAll('button[onclick*="ebSeg"]'), function(b){
        b.classList.toggle('on', (b.getAttribute('onclick') || '').indexOf("'" + name + "'") >= 0); });
    });
  }
  // Deplie tout ce qui cache l'element : match non selectionne, onglet Joueurs/Props, <details>, « voir plus »,
  // carte de match NBA repliee, historique 48 h, fiche joueur repliee.
  function reveal(el){
    var chain = [], a = el;
    while(a && a !== D.body){ chain.unshift(a); a = a.parentElement; }
    chain.forEach(function(x){
      var cl = x.classList;
      if(cl.contains('eb-view') && !cl.contains('on') && x.parentElement)
        [].forEach.call(x.parentElement.children, function(v){ if(v.classList.contains('eb-view')) v.classList.toggle('on', v === x); });
      if(cl.contains('eb-detail') && !cl.contains('on') && x.hasAttribute('data-id')){ var sc = x.closest('[data-eb]'); if(sc) selectMatch(sc, x.getAttribute('data-id')); }
      if(cl.contains('eb-pane') && !cl.contains('on')) pane(x);
      if(x.tagName === 'DETAILS' && !x.open) x.open = true;
      if(cl.contains('eb-off') && !cl.contains('eb-livecard')) cl.remove('eb-off');
      if(cl.contains('nba-match-body') && !cl.contains('show')){
        if(typeof window.toggleNbaMatch === 'function' && x.id) toggleNbaMatch(x.id.slice(9)); else cl.add('show');
      }
      if(x.style && x.style.display === 'none') x.style.display = '';
    });
    if(el.matches('.player-analyse') && typeof window._setExpanded === 'function') _setExpanded(el, true);
  }
  function show(t){
    var v = t;
    while(v && v !== D.body && !v.getClientRects().length) v = v.parentElement;
    if(!v || v === D.body) return;
    v.classList.add('sps-tgt');
    var far = Math.abs(v.getBoundingClientRect().top) > 2 * innerHeight;      // loin : saut direct, sinon defilement doux
    v.scrollIntoView({behavior: far ? 'auto' : 'smooth', block: v.offsetHeight > innerHeight * 0.6 ? 'start' : 'center'});
    v.classList.remove('sps-flash'); void v.offsetWidth; v.classList.add('sps-flash');
    clearTimeout(v._spsT); v._spsT = setTimeout(function(){ v.classList.remove('sps-flash', 'sps-tgt'); }, 2600);
  }
  function nav(main, sub){ if(typeof window.spNav === 'function') spNav(main, sub); }
  function go(r){
    if(r.kind === 'fiche') return goFiche(r);
    if(!r.el.isConnected){ dirty = true; render(); return; }    // section rechargee par la veille entre-temps
    close();
    nav(r.main, r.sub);
    var target = r.el;
    if(r.kind === 'match'){ selectMatch(r.scope, r.id); if(target.matches('.eb-item') && r.det) target = r.det; }
    reveal(target);
    setTimeout(function(){ show(target); }, 80);
  }
  function gameHead(c){
    var t = c.closest('.nba-teams2'); if(!t) return c;
    if(t.previousElementSibling) return t.previousElementSibling;      // match du jour : « Magic @ Celtics »
    var b = t.closest('[id^="nba-body-"]');
    return (b && b.previousElementSibling) || t;                       // historique : en-tete du match
  }
  function findCard(r){
    var best = null;
    D.querySelectorAll('#sport-analyse .player-analyse[data-player-name]').forEach(function(c){
      if(best && best.ok) return;
      if(r.name && c.getAttribute('data-player-name') !== r.name) return;
      if(!!c.closest('#nba-history-block') !== !!r.hist) return;
      var ok = !r.game || norm(txt(gameHead(c))).indexOf(norm(r.game.replace(/ · \d\d\/\d\d$/, ''))) >= 0;
      if(r.first && !ok) return;
      if(!best || ok) best = {c: c, ok: ok};
    });
    return best && best.c;
  }
  var toastEl = null;
  function toast(msg){
    if(!toastEl){ toastEl = D.createElement('div'); toastEl.id = 'sps-toast'; toastEl.setAttribute('role', 'status'); D.body.appendChild(toastEl); }
    toastEl.textContent = msg || ''; toastEl.style.display = msg ? 'block' : 'none';
  }
  function goFiche(r){
    close();
    nav('basket', 'analyse');
    var done = function(){
      toast('');
      var c = findCard(r);
      if(!c){ toast('Fiche introuvable dans Basket › Analyse'); setTimeout(function(){ toast(''); }, 2500); return; }
      reveal(c);
      setTimeout(function(){ show(r.first ? gameHead(c) : c); }, 80);
    };
    var lz = D.querySelector('#sport-analyse > .sp-lazy');
    if(lz && window.spLazy){
      if(lz.getAttribute('data-st') !== 'ok') toast('Chargement des fiches joueurs…');
      spLazy.section('sport-analyse', done);
    } else done();
  }

  // ── Ouverture / fermeture, clavier ──
  function ensure(){
    if(box) return;
    box = D.getElementById('sps'); inp = D.getElementById('sps-q'); res = D.getElementById('sps-res');
    inp.addEventListener('input', function(){ open = {}; clearTimeout(timer); timer = setTimeout(render, 90); });
    inp.addEventListener('keydown', function(e){
      if(e.key === 'Enter'){ e.preventDefault(); clearTimeout(timer); render(); var f = res.querySelector('.sps-r'); if(f) f.click(); }
      else if(e.key === 'ArrowDown'){ var f2 = res.querySelector('.sps-r,.sps-more'); if(f2){ e.preventDefault(); f2.focus(); } }
    });
    res.addEventListener('click', function(e){
      var b = e.target.closest('.sps-r,.sps-more'); if(!b) return;
      if(b.classList.contains('sps-more')){ open[b.getAttribute('data-g')] = 1; render(); return; }
      var r = pick(b.getAttribute('data-r')); if(r) go(r);
    });
    res.addEventListener('keydown', function(e){
      if(e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      var L = [].slice.call(res.querySelectorAll('.sps-r,.sps-more')), i = L.indexOf(D.activeElement);
      if(i < 0) return;
      e.preventDefault();
      if(e.key === 'ArrowDown' && i < L.length - 1) L[i + 1].focus();
      else if(e.key === 'ArrowUp'){ if(i > 0) L[i - 1].focus(); else inp.focus(); }
    });
  }
  function isOpen(){ return box && !box.hidden; }
  function openS(){
    ensure();
    box.hidden = false;
    inp.focus(); inp.select();
    render();
    if(dirty) setTimeout(function(){ if(dirty && isOpen()) build(); }, 50);   // index pret avant la 1re lettre
  }
  function close(){ if(box) box.hidden = true; }
  D.addEventListener('keydown', function(e){
    if(isOpen()){ if(e.key === 'Escape'){ e.preventDefault(); close(); } return; }
    var k = e.key, t = e.target, tag = t && t.tagName;
    var typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (t && t.isContentEditable);
    var slash = k === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !typing;
    var ck = (k === 'k' || k === 'K') && (e.ctrlKey || e.metaKey) && !e.altKey;
    if(!slash && !ck) return;
    var gate = D.getElementById('auth-gate');
    if(gate && gate.getClientRects().length && getComputedStyle(gate).display !== 'none') return;   // page de connexion
    if(!D.getElementById('sps')) return;
    e.preventDefault(); openS();
  });
  window.spSearch = {open: openS, close: close, search: function(q){ return search(q); }, rebuild: function(){ dirty = true; }};
})();
</script>"""


def inject(html):
    """Ajoute le bouton dans le menu principal, le panneau de recherche et son script. Sans effet si deja fait ou si
    le menu est introuvable (la page reste valide, sans recherche)."""
    if MARK in html:
        return html
    m = re.search(r'<div class="sp-nav"[^>]*>', html)
    if not m:
        print("  ⚠️ Recherche : menu principal (sp-nav) introuvable, non ajoutee")
        return html
    close = html.find("</div>", m.end())
    if close < 0:
        return html
    tag = m.group(0).replace('class="sp-nav"', 'class="sp-nav sps-on"', 1)
    html = html[:m.start()] + tag + html[m.end():close] + NAV_BTN + html[close:]
    k = html.rfind("</body>")
    block = MARK + CSS + OVERLAY + SCRIPT
    return html[:k] + block + html[k:] if k >= 0 else html + block


# ── Index des fiches joueurs de Basket › Analyse (fragment charge a la demande) ──
_MATCHUP = re.compile(r'<div[^>]*>([^<>]{1,60}?)\s*<span[^>]*>@</span>\s*([^<>]{1,60}?)</div>')
_PLAYER = re.compile(r'data-player-name="([^"]*)"')
_DATE = re.compile(r">(\d{4})-(\d{2})-(\d{2})<")


def _hist_span(body):
    """(debut, fin) du bloc « Historique NBA (48h) » (#nba-history-block, div imbriques comptes), sinon (0, 0)."""
    i = body.find('id="nba-history-block"')
    if i < 0:
        return 0, 0
    depth = 0
    for m in re.finditer(r"<(/?)div(?=[\s>])", body[body.rfind("<", 0, i):]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return i, body.rfind("<", 0, i) + m.end()
    return i, len(body)


def analyse_index(body):
    """{g: [[« Away @ Home », historique 0/1], ...], p: [[nom, indice du match], ...]} depuis le HTML de la section
    Basket › Analyse (matchs du jour + historique 48 h). Les noms et matchs sont lus dans l'ordre du document."""
    toks = sorted([(m.start(), "g", m) for m in _MATCHUP.finditer(body)] +
                  [(m.start(), "p", m) for m in _PLAYER.finditer(body)], key=lambda x: x[0])
    games, players, seen, gi = [], [], set(), -1
    h0, h1 = _hist_span(body)
    for pos, kind, m in toks:
        if kind == "g":
            label = f"{_html.unescape(m.group(1)).strip()} @ {_html.unescape(m.group(2)).strip()}"
            hist = h0 <= pos < h1
            if hist:
                d = _DATE.search(body, m.end(), m.end() + 600)
                if d:
                    label += f" · {d.group(3)}/{d.group(2)}"
            games.append([label, 1 if hist else 0])
            gi = len(games) - 1
        else:
            name = _html.unescape(m.group(1)).strip()
            if name and (name, gi) not in seen:
                seen.add((name, gi))
                players.append([name, gi])
    return {"g": games, "p": players}


def analyse_script(body):
    """<script> qui embarque l'index des fiches joueurs (appele par site_lazy.split)."""
    data = json.dumps(analyse_index(body), ensure_ascii=False, separators=(",", ":"))
    return "<script>window.SP_SEARCH_PLAYERS=" + data.replace("</", "<\\/") + ";</script>"
