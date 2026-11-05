"""
site_access.py — Validation manuelle des inscriptions (demande de Wilson, 05/11/2026, boite a idees).

Un nouveau compte cree sa demande access/<uid> {email, status: "pending"} ; tant qu'elle n'est pas validee, le site
affiche « compte en attente » et les donnees Firebase (users/<uid>/**) restent fermees par les regles Firestore.
Le relais du chat (chat_bot.access_cycle) previent Wilson sur Telegram ; Wilson valide / refuse depuis le site
(menu compte › Inscriptions, visible pour role « admin », role pose uniquement par le relais).
Rappel : les pages du site sont des fichiers publics (GitHub Pages) ; ce controle protege les donnees Firebase
(bankroll perso, chat), l'ecran d'attente n'est qu'un confort d'affichage.
"""

CSS = """<style>
#auth-gate #sp-acc-wait{display:none;text-align:center;padding:8px 4px 2px}
#auth-gate.sp-wait #sp-acc-wait{display:block}
#auth-gate.sp-wait .bk-auth-tabs,#auth-gate.sp-wait #bk-gate-form,#auth-gate.sp-wait .gate-loading{display:none!important}
#sp-acc-wait .sp-acc-ic{font-size:34px;margin-bottom:8px}
#sp-acc-wait h3{font-size:18px;margin-bottom:8px}
#sp-acc-wait p{color:#9ca3af;font-size:14px;line-height:1.5;margin-bottom:14px}
.sp-acc-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 0;border-bottom:1px solid rgba(255,255,255,.06)}
.sp-acc-row b{display:block;font-size:14px;word-break:break-all}
.sp-acc-row small{color:#8B8D98;font-size:12px}
.sp-acc-row.pending small{color:#fbbf24}
.sp-acc-row.refused b{color:#8B8D98}
.sp-acc-btns{display:flex;gap:6px;flex-shrink:0}
.sp-acc-btns button{border:0;border-radius:10px;padding:7px 11px;font:600 13px inherit;cursor:pointer}
.sp-acc-ok{background:#16a34a;color:#fff}
.sp-acc-no{background:rgba(248,113,113,.14);color:#fca5a5}
.sp-acc-tag{font-size:12px;color:#8B8D98}
</style>"""

