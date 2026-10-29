/* foot_replay_player.js — Lecteur du « match type » foot simule (canvas 1280x720).
 * Donnees : replay produit par foot_sim_run.py (evenements : tirs avec position reelle et xG, buts,
 * penalties, csc, cartons rouges, remplacements). Les deux equipes attaquent vers la droite dans les
 * donnees FotMob : l'equipe a l'exterieur est retournee (elle attaque vers la gauche).
 */
(function () {
  if (window.SPFoot) return;
  var W = 1280, H = 720, PX = 120, PY = 130, PW = 1040, PH = 520;   // terrain dans le canvas
  var C = { di: '#6b7a70', bg: '#0a0d0b', grass: '#0f1a12', grass2: '#122016', line: 'rgba(238,243,239,0.55)', tx: '#eef3ef',
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
    host.querySelector('[data-a=speed]').onclick = function () {
      var SP = [1, 1.25, 1.5, 2, 4]; me.speed = SP[(SP.indexOf(me.speed) + 1) % SP.length]; this.textContent = 'x' + String(me.speed).replace('.', ',');
    };
    this.speed = 1; this.t = 0;
    if (data.teams) { this.S = prepareFull(data); this.T = this.S.T; } else this.timeline();   // v2 : les 22 joueurs
    this.draw();
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
    if (this.S) return drawFull(this);
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
  /* ── Match complet : les 22 joueurs en mouvement (donnees v2 : teams, poss) ─────────────────────────
   * Le moteur ne simule que les tirs (minute, position reelle, xG, issue) : le jeu entre deux tirs est
   * reconstitue — possessions selon la part de balle, passes de joueur a joueur, blocs qui montent / se
   * replient / coulissent vers le ballon, pressing du plus proche. Chaque tir simule est joue par son
   * tireur, depuis la position reelle de ce tir. Trajets image par image a vitesse maximale commune. */
  var F_SPEED = 11, F_FS = 20, F_VP = 34, F_VB = 70;     // s de jeu par s de video ; images/s ; m/s (video) joueurs, ballon
  var LEN = 105, WID = 68;
  function mulberry(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; var t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  var DEF_LAY = { GK: [[0.1, 0.5]], DEF: [[0.3, 0.12], [0.3, 0.38], [0.3, 0.62], [0.3, 0.88], [0.3, 0.5]],
                  MID: [[0.52, 0.2], [0.52, 0.5], [0.52, 0.8], [0.62, 0.35], [0.62, 0.65]], ATT: [[0.84, 0.5], [0.8, 0.2], [0.8, 0.8]] };

  function prepareFull(d) {
    var rng = mulberry(7 + d.events.length * 131 + d.final[0] * 17 + d.final[1] * 29);
    var sides = ['home', 'away'], ALL = [], bySide = { home: [], away: [] };
    sides.forEach(function (s) {
      var used = { GK: 0, DEF: 0, MID: 0, ATT: 0 };
      d.teams[s].players.forEach(function (p) {
        var lay = p.lay;
        if (!lay && p.st) { var L = DEF_LAY[p.pos] || DEF_LAY.MID; lay = L[used[p.pos] % L.length]; used[p.pos]++; }
        var o = { k: ALL.length, side: s, name: p.name, last: (p.name || '').split(' ').slice(-1)[0], num: p.num || '', pos: p.pos, lay: lay, on: !!p.st };
        ALL.push(o); bySide[s].push(o);
      });
    });
    var byName = {}; ALL.forEach(function (o) { byName[o.side + '|' + o.name] = o; });
    // Chronologie des actions en secondes de jeu (tirs espaces d'au moins 25 s)
    var shots = [], other = [], last = -99;
    d.events.forEach(function (e) {
      if (e.kind === 'remplacement' || e.kind === 'rouge') { other.push({ ts: e.t * 60 + 20, e: e }); return; }
      var ts = Math.max(e.t * 60 + 5 + Math.floor(rng() * 45), last + 25); last = ts;
      shots.push({ ts: ts, e: e });
    });
    var endT = Math.max(93 * 60, last + 60);
    var pos = {}, tgt = {}, ball = [LEN / 2, WID / 2], owner = null, flight = null, frames = [], log = [];
    var score = [0, 0], possT = [0, 0], gameT = 0, half = 1, cur = null, si = 0, oi = 0, nextPass = 0, pause = 0, overlay = null, force = null;
    function attackRight(side) { return (side === 'home') === (half === 1); }
    function w(side, x, y) { return attackRight(side) ? [x, y] : [LEN - x, WID - y]; }         // repere equipe -> terrain
    function own(side, p) { return attackRight(side) ? [p[0], p[1]] : [LEN - p[0], WID - p[1]]; }  // terrain -> repere equipe
    function onPitch(side) { return bySide[side].filter(function (o) { return o.on; }); }
    function kickoff(side) {
      sides.forEach(function (s) {
        onPitch(s).forEach(function (o) {
          var dx = o.lay ? o.lay[0] : 0.5, wy = o.lay ? o.lay[1] : 0.5;
          var p = w(s, o.pos === 'GK' ? 4 : clamp(dx * 50, 6, 50), WID / 2 + (wy - 0.5) * WID * 0.8);
          if (!pos[o.k]) pos[o.k] = p; tgt[o.k] = p;
        });
      });
      ball = [LEN / 2, WID / 2];
      var c = onPitch(side).filter(function (o) { return o.pos === 'ATT' || o.pos === 'MID'; });
      owner = c.length ? c[0] : onPitch(side)[0]; flight = null; cur = { side: side, end: gameT + 8 + rng() * 10, shot: null };
      if (owner) { if (!pos[owner.k]) pos[owner.k] = w(side, LEN / 2 - 1, WID / 2); tgt[owner.k] = w(side, LEN / 2 - 1, WID / 2); }
      nextPass = gameT + 3;
    }
    function nearest(list, p) { var b = null, bd = 1e9; list.forEach(function (o) { var q = pos[o.k]; if (!q) return; var dd = Math.hypot(q[0] - p[0], q[1] - p[1]); if (dd < bd) { bd = dd; b = o; } }); return b; }
    function goalOf(side) { return w(side, LEN, WID / 2); }
    function pushLog(o) { o.f = frames.length; log.push(o); }
    onPitch('home').concat(onPitch('away')).forEach(function (o) { pos[o.k] = null; });
    kickoff('home');
    while (gameT < endT) {
      // Remplacements et rouges
      while (oi < other.length && other[oi].ts <= gameT) {
        var ev = other[oi++].e, s2 = ev.side;
        if (ev.kind === 'remplacement') {
          var oin = byName[s2 + '|' + ev.who], oout = byName[s2 + '|' + ev.out];
          if (oin && oout && oout.on) { oin.on = true; oin.lay = oout.lay; oout.on = false; pos[oin.k] = [LEN / 2 + (s2 === 'home' ? -6 : 6), WID + 1]; delete pos[oout.k];
            if (owner === oout) owner = oin;
            pushLog({ kind: 'sub', side: s2, txt: ev.t + "'  ↔ " + oin.name + ' remplace ' + oout.name, who: oin.k }); }
        } else {
          var cand = onPitch(s2).filter(function (o) { return o.pos === 'DEF' || o.pos === 'MID'; }), out = cand[Math.floor(rng() * cand.length)];
          if (out) { out.on = false; delete pos[out.k]; if (owner === out) owner = null; pushLog({ kind: 'rouge', side: s2, txt: ev.t + "'  Carton rouge : " + out.name, who: null }); pause = 1.6 * F_FS; overlay = { k: 'rouge', side: s2 }; }
        }
      }
      // Mi-temps : changement de cote
      if (half === 1 && gameT >= 45 * 60) {
        for (var h = 0; h < 2.5 * F_FS; h++) frames.push(snapshot({ k: 'mt' }));
        half = 2; pos = {}; kickoff('away'); pushLog({ kind: 'mt', txt: 'Mi-temps : ' + score[0] + ' - ' + score[1] });
      }
      // Possession : la prochaine action de tir impose l'equipe et la fin
      if (!cur) {
        var nx = shots[si];
        if (nx && nx.ts - gameT <= 26) cur = { side: nx.e.side, end: nx.ts, shot: nx };
        else {
          var side = force || (rng() < d.poss ? 'home' : 'away'), dur = 10 + rng() * 22; force = null;
          if (nx) dur = Math.min(dur, Math.max(4, nx.ts - gameT - 14));
          cur = { side: side, end: gameT + dur, shot: null };
        }
        cur.recycle = !!(owner && owner.side === cur.side && own(cur.side, ball)[0] > 60);   // meme equipe, haut : on repart de derriere
        if (!owner || owner.side !== cur.side) {   // recuperation : le plus proche du ballon
          var rec = nearest(onPitch(cur.side).filter(function (o) { return o.pos !== 'GK'; }), ball) || onPitch(cur.side)[0];
          flight = null; owner = rec;
        }
        nextPass = gameT + 1.5 + rng() * 2;
      }
      var A = cur.side, D = A === 'home' ? 'away' : 'home';
      // Fin de possession sans tir : ballon perdu
      if (!cur.shot && gameT >= cur.end && !flight) cur = null;
      // Tir
      var ready = false;
      if (cur && cur.shot && gameT >= cur.end && !flight) {
        var e0 = cur.shot.e, sh0 = byName[A + '|' + e0.who];
        if (!sh0 || !sh0.on) { sh0 = nearest(onPitch(A).filter(function (o) { return o.pos !== 'GK'; }), w(A, e0.x || 94, e0.y || 34)); if (sh0) e0.who = sh0.name; }
        var sp0 = w(A, e0.x || 94, e0.y || 34);
        ready = !sh0 || (owner === sh0 && pos[sh0.k] && Math.hypot(pos[sh0.k][0] - sp0[0], pos[sh0.k][1] - sp0[1]) < 3) || gameT > cur.end + 20;
      }
      if (ready) {
        var e = cur.shot.e, sh = byName[A + '|' + e.who];
        var spot = w(A, e.x || 94, e.y || 34), gl = goalOf(A), aim = [gl[0], gl[1] + ((e.y || 34) - 34) * (e.kind === 'rate' ? 0.6 : 0.12)];
        if (sh) owner = sh;
        ball = sh && pos[sh.k] ? pos[sh.k].slice() : spot.slice();
        var gk = onPitch(D).filter(function (o) { return o.pos === 'GK'; })[0];
        var stop = e.kind === 'contre' ? 0.2 : e.kind === 'arret' ? 0.92 : 1.03;
        var to = [ball[0] + (aim[0] - ball[0]) * stop, ball[1] + (aim[1] - ball[1]) * stop];
        if (e.kind === 'rate') to = [aim[0] + (attackRight(A) ? 3 : -3), aim[1] + (aim[1] > WID / 2 ? 6 : -6)];
        var nf = Math.max(6, Math.round(Math.hypot(to[0] - ball[0], to[1] - ball[1]) / (F_VB * 1.6) * F_FS)), b0 = ball.slice();
        var goal = e.kind === 'but' || e.kind === 'csc';
        pushLog({ kind: 'shot', side: A, e: e, who: sh ? sh.k : null, goal: goal,
                  txt: e.t + "'  " + (e.kind === 'csc' ? 'But contre son camp' : (sh ? sh.name : '') + (e.sit && SIT[e.sit] ? ' · ' + SIT[e.sit] : '') +
                       (e.type && TYP[e.type] ? ', ' + TYP[e.type] : '') + (e.xg ? ' · xG ' + e.xg.toFixed(2).replace('.', ',') : '') + ' · ' + KIND[e.kind]) });
        if (goal) score[A === 'home' ? 0 : 1]++;
        owner = null;
        for (var q = 1; q <= nf; q++) { ball = [b0[0] + (to[0] - b0[0]) * q / nf, b0[1] + (to[1] - b0[1]) * q / nf]; frames.push(snapshot({ k: 'tir', who: sh ? sh.k : null, e: e })); }
        si++;
        if (goal) kickoff(D);   // cibles du coup d'envoi : les joueurs y retournent pendant la celebration
        for (var q2 = 0; q2 < (goal ? 2.6 : 1.0) * F_FS; q2++) { step(); frames.push(snapshot({ k: goal ? 'but' : 'tir', who: sh ? sh.k : null, e: e, side: A })); }
        if (goal) { ball = [LEN / 2, WID / 2]; }
        else if (e.kind === 'contre') { owner = nearest(onPitch(D), ball); cur = null; }
        else { owner = gk || null; if (gk && pos[gk.k]) ball = pos[gk.k].slice(); cur = null; force = D; }   // le gardien relance
        if (e.kind === 'contre') force = D;
        continue;
      }
      // Passes
      if (cur && owner && !flight && gameT >= nextPass && !(cur.shot && owner.name === cur.shot.e.who && cur.end - gameT < 5)) {
        var mates = onPitch(A).filter(function (o) { return o !== owner && pos[o.k]; }), rcv = null;
        if (cur.shot && cur.end - gameT < 5) { rcv = byName[A + '|' + cur.shot.e.who]; if (rcv === owner) rcv = null; else if (rcv) nextPass = 0; }
        if (!rcv || !rcv.on || rcv === owner) {
          var bo = own(A, ball), tot = 0, ws = mates.map(function (o) {
            var p = own(A, pos[o.k]), dd = Math.hypot(p[0] - bo[0], p[1] - bo[1]);
            var wgt = Math.exp(-Math.pow(dd - 18, 2) / 350) * Math.max(0.15, 1 + 2.2 * clamp((p[0] - bo[0]) / 25, -0.4, 1.5)) * (o.pos === 'GK' ? 0.1 : 1);
            tot += wgt; return wgt; });
          if (cur.recycle) {   // passe en retrait vers un joueur plus bas
            ws = mates.map(function (o, i) { var p = own(A, pos[o.k]); return (p[0] < bo[0] - 12 && o.pos !== 'GK') ? 1 + (bo[0] - p[0]) / 20 : 0.01; });
            tot = ws.reduce(function (a, b) { return a + b; }, 0); cur.recycle = false;
          }
          var r = rng() * tot; for (var m = 0; m < mates.length; m++) { r -= ws[m]; if (r <= 0) { rcv = mates[m]; break; } }
          rcv = rcv || mates[0];
        }
        if (rcv) {
          var dest = pos[rcv.k].slice();
          if (cur.shot && rcv.name === cur.shot.e.who) dest = w(A, cur.shot.e.x || 94, cur.shot.e.y || 34);   // passe en profondeur vers le spot du tir
          else { var lead = own(A, dest); dest = w(A, Math.min(lead[0] + 4, 100), lead[1]); }                 // dans la course
          var dist = Math.hypot(dest[0] - ball[0], dest[1] - ball[1]);
          flight = { from: ball.slice(), to: dest, n: Math.max(3, Math.round(dist / F_VB * F_FS)), i: 0, rcv: rcv };
          owner = null;
        }
        nextPass = gameT + 1.5 + rng() * 2.5;
      }
      // Cibles : bloc qui attaque (monte, s'ecarte), bloc qui defend (recule, se resserre, coulisse), pressing
      sides.forEach(function (s) {
        var att = s === A, b = own(s, ball);
        onPitch(s).forEach(function (o) {
          var dx = o.lay ? o.lay[0] : 0.5, wy = o.lay ? o.lay[1] : 0.5, x, y;
          if (o.pos === 'GK') { x = att ? clamp(b[0] * 0.22, 4, 20) : clamp(2 + b[0] * 0.05, 2, 7); y = WID / 2 + (b[1] - WID / 2) * 0.15; }
          else if (att) { x = clamp(b[0] - 30 + dx * 64, 3, 101); y = WID / 2 + (wy - 0.5) * WID * 0.92 + (b[1] - WID / 2) * 0.18; }
          else { x = clamp(b[0] * 0.55 + dx * 40 - 6, 5, 82); y = WID / 2 + (wy - 0.5) * WID * 0.62 + (b[1] - WID / 2) * 0.42; }
          tgt[o.k] = w(s, x, clamp(y, 1.5, WID - 1.5));
        });
      });
      if (owner) { var adv = own(A, ball); tgt[owner.k] = w(A, Math.min(adv[0] + 10, Math.max(adv[0], 84)), adv[1]); }   // pas au-dela de l'entree de la surface
      if (cur && cur.shot && owner && owner.name === cur.shot.e.who) tgt[owner.k] = w(A, cur.shot.e.x || 94, cur.shot.e.y || 34);
      if (flight) tgt[flight.rcv.k] = flight.to;
      var pr = nearest(onPitch(D).filter(function (o) { return o.pos !== 'GK'; }), ball);
      if (pr) { var bd = own(D, ball); tgt[pr.k] = w(D, Math.max(bd[0] - 1.5, 1), bd[1]); }
      step();
      // Ballon
      if (flight) {
        flight.i++; var u = flight.i / flight.n;
        ball = [flight.from[0] + (flight.to[0] - flight.from[0]) * u, flight.from[1] + (flight.to[1] - flight.from[1]) * u];
        if (flight.i >= flight.n) { owner = flight.rcv; flight = null; }
      } else if (owner && pos[owner.k]) { var ar = attackRight(owner.side) ? 1 : -1; ball = [pos[owner.k][0] + 0.9 * ar, pos[owner.k][1] + 0.4]; }
      if (cur) possT[cur.side === 'home' ? 0 : 1] += 1;
      frames.push(snapshot(null));
      gameT += F_SPEED / F_FS;
    }
    for (var z = 0; z < 4 * F_FS; z++) frames.push(snapshot({ k: 'fin' }));
    return { full: true, d: d, all: ALL, frames: frames, log: log, T: frames.length / F_FS };

    function step() {
      for (var k in pos) {
        var p = pos[k], g = tgt[k]; if (!p || !g) continue;
        var dx = g[0] - p[0], dy = g[1] - p[1], dd = Math.hypot(dx, dy); if (dd < 1e-3) continue;
        var sp = Math.min(dd, F_VP / F_FS * Math.min(1, 0.3 + dd / 5));
        p[0] += dx / dd * sp; p[1] += dy / dd * sp;
      }
    }
    function snapshot(tag) {
      var P = new Float32Array(ALL.length * 2).fill(NaN);
      for (var k in pos) if (pos[k]) { P[2 * k] = pos[k][0]; P[2 * k + 1] = pos[k][1]; }
      var tot = possT[0] + possT[1];
      return { P: P, b: [ball[0], ball[1]], gt: gameT, sc: score.slice(), own: owner ? owner.k : -1, tag: tag, L: log.length,
               poss: tot ? possT[0] / tot : 0.5 };
    }
  }

  var COL = ['#95e04a', '#f5b942'];
  function drawFull(me) {
    var S = me.S, g = me.g, ff = Math.min(S.frames.length - 1, Math.max(0, me.t * F_FS)), f = Math.floor(ff), fr = S.frames[f], ti = me.title, tag = fr.tag || {};
    var nx = S.frames[Math.min(f + 1, S.frames.length - 1)], wf = ff - f;
    var lp = function (a, b) { return isNaN(b) ? a : a + (b - a) * wf; };   // lissage entre deux images
    g.fillStyle = C.bg; g.fillRect(0, 0, W, H);
    pitch(g);
    var cv = function (x, y) { return [PX + x / LEN * PW, PY + y / WID * PH]; };
    // Joueurs (masques pendant le panneau de mi-temps : changement de cote)
    if (tag.k !== 'mt') S.all.forEach(function (o) {
      var x = fr.P[2 * o.k], y = fr.P[2 * o.k + 1]; if (isNaN(x)) return;
      x = lp(x, nx.P[2 * o.k]); y = lp(y, nx.P[2 * o.k + 1]);
      var q = cv(x, y), col = COL[o.side === 'home' ? 0 : 1], r = 11;
      g.fillStyle = 'rgba(0,0,0,0.35)'; g.beginPath(); g.ellipse(q[0], q[1] + 9, 9, 3.5, 0, 0, 7); g.fill();
      g.fillStyle = o.pos === 'GK' ? '#e8ece9' : col; g.beginPath(); g.arc(q[0], q[1], r, 0, 7); g.fill();
      g.lineWidth = fr.own === o.k ? 3 : 1.5; g.strokeStyle = fr.own === o.k ? '#fff' : 'rgba(0,0,0,0.55)'; g.stroke();
      g.fillStyle = '#0b1208'; g.font = '700 11px ' + MONO; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(o.num || '·', q[0], q[1] + 0.5);
      if (fr.own === o.k) { g.fillStyle = C.tx; g.font = '600 12px ' + SANS; g.fillText(o.last, q[0], q[1] - 19); }
    });
    // Ballon
    var bq = cv(lp(fr.b[0], nx.b[0]), lp(fr.b[1], nx.b[1]));
    g.fillStyle = '#fff'; g.beginPath(); g.arc(bq[0], bq[1], 5.5, 0, 7); g.fill(); g.strokeStyle = '#111'; g.lineWidth = 1.2; g.stroke();
    // Tireur
    if ((tag.k === 'tir' || tag.k === 'but') && tag.who != null) {
      var o2 = S.all[tag.who], x2 = fr.P[2 * tag.who], y2 = fr.P[2 * tag.who + 1];
      if (!isNaN(x2)) {
        var q2 = cv(x2, y2), lab = (o2.num ? o2.num + '  ' : '') + o2.name.toUpperCase(), lab2 = KIND[tag.e.kind].toUpperCase();
        g.font = '800 15px ' + SANS; var w1 = g.measureText(lab).width + 18; g.font = '700 13px ' + SANS; var w2 = g.measureText(lab2).width + 16;
        var x0 = clamp(q2[0] - (w1 + w2) / 2, PX, PX + PW - w1 - w2), y0 = q2[1] - 48;
        g.fillStyle = COL[o2.side === 'home' ? 0 : 1]; g.fillRect(x0, y0, w1, 26); g.fillStyle = tag.e.kind === 'but' ? C.tx : 'rgba(15,20,17,0.95)'; g.fillRect(x0 + w1, y0, w2, 26);
        g.fillStyle = '#0b1208'; g.font = '800 15px ' + SANS; g.textAlign = 'left'; g.fillText(lab, x0 + 9, y0 + 14);
        g.fillStyle = tag.e.kind === 'but' ? '#0b1208' : C.tx; g.font = '700 13px ' + SANS; g.fillText(lab2, x0 + w1 + 8, y0 + 14);
      }
    }
    // Changements : etiquette sur le joueur qui entre (pendant 3 s)
    S.log.slice(Math.max(0, fr.L - 6), fr.L).forEach(function (l) {
      if (l.kind !== 'sub' || f - l.f > 3 * F_FS || l.who == null) return;
      var xs = fr.P[2 * l.who], ys = fr.P[2 * l.who + 1]; if (isNaN(xs)) return;
      var qs = cv(lp(xs, nx.P[2 * l.who]), lp(ys, nx.P[2 * l.who + 1])), o3 = S.all[l.who];
      var t1 = (o3.num ? o3.num + '  ' : '') + o3.name.toUpperCase(), t2 = 'ENTRÉE';
      g.font = '800 14px ' + SANS; var a1 = g.measureText(t1).width + 16; g.font = '700 12px ' + SANS; var a2 = g.measureText(t2).width + 14;
      var xa = clamp(qs[0] - (a1 + a2) / 2, PX, PX + PW - a1 - a2), ya = qs[1] - 44;
      g.fillStyle = COL[o3.side === 'home' ? 0 : 1]; g.fillRect(xa, ya, a1, 24); g.fillStyle = C.tx; g.fillRect(xa + a1, ya, a2, 24);
      g.fillStyle = '#0b1208'; g.textAlign = 'left'; g.textBaseline = 'middle'; g.font = '800 14px ' + SANS; g.fillText(t1, xa + 8, ya + 13);
      g.font = '700 12px ' + SANS; g.fillText(t2, xa + a1 + 7, ya + 13);
    });
    if (tag.k === 'but') { g.fillStyle = COL[tag.side === 'home' ? 0 : 1]; g.font = '800 84px ' + SANS; g.textAlign = 'center'; g.fillText(tag.e.kind === 'csc' ? 'BUT CSC !' : 'BUT !', PX + PW / 2, PY + PH / 2); }
    // Bandeau : equipes, score, minute, possession
    var mins = Math.floor(fr.gt / 60), clock = tag.k === 'fin' ? 'Fin' : tag.k === 'mt' ? 'Mi-temps' : (mins >= 90 ? "90'+" + (mins - 89) : (mins + 1) + "'");
    g.fillStyle = C.panel; g.fillRect(PX, 22, PW, 80);
    g.textBaseline = 'middle'; g.font = '700 28px ' + SANS;
    g.fillStyle = COL[0]; g.textAlign = 'right'; g.fillText(ti.home || 'Domicile', PX + PW / 2 - 85, 52);
    g.fillStyle = COL[1]; g.textAlign = 'left'; g.fillText(ti.away || 'Extérieur', PX + PW / 2 + 85, 52);
    g.fillStyle = C.tx; g.textAlign = 'center'; g.font = '700 40px ' + MONO; g.fillText(fr.sc[0] + ' - ' + fr.sc[1], PX + PW / 2, 52);
    g.font = '600 18px ' + MONO; g.fillStyle = C.home; g.textAlign = 'left'; g.fillText(clock, PX + 16, 52);
    g.fillStyle = C.mu; g.textAlign = 'center'; g.font = '500 14px ' + MONO;
    g.fillText('possession ' + Math.round(100 * fr.poss) + ' % - ' + (100 - Math.round(100 * fr.poss)) + ' %', PX + PW / 2, 88);
    // Ticker : derniere action
    g.fillStyle = C.panel; g.fillRect(PX, PY + PH + 16, PW, 52);
    var lg = S.log[fr.L - 1];
    if (lg && (f - lg.f) < 8 * F_FS) { g.fillStyle = lg.side ? COL[lg.side === 'home' ? 0 : 1] : C.tx; g.font = '500 21px ' + SANS; g.textAlign = 'left'; g.fillText(lg.txt, PX + 16, PY + PH + 43); }
    if (tag.k === 'mt' || tag.k === 'fin') {
      g.fillStyle = 'rgba(10,13,11,0.9)'; g.fillRect(PX + PW / 2 - 300, PY + PH / 2 - 80, 600, 160);
      g.fillStyle = C.tx; g.textAlign = 'center'; g.font = '700 44px ' + SANS;
      g.fillText((tag.k === 'mt' ? 'Mi-temps ' : 'Score final ') + fr.sc[0] + ' - ' + fr.sc[1], PX + PW / 2, PY + PH / 2 - 18);
      g.font = '500 18px ' + MONO; g.fillStyle = C.mu; g.fillText(ti.foot || 'Match simulé, pas un pronostic', PX + PW / 2, PY + PH / 2 + 34);
    }
    g.fillStyle = C.di || C.mu; g.font = '500 13px ' + MONO; g.textAlign = 'right'; g.fillText('Match simulé, pas une prédiction', W - 20, H - 12);
  }

  window.SPFoot = { open: function (host, data, title) { return new Player(host, data, title); } };
})();
