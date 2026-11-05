"""
site_newpicks.py — Notification « 🆕 N nouveaux picks » cliquable (demande de Wilson, 05/11/2026).

Les lignes de pick des onglets Picks (simulateur NBA, simulateur foot, value bets) portent data-np (identifiant stable)
et data-npl (libelle). Au chargement et a chaque mise a jour des sections (la veille republie nba_sim.html /
foot_sim.html), la page compare ces identifiants a ceux deja vus (localStorage) : les nouveaux sont marques 🆕 et une
notification s'affiche ; un clic ouvre la liste, et chaque ligne mene au pick dans son onglet.
Remplace l'ancienne detection (cartes data-pick-id d'une section que le menu n'affiche plus depuis la refonte).
"""

CSS = """<style>
#sp-np-toast{position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:9998;display:none;align-items:center;gap:10px;
  background:linear-gradient(90deg,#fb923c,#f97316);color:#0a1628;border-radius:12px;padding:10px 12px 10px 16px;font:700 14px var(--sans,system-ui);
  box-shadow:0 6px 24px rgba(251,146,60,.45);cursor:pointer;max-width:calc(100vw - 32px)}
#sp-np-toast.on{display:flex}
#sp-np-toast u{text-decoration:none;background:rgba(10,22,40,.14);border-radius:8px;padding:4px 9px;white-space:nowrap}
#sp-np-toast i{font-style:normal;opacity:.7;padding:0 4px}
tr.sp-np-new>td:first-child{box-shadow:inset 3px 0 0 #fb923c}
tr.sp-np-new>td:first-child::before{content:"🆕 ";}
tr.sp-np-flash{animation:spNpFlash 2s ease-out}
@keyframes spNpFlash{0%,40%{background:rgba(251,146,60,.28)}100%{background:transparent}}
.sp-np-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 0;border-bottom:1px solid rgba(255,255,255,.06);font-size:14px}
.sp-np-row button{border:0;border-radius:10px;padding:7px 12px;background:#fb923c;color:#0a1628;font:700 13px inherit;cursor:pointer;flex-shrink:0}
</style>"""

JS = r"""<script>
(function(){
  if(window.spNewPicks) return;
  var KEY = 'sp_np_seen', NEW = {}, timer = null, hideT = null;
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function rows(){ return [].slice.call(document.querySelectorAll('tr[data-np]')); }
  function where(el){   // onglet ou se trouve la ligne
    var b = el.closest('[data-eb]'), k = b ? b.getAttribute('data-eb') : '';
    return k === 'footpick' ? ['foot', 'footpicks'] : ['basket', 'nbapicks'];
  }
  function toast(){
    var n = Object.keys(NEW).length, t = document.getElementById('sp-np-toast');
    if(!t){
      t = document.createElement('div'); t.id = 'sp-np-toast';
      t.onclick = function(e){ if(e.target.tagName === 'I'){ t.classList.remove('on'); return; } spNewPicks.open(); };
      document.body.appendChild(t);
    }
    if(!n){ t.classList.remove('on'); return; }
    t.innerHTML = '🆕 ' + n + ' nouveau' + (n > 1 ? 'x' : '') + ' pick' + (n > 1 ? 's' : '') + ' <u>Voir</u><i title="Fermer">✕</i>';
    t.classList.add('on');
    clearTimeout(hideT); hideT = setTimeout(function(){ t.classList.remove('on'); }, 20000);
  }
  function scan(){
    var R = rows(), raw = null; try { raw = localStorage.getItem(KEY); } catch(e){}
    // aucune ligne : on note quand meme la visite, sinon les premiers picks a paraitre passeraient pour une
    // premiere visite (pas de notification) — cas reel du 05/11/2026, jours sans pick
    if(!R.length){ if(raw === null) try { localStorage.setItem(KEY, '[]'); } catch(e){} return; }
    var seen = {}; try { (JSON.parse(raw || '[]') || []).forEach(function(id){ seen[id] = 1; }); } catch(e){}
    var first = raw === null, added = 0, ids = [];
    R.forEach(function(el){
      var id = el.getAttribute('data-np'); ids.push(id);
      if(NEW[id]){ el.classList.add('sp-np-new'); return; }      // section rechargee : on remet la marque
      if(!first && !seen[id]){ NEW[id] = el.getAttribute('data-npl') || id; el.classList.add('sp-np-new'); added++; }
    });
    var all = Object.keys(seen).concat(ids.filter(function(id){ return !seen[id]; }));
    try { localStorage.setItem(KEY, JSON.stringify(all.slice(-3000))); } catch(e){}
    if(added) toast();
  }
  function go(id){
    var el = document.querySelector('tr[data-np="' + id.replace(/"/g, '\\"') + '"]'); if(!el) return;
    var w = where(el);
    if(typeof _bkCloseForm === 'function') _bkCloseForm();
    if(typeof spNav === 'function') spNav(w[0], w[1]);
    setTimeout(function(){
      var e2 = document.querySelector('tr[data-np="' + id.replace(/"/g, '\\"') + '"]') || el;
      e2.scrollIntoView({behavior: 'smooth', block: 'center'});
      e2.classList.remove('sp-np-flash'); void e2.offsetWidth; e2.classList.add('sp-np-flash');
    }, 250);
  }
  window.spNewPicks = {
    open: function(){
      var ids = Object.keys(NEW);
      var t = document.getElementById('sp-np-toast'); if(t) t.classList.remove('on');
      if(ids.length === 1){ go(ids[0]); return; }
      if(typeof _bkEnsureModalRoot !== 'function'){ go(ids[0]); return; }
      var root = _bkEnsureModalRoot();
      root.innerHTML = '<div class="bk-modal-bd" onclick="_bkCloseForm()"></div><div class="bk-modal-card" role="dialog" aria-modal="true">'
        + '<div class="bk-m-hd"><button class="bk-m-cancel" onclick="_bkCloseForm()">Fermer</button><div class="bk-m-title">Nouveaux picks</div>'
        + '<div style="width:64px"></div></div><div class="bk-m-body">'
        + ids.map(function(id, i){ return '<div class="sp-np-row"><span>' + esc(NEW[id]) + '</span><button data-i="' + i + '">Voir</button></div>'; }).join('')
        + '</div></div>';
      root.querySelectorAll('.sp-np-row button').forEach(function(b){ b.onclick = function(){ go(ids[+b.getAttribute('data-i')]); }; });
      setTimeout(function(){ root.classList.add('open'); }, 10);
    },
    scan: scan
  };
  // au chargement, puis a chaque remplacement d'une section (veille) : nouvelle lecture, sans bloquer la page
  function later(){ clearTimeout(timer); timer = setTimeout(scan, 1200); }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', later); else later();
  new MutationObserver(function(m){
    for(var i = 0; i < m.length; i++) for(var j = 0; j < m[i].addedNodes.length; j++){
      var n = m[i].addedNodes[j];
      if(n.nodeType === 1 && (n.matches && n.matches('tr[data-np]') || n.querySelector && n.querySelector('tr[data-np]'))){ later(); return; }
    }
  }).observe(document.body, {childList: true, subtree: true});
})();
</script>"""

HTML = CSS + JS


def inject(html):
    """Ajoute la notification juste avant le dernier </body>."""
    k = html.rfind("</body>")
    return html[:k] + HTML + html[k:] if k >= 0 else html