JS = r"""<script>
(function(){
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function gate(){ return document.getElementById('auth-gate'); }
  function wait(status){
    var g = gate(); if(!g) return;
    // l'indicateur « deja valide » ferait afficher le site avant le controle au prochain chargement
    try { localStorage.removeItem('sp_access_ok'); } catch(e){}
    document.documentElement.classList.remove('bk-prelogged');
    g.classList.add('ready'); g.classList.remove('hidden'); g.classList.add('sp-wait');
    var w = document.getElementById('sp-acc-wait');
    if(!w){ w = document.createElement('div'); w.id = 'sp-acc-wait'; (g.querySelector('.gate-card') || g).appendChild(w); }
    w.innerHTML = (status === 'refused'
      ? '<div class="sp-acc-ic">⛔</div><h3>Accès refusé</h3><p>Ta demande d\'accès à SportsPicks n\'a pas été acceptée.</p>'
      : '<div class="sp-acc-ic">⏳</div><h3>Compte en attente de validation</h3><p>Ton inscription est bien enregistrée. '
        + 'Chaque compte est validé à la main : cette page s\'ouvrira toute seule dès que ce sera fait.</p>')
      + '<button type="button" class="bk-auth-link" onclick="_bkSignOut()">Se déconnecter</button>';
  }
  function unwait(){ var g = gate(); if(g) g.classList.remove('sp-wait'); }

  // Appele par _bkOnAuthChanged avant de charger les donnees : true = acces ouvert.
  window._spAccessOk = async function(user){
    if(!window._fb || !window._fb.accessGet) return true;
    var d;
    try {
      var s = await window._fb.accessGet(user.uid);
      d = s.exists() ? s.data() : null;
      if(!d){ await window._fb.accessRequest(user.uid, user.email || ''); d = {status: 'pending'}; }
    } catch(e){
      // regles Firestore pas encore publiees (permission-denied) ou reseau : on ne bloque pas l'affichage,
      // le vrai controle des donnees est dans les regles
      console.warn('[acces] controle indisponible', e && e.code);
      return true;
    }
    window._spAccess = d;
    if(d.status === 'approved'){ unwait(); adminMenu(d); return true; }
    wait(d.status);
    if(!window._spAccUnsub) window._spAccUnsub = window._fb.accessListen(user.uid, function(x){
      var v = x.data(); if(!v) return;
      if(v.status === 'approved'){
        if(window._spAccUnsub){ window._spAccUnsub(); window._spAccUnsub = null; }
        unwait(); _bkOnAuthChanged(user);
      } else wait(v.status);
    }, function(){});
    return false;
  };
  window._spAccessReset = function(){
    if(window._spAccUnsub){ window._spAccUnsub(); window._spAccUnsub = null; }
    window._spAccess = null; unwait();
    var b = document.getElementById('sp-acc-item'); if(b) b.remove();
  };

  function adminMenu(d){
    if(d.role !== 'admin') return;
    var dd = document.querySelector('#global-account-menu .acct-dropdown');
    if(!dd) return;
    if(!document.getElementById('sp-acc-item')){
      var b = document.createElement('button');
      b.type = 'button'; b.id = 'sp-acc-item'; b.className = 'acct-dd-item';
      b.innerHTML = '<span class="ic">👥</span><span>Inscriptions <b id="sp-acc-n"></b></span>';
      b.onclick = function(){ _bkCloseAccountMenu(); spAccess.open(); };
      dd.insertBefore(b, dd.querySelector('.acct-dd-item.danger'));
      if(location.hash === '#acces') setTimeout(function(){ spAccess.open(); }, 400);   // lien du message Telegram
    }
    count();
  }
  async function list(){
    var s = await window._fb.accessAll();
    return s.docs.map(function(x){ var v = x.data(); v.uid = x.id; return v; });
  }
  async function count(){
    try {
      var n = (await list()).filter(function(v){ return v.status === 'pending'; }).length;
      var el = document.getElementById('sp-acc-n'); if(el) el.textContent = n ? '(' + n + ')' : '';
    } catch(e){}
  }
  function when(v){
    var t = v.at && v.at.toDate ? v.at.toDate() : null;
    return t ? t.toLocaleString('fr-FR', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}) : '';
  }
  var LAB = {pending: 'En attente', approved: 'Validé', refused: 'Refusé'}, ORDER = {pending: 0, approved: 1, refused: 2};

  window.spAccess = {
    open: function(){
      var root = _bkEnsureModalRoot();
      root.innerHTML = '<div class="bk-modal-bd" onclick="_bkCloseForm()"></div><div class="bk-modal-card" role="dialog" aria-modal="true">'
        + '<div class="bk-m-hd"><button class="bk-m-cancel" onclick="_bkCloseForm()">Fermer</button><div class="bk-m-title">Inscriptions</div>'
        + '<div style="width:64px"></div></div><div class="bk-m-body" id="sp-acc-body">Chargement…</div></div>';
      setTimeout(function(){ root.classList.add('open'); }, 10);
      window._bkFormEscHandler = function(e){ if(e.key === 'Escape') _bkCloseForm(); };
      document.addEventListener('keydown', window._bkFormEscHandler);
      this.render();
    },
    render: async function(){
      var body = document.getElementById('sp-acc-body'); if(!body) return;
      var L;
      try { L = await list(); } catch(e){ body.textContent = 'Lecture impossible (' + (e && e.code || e) + ').'; return; }
      var me = window._fbUser && window._fbUser.uid;
      L.sort(function(a, b){ return (ORDER[a.status] - ORDER[b.status]) || ((b.at && b.at.seconds || 0) - (a.at && a.at.seconds || 0)); });
      body.innerHTML = L.length ? L.map(function(v){
        var id = String(v.uid).replace(/[^\w-]/g, ''), st = LAB[v.status] ? v.status : 'pending';
        var btns = (v.uid === me || v.role === 'admin') ? '<span class="sp-acc-tag">administrateur</span>'
          : (st !== 'approved' ? '<button class="sp-acc-ok" onclick="spAccess.decide(\'' + id + '\',\'approved\')">Valider</button>' : '')
          + (st !== 'refused' ? '<button class="sp-acc-no" onclick="spAccess.decide(\'' + id + '\',\'refused\')">'
             + (st === 'approved' ? 'Retirer' : 'Refuser') + '</button>' : '');
        return '<div class="sp-acc-row ' + st + '"><div><b>' + esc(v.email || '?') + '</b><small>' + LAB[st]
          + (when(v) ? ' · demandé le ' + when(v) : '') + '</small></div><div class="sp-acc-btns">' + btns + '</div></div>';
      }).join('') : '<p>Aucune demande pour l\'instant.</p>';
    },
    decide: async function(uid, status){
      if(status === 'refused' && !confirm('Confirmer ? Ce compte n\'aura plus accès à ses données sur le site.')) return;
      try { await window._fb.accessDecide(uid, status, window._fbUser && window._fbUser.uid); }
      catch(e){ alert('Impossible : ' + (e && e.code || e)); }
      this.render(); count();
    }
  };
})();
</script>"""

HTML = CSS + JS


def inject(html):
    """Ajoute le controle d'acces juste avant le dernier </body> (avant le module Firebase, qui est differe)."""
    k = html.rfind("</body>")
    return html[:k] + HTML + html[k:] if k >= 0 else html
