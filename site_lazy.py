"""
site_lazy.py — Allege index.html (demande de Wilson, 05/11/2026 : « améliorer le frontend », point 1).

Mesure du 05/11/2026 (page en ligne, telephone simule = processeur ralenti x4, sans Firebase) :
    index.html 27,8 Mo, affichage en 8,4 s (mediane de 3)
    dont Basket › Analyse 16,8 Mo (2 043 fiches joueurs pre-rendues)          -> sans elle : 2,3 s
    dont anciennes sections que le menu n'affiche plus (foot, historiques)   -> sans tout : 1,3 s

Passe APRES optimize_html (les classes courtes s1, s2... doivent etre les memes dans la page et le fragment) :
  1. le contenu de Basket › Analyse part dans lazy/nba_analyse.html, charge au premier affichage de l'onglet
     (ou par goToAnalyse) ; la page garde un indicateur « Chargement… » ;
  2. les anciennes sections (deja inertes dans des <template>) sont videes : le menu ne les affiche plus depuis
     la refonte Simulation / Picks / Historique, leur bloc id reste pour l'ancien JS.
Le fragment porte un tampon de version (?v=) : la page et le fragment d'une meme generation vont ensemble.
En cas d'echec du chargement, un bouton « Réessayer » s'affiche (la page ne casse pas).
"""
import hashlib
import os
import re

LAZY = {"sport-analyse": "lazy/nba_analyse.html"}
EMPTY = ("sport-football", "sport-foothist", "sport-nbahist")

SCRIPT = r"""<script>
(function(){
  if(window.spLazy) return;
  function load(box, cb){
    var st = box.getAttribute('data-st');
    if(st === 'ok'){ if(cb) cb(); return; }
    (box._cb = box._cb || []).push(cb);
    if(st === 'run') return;
    box.setAttribute('data-st', 'run');
    box.innerHTML = '<div class="sp-lazy-msg">Chargement…</div>';
    fetch(box.getAttribute('data-src'), {cache: 'force-cache'}).then(function(r){ if(!r.ok) throw r.status; return r.text(); })
      .then(function(h){
        var host = box.parentNode, tmp = document.createElement('div'); tmp.innerHTML = h;
        while(tmp.firstChild) host.insertBefore(tmp.firstChild, box);
        box.setAttribute('data-st', 'ok'); box.style.display = 'none';
        var L = box._cb; box._cb = []; L.forEach(function(f){ if(f) try { f(); } catch(e){} });
      })
      .catch(function(){
        box.setAttribute('data-st', 'ko');
        box.innerHTML = '<div class="sp-lazy-msg">Chargement impossible. <button onclick="spLazy.load(this.closest(\'.sp-lazy\'))">Réessayer</button></div>';
      });
  }
  function visible(){   // charge les blocs des sections affichees
    document.querySelectorAll('.sp-lazy:not([data-st])').forEach(function(b){ if(b.offsetParent !== null) load(b); });
  }
  window.spLazy = {load: load, visible: visible,
    section: function(id, cb){ var b = document.querySelector('#' + id + ' > .sp-lazy'); if(b) load(b, cb); else if(cb) cb(); }};
  if(typeof showSport === 'function'){
    var _show = showSport;
    window.showSport = showSport = function(s){ var r = _show.apply(this, arguments); setTimeout(visible, 0); return r; };
  }
  if(typeof goToAnalyse === 'function'){
    var _go = goToAnalyse;
    window.goToAnalyse = goToAnalyse = function(){ var a = arguments, t = this;
      showSport('analyse'); spLazy.section('sport-analyse', function(){ _go.apply(t, a); }); };
  }
  document.addEventListener('DOMContentLoaded', function(){ setTimeout(visible, 0); });
})();
</script>
<style>.sp-lazy-msg{padding:40px 16px;text-align:center;color:var(--mu,#94a3b8);font-size:14px}
.sp-lazy-msg button{margin-left:8px;border:1px solid var(--line2,#334155);background:var(--chip,#1e293b);color:inherit;border-radius:8px;padding:6px 12px;cursor:pointer}</style>"""


def _inner(html, sid):
    """(debut, fin) du contenu du <div id="sid"> (div imbriques comptes), ou None."""
    start = html.find(f'<div id="{sid}"')
    if start < 0: return None
    open_end = html.index(">", start) + 1
    depth = 1
    for m in re.finditer(r"<(/?)div\b", html[open_end:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return open_end, open_end + m.start()
    return None


def split(path="index.html"):
    html = open(path, encoding="utf-8").read()
    before = len(html.encode("utf-8"))
    for sid in EMPTY:
        r = _inner(html, sid)
        if r: html = html[:r[0]] + html[r[1]:]
    for sid, frag in LAZY.items():
        r = _inner(html, sid)
        if not r: continue
        body = html[r[0]:r[1]]
        if "<script" in body:          # le fragment est insere sans executer de script : on ne decoupe pas
            print(f"  ⚠️ {sid} contient un script : laisse dans la page")
            continue
        os.makedirs(os.path.dirname(frag), exist_ok=True)
        with open(frag, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        v = hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]
        html = html[:r[0]] + f'<div class="sp-lazy" data-src="{frag}?v={v}"></div>' + html[r[1]:]
    k = html.rfind("</body>")
    html = html[:k] + SCRIPT + html[k:]
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    after = len(html.encode("utf-8"))
    print(f"⚡ index.html allege (chargement a la demande) — {before / 1048576:.1f} Mo → {after / 1048576:.1f} Mo")
    return before, after
