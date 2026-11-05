"""
site_chat.py — chat du site (bouton 💬) : Wilson discute avec Claude depuis n'importe quelle page.

Le message part dans Firestore (users/<uid>/chat, connexion Firebase du site) ; le relais chat_bot.py
(workflow chat.yml) fait repondre Claude Code avec l'abonnement de Wilson et ecrit la reponse au meme
endroit, affichee en direct. Insere par generate_site.main() juste avant </body> (inject()).
"""

HTML = r"""
<!-- Chat avec Claude (site_chat.py) -->
<style id="sp-chat-css">
#sp-chat-fab{position:fixed;right:18px;bottom:18px;z-index:10003;width:54px;height:54px;border-radius:999px;border:0;cursor:pointer;
  background:var(--acc,#95e04a);color:var(--acc-ink,#0b1208);font-size:24px;display:flex;align-items:center;justify-content:center;
  box-shadow:0 6px 24px rgba(0,0,0,.45),0 0 0 4px var(--bg,#0a0d0b);-webkit-tap-highlight-color:transparent}
#sp-chat-fab:active{transform:scale(.93)}
#sp-chat-fab .dot{position:absolute;top:4px;right:4px;width:12px;height:12px;border-radius:9px;background:#ef4444;border:2px solid var(--bg,#0a0d0b);display:none}
#sp-chat-fab.unread .dot{display:block}
#sp-chat{position:fixed;right:18px;bottom:84px;z-index:10004;width:min(420px,calc(100vw - 36px));height:min(640px,calc(100vh - 110px));
  display:none;flex-direction:column;background:var(--panel,#0f1411);border:1px solid var(--line2,#2a352c);border-radius:16px;
  box-shadow:0 18px 60px rgba(0,0,0,.6);overflow:hidden;font-family:var(--sans,sans-serif);color:var(--tx,#eef3ef)}
#sp-chat.open{display:flex}
#sp-chat .ch-h{display:flex;align-items:center;gap:10px;padding:12px 14px;border-bottom:1px solid var(--line,#1f2a22)}
#sp-chat .ch-h b{font-size:15px}
#sp-chat .ch-h small{display:block;font:11px var(--mono,monospace);color:var(--di,#6b7a70)}
#sp-chat .ch-h .sp{flex:1}
#sp-chat .ch-h button{background:transparent;border:1px solid var(--line2,#2a352c);color:var(--mu,#9aa89e);border-radius:8px;
  padding:6px 10px;font:500 12.5px var(--sans,sans-serif);cursor:pointer}
#sp-chat .ch-h button.x{border:0;font-size:20px;padding:2px 6px;color:var(--tx,#eef3ef)}
#sp-chat .ch-b{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px;overscroll-behavior:contain}
#sp-chat .m{max-width:88%;padding:9px 12px;border-radius:14px;font-size:14.5px;line-height:1.5;word-wrap:break-word;overflow-wrap:anywhere}
#sp-chat .m.u{align-self:flex-end;background:var(--acc-soft,rgba(149,224,74,.12));border:1px solid rgba(149,224,74,.3);border-bottom-right-radius:4px;white-space:pre-wrap}
#sp-chat .m.a{align-self:flex-start;background:var(--chip,#1a221c);border-bottom-left-radius:4px}
#sp-chat .m.a ul{margin:4px 0 4px 18px;padding:0}
#sp-chat .m.a code{font:12.5px var(--mono,monospace);background:rgba(255,255,255,.06);padding:1px 4px;border-radius:4px}
#sp-chat .m.a h4{font-size:14.5px;margin:6px 0 2px}
#sp-chat .m.err{border:1px solid rgba(239,68,68,.4)}
#sp-chat .m.a .tw{overflow-x:auto;margin:6px 0}
#sp-chat .m.a table{border-collapse:collapse;font-size:12.5px;white-space:nowrap}
#sp-chat .m.a th,#sp-chat .m.a td{border-bottom:1px solid var(--line2,#2a352c);padding:4px 8px;text-align:left}
#sp-chat .m.a th{color:var(--mu,#9aa89e);font-weight:600}
#sp-chat .sep{align-self:center;font:11px var(--mono,monospace);color:var(--di,#6b7a70);padding:4px 0}
#sp-chat .wait{align-self:flex-start;font:12.5px var(--mono,monospace);color:var(--mu,#9aa89e)}
#sp-chat .wait i{display:inline-block;width:6px;height:6px;border-radius:3px;background:var(--acc,#95e04a);margin-right:4px;animation:spch 1.2s infinite}
#sp-chat .wait i:nth-child(2){animation-delay:.2s}#sp-chat .wait i:nth-child(3){animation-delay:.4s;margin-right:8px}
@keyframes spch{0%,80%,100%{opacity:.25}40%{opacity:1}}
#sp-chat .empty{color:var(--mu,#9aa89e);font-size:13.5px;line-height:1.6;padding:6px 2px}
#sp-chat .empty button{display:block;margin:8px 0 0;background:var(--chip,#1a221c);border:1px solid var(--line2,#2a352c);color:var(--tx,#eef3ef);
  border-radius:10px;padding:8px 12px;font:13px var(--sans,sans-serif);cursor:pointer;text-align:left;width:100%}
#sp-chat .ch-f{display:flex;gap:8px;padding:10px;border-top:1px solid var(--line,#1f2a22);background:var(--panel,#0f1411)}
#sp-chat textarea{flex:1;resize:none;min-height:44px;max-height:140px;background:var(--bg,#0a0d0b);color:var(--tx,#eef3ef);
  border:1px solid var(--line2,#2a352c);border-radius:12px;padding:11px 12px;font:16px var(--sans,sans-serif);outline:none}
#sp-chat textarea:focus{border-color:var(--acc,#95e04a)}
#sp-chat .send{width:46px;border:0;border-radius:12px;background:var(--acc,#95e04a);color:var(--acc-ink,#0b1208);font-size:19px;cursor:pointer}
#sp-chat .send:disabled{opacity:.4}
@media (max-width:720px){
  #sp-chat-fab{right:12px;bottom:calc(140px + env(safe-area-inset-bottom,0px));width:50px;height:50px;font-size:22px}
  #sp-chat{inset:0;width:auto;height:auto;height:100dvh;border-radius:0;border:0}
  #sp-chat .ch-h{padding-top:calc(12px + env(safe-area-inset-top,0px))}
  #sp-chat .ch-f{padding-bottom:calc(10px + env(safe-area-inset-bottom,0px))}
  #sp-chat .m{max-width:92%}
}
</style>
<button id="sp-chat-fab" onclick="spChat.toggle()" aria-label="Discuter avec Claude" title="Discuter avec Claude">💬<span class="dot"></span></button>
<div id="sp-chat" role="dialog" aria-label="Discussion avec Claude">
  <div class="ch-h"><div class="sp"><b>Claude</b><small>lit les données du site · réponse en ~1 min</small></div>
    <button onclick="spChat.reset()" title="Repartir de zéro">Nouvelle</button><button class="x" onclick="spChat.toggle(false)" aria-label="Fermer">✕</button></div>
  <div class="ch-b" id="sp-chat-b"></div>
  <form class="ch-f" onsubmit="spChat.send();return false">
    <textarea id="sp-chat-in" rows="1" placeholder="Pose ta question…" maxlength="4000"
      onkeydown="if(event.key==='Enter'&&!event.shiftKey&&window.innerWidth>720){event.preventDefault();spChat.send();}"
      oninput="this.style.height='auto';this.style.height=Math.min(this.scrollHeight,140)+'px'"></textarea>
    <button class="send" type="submit" id="sp-chat-go" aria-label="Envoyer">➤</button>
  </form>
</div>
<script>
window.spChat = (function(){
  var docs = [], unsub = null, uid = null, open = false, seen = 0;
  var SUGG = ['Résume-moi la soirée NBA de cette nuit', 'Comment va la Bankroll IA ?', 'Quels sont les meilleurs picks de ce soir et pourquoi ?'];
  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function md(s){
    var out = [], list = false, tbl = null;
    var flush = function(){ if(tbl){ out.push('<div class="tw"><table>' + tbl.join('') + '</table></div>'); tbl = null; } };
    esc(s).split('\n').forEach(function(l){
      if(/^\s*\|.*\|\s*$/.test(l)){            // tableau markdown -> vrai tableau (defile sur mobile)
        if(/^\s*\|[\s:|-]+\|\s*$/.test(l)) return;
        var cells = l.trim().slice(1, -1).split('|').map(function(c){ return c.trim().replace(/\*\*(.+?)\*\*/g, '<b>$1</b>'); });
        if(list){ out.push('</ul>'); list = false; }
        if(!tbl) tbl = ['<tr>' + cells.map(function(c){ return '<th>' + c + '</th>'; }).join('') + '</tr>'];
        else tbl.push('<tr>' + cells.map(function(c){ return '<td>' + c + '</td>'; }).join('') + '</tr>');
        return;
      }
      flush();
      l = l.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');
      var li = l.match(/^\s*(?:[-•*]|\d+[.)])\s+(.*)/);
      if(li){ if(!list){ out.push('<ul>'); list = true; } out.push('<li>' + li[1] + '</li>'); return; }
      if(list){ out.push('</ul>'); list = false; }
      var h = l.match(/^#{1,4}\s+(.*)/);
      out.push(h ? '<h4>' + h[1] + '</h4>' : (l.trim() ? l + '<br>' : '<br>'));
    });
    flush();
    if(list) out.push('</ul>');
    return out.join('').replace(/(<br>)+$/, '');
  }
  function ms(d){ return d.at && d.at.toMillis ? d.at.toMillis() : Date.now(); }
  // force : aller en bas (ouverture, envoi). Sinon on ne descend que si la vue etait deja en bas : Wilson peut
  // remonter lire l'historique sans etre ramene en bas par le rafraichissement (05/11/2026).
  function render(force){
    var b = document.getElementById('sp-chat-b'); if(!b) return;
    var atBottom = b.scrollHeight - b.scrollTop - b.clientHeight < 80, keep = b.scrollTop;
    if(!uid){ b.innerHTML = '<div class="empty">Connecte-toi au site pour discuter avec Claude.</div>'; return; }
    var cut = -1; docs.forEach(function(d, i){ if(d.role === 'reset') cut = i; });
    var cur = docs.slice(cut + 1), h = [];
    if(!cur.length){
      h.push('<div class="empty">Pose une question sur les picks, les simulations, la bankroll ou le site : Claude lit les données à jour du dépôt.'
        + SUGG.map(function(s){ return '<button type="button" onclick="spChat.ask(this.textContent)">' + esc(s) + '</button>'; }).join('') + '</div>');
    }
    cur.forEach(function(d){
      if(d.role === 'user') h.push('<div class="m u">' + esc(d.text || '') + '</div>');
      else if(d.role === 'assistant') h.push('<div class="m a' + (/^Erreur du relais|^Limite de/.test(d.text || '') ? ' err' : '') + '">' + md(d.text || '') + '</div>');
    });
    var last = cur.filter(function(d){ return d.role === 'user'; }).pop();
    if(last && (last.status === 'pending' || last.status === 'thinking')){
      var late = last.status === 'pending' && Date.now() - ms(last) > 3 * 60 * 1000;
      h.push('<div class="wait"><i></i><i></i><i></i>' + (late ? 'Le relais ne répond pas encore (il redémarre peut-être) : ton message sera traité à son retour.'
        : last.status === 'thinking' ? 'Claude réfléchit…' : 'Message transmis…') + '</div>');
    }
    b.innerHTML = h.join('');
    b.scrollTop = (force || atBottom) ? b.scrollHeight : keep;
  }
  function listen(){
    if(unsub || !uid || !window._fb || !window._fb.chatListen) return;
    unsub = window._fb.chatListen(uid, function(snap){
      docs = snap.docs.map(function(x){ var d = x.data({serverTimestamps: 'estimate'}); d.id = x.id; return d; });
      var n = docs.filter(function(d){ return d.role === 'assistant'; }).length;
      if(!open && seen && n > seen) document.getElementById('sp-chat-fab').classList.add('unread');
      if(open || !seen) seen = n;
      render(window._spChatForce); window._spChatForce = false;
    }, function(){ unsub = null; });
  }
  // la connexion Firebase arrive en asynchrone : on attend l'utilisateur
  setInterval(function(){
    var u = window._fbUser || null, id = u ? u.uid : null;
    if(id !== uid){ if(unsub){ unsub(); unsub = null; } uid = id; docs = []; seen = 0; render(); }
    listen();
  }, 1500);
  setInterval(function(){ if(open) render(); }, 30000);
  return {
    toggle: function(v){
      open = v === undefined ? !open : v;
      document.getElementById('sp-chat').classList.toggle('open', open);
      if(open){
        document.getElementById('sp-chat-fab').classList.remove('unread');
        seen = docs.filter(function(d){ return d.role === 'assistant'; }).length;
        render(true);
        if(window.innerWidth > 720) setTimeout(function(){ document.getElementById('sp-chat-in').focus(); }, 50);
      }
    },
    send: function(){
      var t = document.getElementById('sp-chat-in'), txt = (t.value || '').trim();
      if(!txt || !uid || !window._fb) return;
      t.value = ''; t.style.height = 'auto'; window._spChatForce = true;
      var ctx = (window._spMain || '') + ' / ' + (window._spSub || '');
      window._fb.chatSend(uid, {role: 'user', text: txt.slice(0, 4000), status: 'pending', ctx: ctx})
        .catch(function(e){ alert('Envoi impossible : ' + (e && e.code || e)); t.value = txt; });
    },
    ask: function(q){ document.getElementById('sp-chat-in').value = q; this.send(); },
    reset: function(){ if(uid && window._fb) window._fb.chatSend(uid, {role: 'reset', text: '', status: 'done'}); }
  };
})();
</script>
"""


def inject(html):
    """Ajoute le chat juste avant le dernier </body> de la page."""
    k = html.rfind("</body>")
    return html[:k] + HTML + html[k:] if k >= 0 else html
