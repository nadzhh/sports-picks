/* foot_replay_player.js — Lecteur du « match type » foot simule (canvas 1280x720).
 * Donnees : replay produit par foot_sim_run.py (evenements : tirs avec position reelle et xG, buts,
 * penalties, csc, cartons rouges, remplacements). Les deux equipes attaquent vers la droite dans les
 * donnees FotMob : l'equipe a l'exterieur est retournee (elle attaque vers la gauche).
 */
(function () {
  if (window.SPFoot) return;
  var W = 1280, H = 720, PX = 120, PY = 130, PW = 1040, PH = 520;   // terrain dans le canvas
  var C = { bg: '#0a0d0b', grass: '#0f1a12', grass2: '#122016', line: 'rgba(238,243,239,0.55)', tx: '#eef3ef',
            mu: '#9aa89e', home: '#95e04a', away: '#f5b942', loss: '#ef4444', panel: 'rgba(15,20,17,0.92)' };
  var SANS = "'Space Grotesk','Segoe UI',sans-serif", MONO = "'JetBrains Mono',Consolas,monospace";
  var KIND = { but: 'BUT !', arret: 'Arrêt du gardien', rate: 'À côté', contre: 'Contré', csc: 'But contre son camp',
               rouge: 'Carton rouge', remplacement: 'Remplacement' };
  var SIT = { RegularPlay: 'dans le jeu', FastBreak: 'contre-attaque', SetPiece: 'coup de pied arrêté', FromCorner: 'sur corner',
              Penalty: 'penalty', FreeKick: 'coup franc direct', ThrowInSetPiece: 'sur touche' };
  var TYP = { RightFoot: 'pied droit', LeftFoot: 'pied gauche', Header: 'de la tête' };

  function px(x, y, side) {           // coordonnees FotMob (105 x 68, attaque vers x=105) -> canvas
    if (side === 'away') { x = 105 - x; y = 68 - y; }
    return [PX + x / 105 * PW, PY + y / 68 * PH];
  }
  function ease(t) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }

  function pitch(g) {
    g.fillStyle = C.grass; g.fillRect(PX, PY, PW, PH);
    for (var i = 0; i < 10; i += 2) { g.fillStyle = C.grass2; g.fillRect(PX + i * PW / 10, PY, PW / 10, PH); }
    g.strokeStyle = C.line; g.lineWidth = 2.5;
    g.strokeRect(PX, PY, PW, PH);
    g.beginPath(); g.moveTo(PX + PW / 2, PY); g.lineTo(PX + PW / 2, PY + PH); g.stroke();
    g.beginPath(); g.arc(PX + PW / 2, PY + PH / 2, 9.15 / 105 * PW, 0, 7); g.stroke();
    [0, 1].forEach(function (s) {
      var x0 = s ? PX + PW : PX, d = s ? -1 : 1;
      var bw = 16.5 / 105 * PW, bh = 40.3 / 68 * PH, sw = 5.5 / 105 * PW, sh = 18.3 / 68 * PH, gh = 7.32 / 68 * PH;
      g.strokeRect(s ? x0 - bw : x0, PY + (PH - bh) / 2, bw, bh);
      g.strokeRect(s ? x0 - sw : x0, PY + (PH - sh) / 2, sw, sh);
      g.fillStyle = C.line; g.beginPath(); g.arc(x0 + d * 11 / 105 * PW, PY + PH / 2, 3, 0, 7); g.fill();
      g.lineWidth = 5; g.strokeStyle = C.tx; g.beginPath(); g.moveTo(x0, PY + (PH - gh) / 2); g.lineTo(x0, PY + (PH + gh) / 2); g.stroke();
      g.lineWidth = 2.5; g.strokeStyle = C.line;
    });
  }

  function Player(host, data, title) {
    this.d = data; this.title = title || {};
    host.innerHTML = '<canvas class="sp-foot-cv" width="' + W + '" height="' + H + '"></canvas>' +
      '<div class="sp-foot-bar"><button data-a="play">▶ Lecture</button><button data-a="restart">↺</button>' +
      '<button data-a="speed">x1</button><span class="sp-foot-hint"></span></div>';
    this.cv = host.querySelector('canvas'); this.g = this.cv.getContext('2d');
    var me = this;
    host.querySelector('[data-a=play]').onclick = function () { me.toggle(this); };
    host.querySelector('[data-a=restart]').onclick = function () { me.t = 0; me.draw(); };
    host.querySelector('[data-a=speed]').onclick = function () { me.speed = me.speed === 1 ? 2 : (me.speed === 2 ? 4 : 1); this.textContent = 'x' + me.speed; };
    this.speed = 1; this.t = 0; this.timeline(); this.draw();
  }
  // Ligne de temps : 0,55 s par minute de jeu sans action, 4,5 s par action (2,5 s pour un remplacement)
  Player.prototype.timeline = function () {
    var ev = this.d.events, segs = [], t = 0, last = 0;
    ev.forEach(function (e) {
      var gap = Math.max(e.t - last, 0); segs.push({ kind: 'run', t0: t, t1: t + gap * 0.18, m0: last, m1: e.t }); t += gap * 0.18;
      var dur = e.kind === 'remplacement' ? 2.2 : (e.kind === 'rouge' ? 2.8 : (e.kind === 'but' || e.kind === 'csc' ? 5.0 : 3.6));
      segs.push({ kind: 'ev', t0: t, t1: t + dur, e: e, m0: e.t, m1: e.t }); t += dur; last = e.t;
    });
    var end = Math.max(last, 90);
    segs.push({ kind: 'run', t0: t, t1: t + (end - last) * 0.18 + 0.5, m0: last, m1: end }); t += (end - last) * 0.18 + 0.5;
    segs.push({ kind: 'end', t0: t, t1: t + 4, m0: end, m1: end }); t += 4;
    this.segs = segs; this.T = t;
  };
  Player.prototype.toggle = function (btn) {
    var me = this;
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; btn.textContent = '▶ Lecture'; return; }
    if (this.t >= this.T) this.t = 0;
    btn.textContent = '❚❚ Pause'; var prev = performance.now();
    var step = function (now) {
      me.t += (now - prev) / 1000 * me.speed; prev = now; me.draw();
      if (me.t < me.T) me.raf = requestAnimationFrame(step);
      else { me.raf = null; btn.textContent = '▶ Revoir'; }
    };
    this.raf = requestAnimationFrame(step);
  };
  Player.prototype.state = function () {
    var seg = this.segs[this.segs.length - 1], i;
    for (i = 0; i < this.segs.length; i++) if (this.t < this.segs[i].t1) { seg = this.segs[i]; break; }
    var f = Math.min(Math.max((this.t - seg.t0) / Math.max(seg.t1 - seg.t0, 1e-6), 0), 1);
    var score = [0, 0], xg = [0, 0], reds = [0, 0], me = this;
    this.d.events.forEach(function (e) {
      var done = e.t < seg.m0 || (e.t === seg.m0 && seg.kind !== 'ev') || (seg.kind === 'ev' && e === seg.e && f > 0.62) ||
                 (seg.kind === 'ev' && e.t === seg.m0 && me.d.events.indexOf(e) < me.d.events.indexOf(seg.e));
      if (seg.kind === 'end') done = true;
      if (!done) return;
      var s = e.side === 'home' ? 0 : 1;
      if (e.kind === 'but' || e.kind === 'csc') score[s]++;
      if (e.xg) xg[s] += e.xg;
      if (e.kind === 'rouge') reds[s]++;
    });
    return { seg: seg, f: f, score: score, xg: xg, reds: reds, minute: Math.round(seg.m0 + (seg.m1 - seg.m0) * f) };
  };
  Player.prototype.draw = function () {
    var g = this.g, st = this.state(), seg = st.seg, f = st.f, ti = this.title;
    g.fillStyle = C.bg; g.fillRect(0, 0, W, H);
    pitch(g);
    // Bandeau du score
    g.fillStyle = C.panel; g.fillRect(PX, 28, PW, 74);
    g.font = '600 30px ' + SANS; g.textBaseline = 'middle';
    g.fillStyle = C.home; g.textAlign = 'right'; g.fillText(ti.home || 'Domicile', PX + PW / 2 - 90, 65);
    g.fillStyle = C.away; g.textAlign = 'left'; g.fillText(ti.away || 'Extérieur', PX + PW / 2 + 90, 65);
    g.fillStyle = C.tx; g.textAlign = 'center'; g.font = '700 40px ' + MONO; g.fillText(st.score[0] + ' - ' + st.score[1], PX + PW / 2, 65);
    g.font = '500 20px ' + MONO; g.fillStyle = C.mu; g.textAlign = 'left';
    g.fillText(seg.kind === 'end' ? 'Fin du match' : st.minute + "'", PX + 18, 65);
    g.textAlign = 'right'; g.fillText('xG ' + st.xg[0].toFixed(2) + ' - ' + st.xg[1].toFixed(2), PX + PW - 18, 65);
    [0, 1].forEach(function (s) { for (var r = 0; r < st.reds[s]; r++) { g.fillStyle = C.loss; g.fillRect(s ? PX + PW / 2 + 60 + r * 14 : PX + PW / 2 - 70 - r * 14, 50, 10, 30); } });
    // Action en cours
    var cap = '';
    if (seg.kind === 'ev') {
      var e = seg.e, col = e.side === 'home' ? C.home : C.away;
      if (e.x !== undefined && e.kind !== 'rouge' && e.kind !== 'remplacement') {
        var to = px(e.x, e.y, e.side), from = px(e.sit === 'Penalty' ? 94.5 : 60 + (e.y % 7), e.sit === 'Penalty' ? 34 : 34 + (e.x % 9) - 4.5, e.side);
        var goalX = e.side === 'home' ? PX + PW : PX, gy = PY + PH / 2 + ((e.y - 34) / 34) * (e.kind === 'rate' ? 70 : 22);
        var bx, by, p1 = Math.min(f / 0.4, 1), p2 = Math.min(Math.max((f - 0.4) / 0.22, 0), 1);
        bx = from[0] + (to[0] - from[0]) * ease(p1); by = from[1] + (to[1] - from[1]) * ease(p1);
        if (p2 > 0) {
          var stop = e.kind === 'contre' ? 0.25 : (e.kind === 'arret' ? 0.9 : 1.04);
          bx = to[0] + (goalX - to[0]) * stop * ease(p2); by = to[1] + (gy - to[1]) * stop * ease(p2);
        }
        g.strokeStyle = col; g.globalAlpha = 0.35; g.setLineDash([6, 6]); g.lineWidth = 2;
        g.beginPath(); g.moveTo(from[0], from[1]); g.lineTo(to[0], to[1]); if (p2 > 0) g.lineTo(bx, by); g.stroke(); g.setLineDash([]); g.globalAlpha = 1;
        g.fillStyle = col; g.beginPath(); g.arc(to[0], to[1], 7 + 30 * (e.xg || 0), 0, 7); g.globalAlpha = 0.25; g.fill(); g.globalAlpha = 1;
        g.fillStyle = '#fff'; g.beginPath(); g.arc(bx, by, 7, 0, 7); g.fill();
        if ((e.kind === 'but' || e.kind === 'csc') && f > 0.62) {
          g.fillStyle = col; g.font = '700 ' + Math.round(70 + 20 * Math.sin(f * 20)) + 'px ' + SANS; g.textAlign = 'center';
          g.fillText(e.kind === 'csc' ? 'BUT CSC !' : 'BUT !', PX + PW / 2, PY + PH / 2);
        }
      }
      if (e.kind === 'rouge') { g.fillStyle = C.loss; g.fillRect(PX + PW / 2 - 25, PY + PH / 2 - 40, 50, 80); }
      cap = e.t + "'  " + (e.kind === 'remplacement' ? '↔ ' + e.who + ' remplace ' + e.out :
            e.kind === 'rouge' ? 'Carton rouge' : e.kind === 'csc' ? 'But contre son camp' :
            (e.who || '') + (e.sit ? ' · ' + (SIT[e.sit] || '') : '') + (e.type && TYP[e.type] ? ', ' + TYP[e.type] : '') +
            (e.xg ? ' · xG ' + e.xg.toFixed(2).replace('.', ',') : '') + ' · ' + KIND[e.kind]);
      cap = { t: cap, c: col };
    }
    if (seg.kind === 'end') {
      g.fillStyle = C.panel; g.fillRect(PX + PW / 2 - 330, PY + PH / 2 - 90, 660, 180);
      g.fillStyle = C.tx; g.textAlign = 'center'; g.font = '700 46px ' + SANS;
      g.fillText('Score final ' + st.score[0] + ' - ' + st.score[1], PX + PW / 2, PY + PH / 2 - 25);
      g.font = '500 20px ' + MONO; g.fillStyle = C.mu;
      g.fillText(ti.foot || 'Le match type parmi 10 000 simulations', PX + PW / 2, PY + PH / 2 + 35);
    }
    g.fillStyle = C.panel; g.fillRect(PX, PY + PH + 18, PW, 56);
    if (cap) { g.fillStyle = cap.c; g.font = '500 24px ' + SANS; g.textAlign = 'left'; g.fillText(cap.t, PX + 18, PY + PH + 46); }
  };
  window.SPFoot = { open: function (host, data, title) { return new Player(host, data, title); } };
})();
