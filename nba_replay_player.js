/* nba_replay_player.js — Lecteur du « match type » simule (canvas 1920x1080).
 * Un seul rendu deterministe draw(t) sert a la lecture en direct et a l'export MP4
 * (WebCodecs + mp4-muxer, image par image ; repli MediaRecorder si indisponible).
 * Donnees : JSON produit par nba_replay.py (evenements, joueurs, resume des simulations).
 */
(function () {
  if (window.SPReplay) return;
  var W = 1920, H = 1080, FPS = 30;
  var C = { bg: '#0a0d0b', panel: 'rgba(15,20,17,0.92)', line: '#1f2a22', tx: '#eef3ef', mu: '#9aa89e', di: '#6b7a70', acc: '#95e04a', loss: '#ef4444' };
  var SANS = "'Space Grotesk', 'Segoe UI', sans-serif", MONO = "'JetBrains Mono', Consolas, monospace";
  var DUR = { intro: 3.5, box: 8, summary: 7 };

  function lum(hex) {
    var h = (hex || '').replace('#', ''); if (h.length !== 6) return 0;
    var c = [0, 2, 4].map(function (i) { var v = parseInt(h.substr(i, 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  function teamColor(t) {
    var a = '#' + (t.color || '94a3b8'), b = '#' + (t.alt || '94a3b8');
    return lum(a) >= 0.045 ? a : (lum(b) >= 0.045 ? b : '#94a3b8');
  }
  function rgb(hex) { var h = hex.replace('#', ''); return [0, 2, 4].map(function (i) { return parseInt(h.substr(i, 2), 16); }); }
  function cdist(a, b) { var x = rgb(a), y = rgb(b); return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]); }
  function teamColors(home, away) {
    // Deux couleurs proches (ex. Cavaliers et Hawks, rouge tous les deux) : l'equipe visiteuse passe a
    // sa couleur secondaire, ou a un blanc casse, pour distinguer attaque et defense
    var h = teamColor(home), a = teamColor(away);
    if (cdist(h, a) >= 110) return [h, a];
    var alt = '#' + (away.alt || '');
    if (alt.length === 7 && lum(alt) >= 0.045 && cdist(h, alt) >= 110) return [h, alt];
    return [h, '#e8ece9'];
  }
  function fmtClock(c) { c = Math.max(0, c); if (c < 60) return c.toFixed(1); var m = Math.floor(c / 60), s = Math.floor(c % 60); return m + ':' + (s < 10 ? '0' : '') + s; }
  function pct(x, d) { return (100 * x).toFixed(d == null ? 1 : d).replace('.', ',') + ' %'; }
  function lerp(a, b, u) { return a + (b - a) * u; }
  function ease(u) { u = Math.max(0, Math.min(1, u)); return u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2; }
  function rnd(seed) { var x = Math.sin(seed * 9301 + 49297) * 233280; return x - Math.floor(x); }

  /* ── Preparation : chronologie + etats cumules ───────────────────────── */
  // Match complet : chaque action dure a l'ecran son vrai temps de jeu / SPEED (remontee de balle, mise en
  // place, action), plus le temps de montrer son issue (EXTRA). Resume : 75 temps forts (~3 min).
  var SPEED = 8;
  var EXTRA = { shot: 0.75, ft: 0.4, reb: 0.25, to: 0.5, foul: 0.5, sub: 0.25, end: 3.0 };
  // Deplacements : une vitesse de course maximale, la meme partout (pieds de terrain par seconde de
  // video), positions calculees image par image (FS par seconde) ; la video attend le joueur de
  // l'action s'il n'est pas encore a son spot, au lieu de le faire sauter
  var VMAX = 38, FS = 20;
  function gtime(e) { return e.q <= 4 ? (e.q - 1) * 720 + (720 - e.c) : 2880 + (e.q - 5) * 300 + (300 - e.c); }
  function prepare(data, mode) {
    var full = mode !== 'resume';
    var ev = data.events, P = data.players, n = P.length;
    var cols = ['MIN', 'PTS', 'FGM', 'FGA', 'TPM', 'TPA', 'FTM', 'FTA', 'REB', 'AST', 'STL', 'BLK', 'PF'];
    var box = cols.map(function () { return new Float32Array(n); });
    var B = {}; cols.forEach(function (c, i) { B[c] = box[i]; });
    var on = {}; data.starters.forEach(function (j) { on[j] = true; });
    var gPrev = 0, fouls = [0, 0], curQ = 1;
    var keep = {};
    if (!full) {
      ev.map(function (e, i) { return { i: i, score: (e.hl ? 1 : 0) * (10 + e.pr + (e.lc ? 5 : 0) + ((e.q === 4 && e.c <= 120) || e.q >= 5 ? 20 : 0)) }; })
        .filter(function (x) { return x.score > 0; }).sort(function (a, b) { return b.score - a.score; }).slice(0, 75)
        .forEach(function (x) { keep[x.i] = true; });
      ev.forEach(function (e, i) { if (e.k === 'end') keep[i] = true; });
    }
    var t = DUR.intro, timeline = [], team = 0, ps = 0, sc0 = 24, gLast = 0;
    ev.forEach(function (e, i) {
      var g = gtime(e);
      if (e.q !== curQ) { fouls = [0, 0]; curQ = e.q; ps = g; sc0 = 24; gLast = g; }
      for (var j in on) B.MIN[j] += Math.max(0, g - gPrev) / 60;
      gPrev = Math.max(gPrev, g);
      if (e.k === 'sub') { e.out.forEach(function (j) { delete on[j]; }); e['in'].forEach(function (j) { on[j] = true; }); }
      if (e.k === 'shot') { B.FGA[e.p]++; if (e.v === 3) B.TPA[e.p]++; if (e.made) { B.FGM[e.p]++; B.PTS[e.p] += e.v; if (e.v === 3) B.TPM[e.p]++; if (e.ast != null) B.AST[e.ast]++; } if (e.blk != null) B.BLK[e.blk]++; }
      if (e.k === 'ft') { B.FTA[e.p]++; if (e.made) { B.FTM[e.p]++; B.PTS[e.p]++; } }
      if (e.k === 'reb') B.REB[e.p]++;
      if (e.k === 'to' && e.stl != null) B.STL[e.stl]++;
      if (e.k === 'foul' || (e.k === 'to' && e.off)) { B.PF[e.p]++; if (e.k === 'foul') fouls[P[e.p].team]++; }
      var tm = e.team != null ? e.team : team;
      var newPoss = e.team != null && e.team !== team;
      if (newPoss) { ps = gLast; sc0 = 24; }
      var big = full || !!keep[i];
      var app = full ? Math.min(Math.max(0, g - gLast) / SPEED, 5) : (big && e.k !== 'sub' && e.k !== 'end' ? 0.6 : 0);
      var ex = !big ? (e.k === 'sub' ? 0.04 : 0.07) : full ? (EXTRA[e.k] || 0.5) : (e.k === 'shot' ? 1.4 : e.k === 'end' ? 3.6 : 0.8);
      timeline.push({ e: e, i: i, k: timeline.length, t0: t, app: app, ex: ex, d: app + ex, big: big, g0: gLast, g: g, team: tm,
                      newPoss: newPoss, ps: ps, sc0: sc0, box: box.map(function (a) { return a.slice(); }),
                      on: Object.keys(on).map(Number), fouls: fouls.slice() });
      t += app + ex;
      if (e.k === 'reb' && e.o) { ps = g; sc0 = 14; }          // rebond offensif : 14 secondes
      if (e.k === 'foul') { sc0 = Math.max(14, sc0 - (g - ps)); ps = g; }   // faute : au moins 14 secondes
      if (e.team != null) team = e.team;
      gLast = Math.max(gLast, g);
    });
    var quarters = data.quarters;
    if (!quarters) { quarters = []; var pv = [0, 0]; ev.forEach(function (e) { if (e.k === 'end') { quarters.push([e.s[0] - pv[0], e.s[1] - pv[1]]); pv = e.s; } }); }
    var S = { data: data, mode: full ? 'full' : 'resume', tl: timeline, cols: cols, quarters: quarters,
              colors: teamColors(data.meta.home, data.meta.away) };
    simulateMotion(S);
    S.total = S.gameEnd + DUR.box + DUR.summary;
    return S;
  }

  function simulateMotion(S) {
    var P = S.data.players, n = P.length, tl = S.tl, cur = {}, frames = [];
    function snap() { var a = new Float32Array(2 * n).fill(NaN); for (var j in cur) { a[2 * j] = cur[j][0]; a[2 * j + 1] = cur[j][1]; } frames.push(a); }
    function step(tg) {
      for (var j in cur) {
        var g = tg[j]; if (!g) continue;
        var dx = g[0] - cur[j][0], dy = g[1] - cur[j][1], d = Math.hypot(dx, dy);
        if (d < 1e-3) continue;
        var sp = Math.min(d, (VMAX / FS) * Math.min(1, 0.25 + d / 6));   // ralentit en arrivant
        cur[j][0] += dx / d * sp; cur[j][1] += dy / d * sp;
      }
    }
    var f0 = formation(S, tl[0]);
    tl[0].on.forEach(function (j) { cur[j] = (f0[j] || [47, 25]).slice(); });
    for (var i = 0; i < Math.round(DUR.intro * FS); i++) snap();
    tl.forEach(function (item) {
      var e = item.e;
      item.t0 = frames.length / FS;
      // Changements : le sortant quitte le terrain, l'entrant arrive du banc (ligne de touche, milieu)
      for (var j in cur) if (item.on.indexOf(+j) < 0) delete cur[j];
      item.on.forEach(function (j) { if (!cur[j]) cur[j] = [47 + (P[j].team === 0 ? 9 : -9), 53]; });
      var fin = formation(S, item);
      var fast = e.ctx === 'contre-attaque' || e.ctx === 'transition';
      var bu = item.newPoss && !fast && item.app > 0.9 ? bringUp(S, item) : null;
      var actor = item.big && (e.k === 'shot' || e.k === 'ft') ? e.p : null;   // resume : on n'attend que pour les temps forts
      var nApp = Math.round(item.app * FS), k2 = 0;
      while (k2 < nApp || (actor != null && cur[actor] && fin[actor] && Math.hypot(cur[actor][0] - fin[actor][0], cur[actor][1] - fin[actor][1]) > 2.5 && k2 < nApp + 3 * FS)) {
        var u = nApp ? k2 / nApp : 1, tg = {};
        item.on.forEach(function (j) { tg[j] = (bu && u < 0.45 && bu[j]) ? bu[j] : fin[j]; });
        step(tg); snap(); k2++;
      }
      item.app = k2 / FS;
      var nEx = Math.max(1, Math.round(item.ex * FS));
      for (var k3 = 0; k3 < nEx; k3++) { step(fin); snap(); }
      item.d = item.app + nEx / FS;
    });
    S.frames = frames;
    S.gameEnd = frames.length / FS;
  }
  function framePos(S, t, on) {
    var f = Math.max(0, Math.min(S.frames.length - 1, t * FS)), i0 = Math.floor(f), i1 = Math.min(i0 + 1, S.frames.length - 1), w = f - i0;
    var A = S.frames[i0], B = S.frames[i1], pos = {};
    on.forEach(function (j) {
      var ax = A[2 * j], ay = A[2 * j + 1], bx = B[2 * j], by = B[2 * j + 1];
      if (isNaN(ax)) { ax = bx; ay = by; }
      if (isNaN(bx)) { bx = ax; by = ay; }
      if (!isNaN(ax)) pos[j] = [lerp(ax, bx, w), lerp(ay, by, w)];
    });
    return pos;
  }

  /* ── Terrain en perspective (pieds : longueur 94, largeur 50) ────────── */
  var CX = 960, YF = 350, YN = 880, KX = 11.4, SF = 0.80;
  function proj(X, Y) {
    var s = SF + (1 - SF) * (Y / 50);
    return [CX + (X - 47) * KX * s, YF + (YN - YF) * (Y / 50), s];
  }
  function path(ctx, pts) { ctx.beginPath(); pts.forEach(function (p, i) { var q = proj(p[0], p[1]); i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); }); }
  function arc(cx, cy, r, a0, a1, n) { var o = []; for (var i = 0; i <= n; i++) { var a = a0 + (a1 - a0) * i / n; o.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]); } return o; }
  var courtCache = null;
  function drawCourt(ctx) {
    if (!courtCache) {
      courtCache = document.createElement('canvas'); courtCache.width = W; courtCache.height = H;
      var c = courtCache.getContext('2d');
      var corners = [proj(-3, -3), proj(97, -3), proj(97, 53), proj(-3, 53)];
      c.beginPath(); corners.forEach(function (q, i) { i ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1]); }); c.closePath();
      c.fillStyle = '#1a120c'; c.fill();
      var fl = [proj(0, 0), proj(94, 0), proj(94, 50), proj(0, 50)];
      c.beginPath(); fl.forEach(function (q, i) { i ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1]); }); c.closePath();
      var gr = c.createLinearGradient(0, YF, 0, YN); gr.addColorStop(0, '#5a3d24'); gr.addColorStop(1, '#7a5432'); c.fillStyle = gr; c.fill();
      c.save(); c.clip(); c.globalAlpha = 0.08; c.strokeStyle = '#000';
      for (var y = 0; y <= 50; y += 1.2) { path(c, [[0, y], [94, y]]); c.stroke(); }
      c.restore();
      c.strokeStyle = 'rgba(255,255,255,0.85)'; c.lineWidth = 2.6; c.lineJoin = 'round';
      path(c, [[0, 0], [94, 0], [94, 50], [0, 50], [0, 0]]); c.stroke();
      path(c, [[47, 0], [47, 50]]); c.stroke();
      path(c, arc(47, 25, 6, 0, 2 * Math.PI, 48)); c.stroke();
      [0, 1].forEach(function (side) {
        var bx = side ? 88.75 : 5.25, dir = side ? -1 : 1, base = side ? 94 : 0;
        c.fillStyle = 'rgba(0,0,0,0.18)';
        path(c, [[base, 17], [base + dir * 19, 17], [base + dir * 19, 33], [base, 33]]); c.closePath(); c.fill(); c.stroke();
        path(c, arc(base + dir * 19, 25, 6, -Math.PI / 2, Math.PI / 2, 24).map(function (p) { return [base + dir * 19 + dir * (p[0] - (base + dir * 19)), p[1]]; })); c.stroke();
        var pts3 = [[base, 3], [base + dir * 14, 3]];
        var arcPts = []; for (var i = 0; i <= 40; i++) { var a = -Math.PI / 2 + (Math.PI) * i / 40; var yy = 25 + 23.75 * Math.sin(a); if (yy < 3 || yy > 47) continue; arcPts.push([bx + dir * 23.75 * Math.cos(a), yy]); }
        path(c, pts3.concat(arcPts).concat([[base + dir * 14, 47], [base, 47]])); c.stroke();
        path(c, arc(bx, 25, 4, -Math.PI / 2, Math.PI / 2, 16).map(function (p) { return [bx + dir * (p[0] - bx), p[1]]; })); c.stroke();
        var r = proj(bx, 25), bb = proj(base + dir * 4, 25);
        c.strokeStyle = 'rgba(255,255,255,0.9)'; c.lineWidth = 4;
        c.beginPath(); c.moveTo(bb[0], bb[1] - 46); c.lineTo(bb[0], bb[1] - 6); c.stroke();
        c.strokeStyle = '#f97316'; c.lineWidth = 3.2; c.beginPath(); c.ellipse(r[0], r[1] - 34, 11, 5, 0, 0, 2 * Math.PI); c.stroke();
        c.strokeStyle = 'rgba(255,255,255,0.85)'; c.lineWidth = 2.6;
      });
    }
    ctx.drawImage(courtCache, 0, 0);
  }

  /* ── Positions des joueurs ───────────────────────────────────────────── */
  var OFF_SPOTS = [[4, 3], [22, 10], [29, 25], [22, 40], [4, 47]];
  function attackRight(team, q) { var firstHalf = q <= 2; return team === 0 ? firstHalf : !firstHalf; }
  function toCourt(team, q, dx, y) { return attackRight(team, q) ? [94 - dx, y] : [dx, y]; }
  // Zones de jeu : [x en largeur 0-50, profondeur depuis le panier]
  var ROLE_SPOTS = [[[25, 25], [9, 21], [41, 21]], [[3, 4], [47, 4], [12, 15], [38, 15]], [[17, 4], [33, 4], [25, 10]]];
  var FREE = [[25, 25], [8, 21], [42, 21], [2, 4], [48, 4], [17, 4], [33, 4], [13, 14], [37, 14]];
  function role(p) { var s = (p.pos || '').toUpperCase(); return s.indexOf('C') >= 0 ? 2 : s.indexOf('F') >= 0 ? 1 : 0; }
  function dist(a, b) { return Math.hypot(a[0] - b[0], a[1] - b[1]); }
  function byRole(P, list) { return list.slice().sort(function (a, b) { return role(P[a]) - role(P[b]) || a - b; }); }
  function baseSpots(P, off) {
    // Chaque attaquant a sa zone habituelle (nba_replay._spot : la ou il tire le plus) ; deux joueurs
    // au meme endroit -> le second prend la place libre la plus proche (ecartement)
    var used = [], out = {};
    byRole(P, off).forEach(function (j) {
      var r = ROLE_SPOTS[role(P[j])], sp = P[j].spot || r[j % r.length];
      if (used.some(function (u) { return dist(u, sp) < 8; })) {
        var cand = FREE.filter(function (f) { return used.every(function (u) { return dist(u, f) >= 8; }); });
        if (cand.length) sp = cand.sort(function (a, b) { return dist(a, sp) - dist(b, sp); })[0];
      }
      used.push(sp); out[j] = sp;
    });
    return out;
  }
  function formation(S, item) {
    if (item.pos) return item.pos;
    var e = item.e, P = S.data.players, pos = {};
    if (e.k === 'end' && item.k > 0) return (item.pos = formation(S, S.tl[item.k - 1]));
    var offT = item.team != null ? item.team : (e.team != null ? e.team : 0), q = e.q;
    var at = function (x, d) { return toCourt(offT, q, 5.25 + d, x); };
    var off = item.on.filter(function (j) { return P[j].team === offT; }), def = item.on.filter(function (j) { return P[j].team !== offT; });
    var hoop = at(25, 0);
    if (e.k === 'ft' && e.p != null) {
      // Lancers francs : tireur sur la ligne, raquette alignee (defense au plus pres du cercle), deux joueurs a l'arriere
      pos[e.p] = at(25, 13.75);
      var LD = [[17, 7], [33, 7], [17, 13]], LO = [[33, 10], [17, 10]], BD = [[12, 30], [38, 30]], BO = [[25, 34], [6, 28], [44, 28]];
      byRole(P, def).reverse().forEach(function (j, k) { var s2 = k < 3 ? LD[k] : BD[k - 3]; pos[j] = at(s2[0], s2[1]); });
      byRole(P, off.filter(function (j) { return j !== e.p; })).reverse().forEach(function (j, k) { var s2 = k < 2 ? LO[k] : BO[k - 2]; pos[j] = at(s2[0], s2[1]); });
      return (item.pos = pos);
    }
    var sp = baseSpots(P, off);
    off.forEach(function (j) { pos[j] = at(sp[j][0], sp[j][1]); });
    if (e.k === 'shot' && e.xy && pos[e.p]) pos[e.p] = at(e.xy[0], e.xy[1]);              // le vrai spot de tir
    if (e.k === 'reb' && e.p != null && e.o && pos[e.p]) pos[e.p] = at(e.p % 2 ? 21 : 29, 3.5);   // rebond offensif
    // Defense homme a homme : defenseurs et attaquants apparies par poste, entre leur joueur et le cercle
    var D = byRole(P, def), O = byRole(P, off);
    D.forEach(function (j, k) {
      var man = O[k % Math.max(O.length, 1)], o = pos[man] || hoop;
      var sag = (e.k === 'shot' && man === e.p) ? 0.12 : (sp[man] && sp[man][1] > 20 ? 0.22 : 0.3);   // plus pres du tireur
      pos[j] = [lerp(o[0], hoop[0], sag), lerp(o[1], hoop[1], sag) + (o[1] < 25 ? 2.2 : -2.2)];
    });
    if (e.k === 'reb' && e.p != null && !e.o) pos[e.p] = at(e.p % 2 ? 22 : 28, 4.5);             // rebond defensif
    if (e.k === 'to' && e.stl != null && pos[e.p]) pos[e.stl] = [pos[e.p][0] + 1.5, pos[e.p][1] + 1.2];
    return (item.pos = pos);
  }

  // Remontee de balle : le meneur passe le milieu de terrain, les ailiers dans les couloirs, la defense se replie
  var LANES = [[25, 44], [8, 36], [42, 36], [16, 30], [34, 30]];
  function bringUp(S, item) {
    if (item.bu) return item.bu;
    var P = S.data.players, offT = item.team, q = item.e.q, bu = {};
    var at = function (x, d) { return toCourt(offT, q, 5.25 + d, x); };
    byRole(P, item.on.filter(function (j) { return P[j].team === offT; })).forEach(function (j, k) { var l = LANES[Math.min(k, 4)]; bu[j] = at(l[0], l[1]); });
    byRole(P, item.on.filter(function (j) { return P[j].team !== offT; })).forEach(function (j, k) { var l = LANES[Math.min(k, 4)]; bu[j] = at(lerp(l[0], 25, 0.3), l[1] - 10); });
    return (item.bu = bu);
  }
  // Porteur du ballon pendant la mise en place
  function handler(S, item) {
    var e = item.e, P = S.data.players;
    if (e.k === 'shot') return e.ast != null ? e.ast : e.p;
    if (e.p != null && P[e.p] && P[e.p].team === item.team && item.on.indexOf(e.p) >= 0) return e.p;
    var off = byRole(P, item.on.filter(function (j) { return P[j].team === item.team; }));
    return off.length ? off[0] : null;
  }
  // Etiquette au-dessus d'un joueur : numero + nom (couleur d'equipe) puis l'action
  function callout(S, ctx, j, sx, sy, lab2, alpha, bad) {
    var P = S.data.players, col = S.colors[P[j].team];
    var label = (P[j].num ? P[j].num + '  ' : '') + P[j].short.toUpperCase();
    ctx.font = '800 22px ' + SANS; var w1 = ctx.measureText(label).width + 24; ctx.font = '700 18px ' + SANS; var w2 = ctx.measureText(lab2).width + 22;
    var x0 = Math.min(Math.max(sx - (w1 + w2) / 2, 460), 1460 - w1 - w2), y0 = sy - 72;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = col; ctx.fillRect(x0, y0, w1, 36);
    ctx.fillStyle = bad ? C.loss : C.acc; ctx.fillRect(x0 + w1, y0, w2, 36);
    ctx.fillStyle = lum(col) > 0.45 ? '#0b1208' : '#fff'; ctx.font = '800 22px ' + SANS; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(label, x0 + 12, y0 + 19);
    ctx.fillStyle = bad ? '#fff' : '#0b1208'; ctx.font = '700 18px ' + SANS; ctx.fillText(lab2, x0 + w1 + 11, y0 + 19);
    ctx.globalAlpha = 1;
  }

  /* ── Rendu d'une image ──────────────────────────────────────────────── */
  function draw(S, ctx, t) {
    var D = S.data, P = D.players;
    ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, H);
    var g1 = ctx.createRadialGradient(200, 0, 50, 200, 0, 900); g1.addColorStop(0, hexA(S.colors[1], 0.18)); g1.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g1; ctx.fillRect(0, 0, W, H);
    var g2 = ctx.createRadialGradient(1720, 0, 50, 1720, 0, 900); g2.addColorStop(0, hexA(S.colors[0], 0.18)); g2.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g2; ctx.fillRect(0, 0, W, H);
    if (t >= S.gameEnd + DUR.box) return drawSummary(S, ctx, t - S.gameEnd - DUR.box);
    if (t >= S.gameEnd) return drawFinalBox(S, ctx, t - S.gameEnd);
    var idx = 0, tl = S.tl;
    var lo = 0, hi = tl.length - 1;
    while (lo <= hi) { var mid = (lo + hi) >> 1; if (tl[mid].t0 <= t) { idx = mid; lo = mid + 1; } else hi = mid - 1; }
    var cur = tl[idx], prev = tl[Math.max(0, idx - 1)], e = cur.e;
    var lt = Math.max(0, t - cur.t0);
    var ua = t < DUR.intro ? 0 : (cur.app > 0 ? Math.min(1, lt / cur.app) : 1);                 // mise en place
    var ux = t < DUR.intro ? 0 : Math.max(0, Math.min(1, (lt - cur.app) / Math.max(cur.d - cur.app, 1e-3)));   // issue
    drawCourt(ctx);
    // Joueurs : trajets precalcules a vitesse de course constante (simulateMotion) + leger mouvement sur place
    var pos = framePos(S, t, cur.on);
    cur.on.forEach(function (j) {
      if (!pos[j]) { var fb = formation(S, cur)[j] || [47, 25]; pos[j] = fb.slice(); }
      if (j !== e.p && e.k !== 'ft') { pos[j][0] += Math.sin(t * 1.1 + j * 2.3) * 0.5; pos[j][1] += Math.cos(t * 0.8 + j * 1.7) * 0.5; }
    });
    var tokens = cur.on.map(function (j) { return { j: j, X: pos[j][0], Y: pos[j][1] }; }).sort(function (a, b) { return a.Y - b.Y; });
    tokens.forEach(function (tk) {
      var q = proj(tk.X, tk.Y), r = 17 * q[2], col = S.colors[P[tk.j].team];
      ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.beginPath(); ctx.ellipse(q[0], q[1] + r * 0.8, r * 0.9, r * 0.35, 0, 0, 2 * Math.PI); ctx.fill();
      ctx.fillStyle = col; ctx.beginPath(); ctx.arc(q[0], q[1], r, 0, 2 * Math.PI); ctx.fill();
      ctx.lineWidth = (e.p === tk.j && cur.big) ? 4 : 2; ctx.strokeStyle = (e.p === tk.j && cur.big) ? C.acc : 'rgba(255,255,255,0.85)'; ctx.stroke();
      ctx.fillStyle = lum(col) > 0.45 ? '#0b1208' : '#fff'; ctx.font = '700 ' + Math.round(14 * q[2]) + 'px ' + MONO; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(P[tk.j].num || '·', q[0], q[1] + 1);
    });
    // Ballon : dribble du porteur pendant la mise en place, puis l'issue de l'action
    var inAct = lt >= cur.app;
    var hoop = toCourt(cur.team, e.q, 5.25, 25), hq = proj(hoop[0], hoop[1]);
    var dribble = function (j) { if (j == null || !pos[j]) return; var q = proj(pos[j][0], pos[j][1]); drawBall(ctx, q[0] + 13 * q[2], q[1] - 6 - Math.abs(Math.sin(t * 9)) * 16 * q[2], q[2]); };
    var at = function (j) { var q = proj(pos[j][0], pos[j][1]); return q; };
    if (e.k === 'shot' && cur.big) { if (inAct) drawShot(S, ctx, cur, ux, pos); else dribble(handler(S, cur)); }
    else if (!inAct && e.k !== 'end' && e.k !== 'sub') dribble(handler(S, cur));
    else if (cur.big && inAct && e.p != null && pos[e.p]) {
      var q0 = at(e.p), al = Math.min(1, ux / 0.15);
      if (e.k === 'reb') {   // le ballon retombe du cercle dans les mains du rebondeur
        var f = ease(Math.min(1, ux / 0.6)); drawBall(ctx, lerp(hq[0], q0[0] + 12, f), lerp(hq[1] - 40, q0[1] - 12, f) - Math.sin(Math.PI * f) * 30, 1);
        callout(S, ctx, e.p, q0[0], q0[1], e.o ? 'REBOND OFFENSIF' : 'REBOND', al);
      } else if (e.k === 'ft') {   // lancer franc : arc vers le cercle
        var f2 = Math.min(1, ux / 0.7), xb = lerp(q0[0], hq[0], f2), yb = lerp(q0[1] - 14, hq[1] - 34, f2) - Math.sin(Math.PI * f2) * 90;
        drawBall(ctx, ux < 0.7 ? xb : hq[0] + (e.made ? 0 : 24 * (ux - 0.7) / 0.3), ux < 0.7 ? yb : hq[1] - 34 + 30 * (ux - 0.7) / 0.3, 1);
        callout(S, ctx, e.p, q0[0], q0[1], 'LANCER ' + e.i + '/' + e.n + (ux > 0.7 ? (e.made ? ' ✓' : ' ✗') : ''), al, ux > 0.7 && !e.made);
      } else if (e.k === 'to') {
        var tgt = e.stl != null && pos[e.stl] ? at(e.stl) : q0, f3 = ease(Math.min(1, ux / 0.5));
        drawBall(ctx, lerp(q0[0], tgt[0], f3) + 12, lerp(q0[1], tgt[1], f3) - 12, 1);
        callout(S, ctx, e.stl != null ? e.stl : e.p, tgt[0], tgt[1], e.stl != null ? 'INTERCEPTION' : (e.off ? 'PASSAGE EN FORCE' : 'PERTE DE BALLE'), al, true);
      } else if (e.k === 'foul') {
        callout(S, ctx, e.p, q0[0], q0[1], (e.pf ? (e.pf === 1 ? '1re' : e.pf + 'e') + ' FAUTE' : 'FAUTE') + (e.pf >= 6 ? ' · EXCLU' : ''), al, true);
        if (e.on != null && pos[e.on]) dribble(e.on);
      }
    }
    drawScoreboard(S, ctx, cur, idx, ua, ux);
    drawBox(S, ctx, cur, 1, 30, 'left');
    drawBox(S, ctx, cur, 0, W - 30, 'right');
    drawTicker(S, ctx, cur, ux);
    if (e.k === 'end' && idx < tl.length - 1) drawQuarters(S, ctx, 610, 300, e.q, e.s, Math.min(1, ux / 0.15));
    ctx.fillStyle = C.di; ctx.font = '500 17px ' + MONO; ctx.textAlign = 'right'; ctx.textBaseline = 'alphabetic';
    ctx.fillText('Match simulé, pas une prédiction · simulation ' + fmtN(D.summary.k) + ' sur ' + fmtN(D.summary.n), W - 34, H - 18);
    ctx.textAlign = 'left'; ctx.fillStyle = C.tx; ctx.font = '700 24px ' + SANS; ctx.fillText('Sports', 34, H - 22);
    ctx.fillStyle = C.acc; ctx.fillText('Picks', 34 + ctx.measureText('Sports').width, H - 22);
    if (t < DUR.intro) drawIntro(S, ctx, t);
  }
  function fmtN(x) { return String(x).replace(/\B(?=(\d{3})+(?!\d))/g, ' '); }
  function hexA(hex, a) { var h = hex.replace('#', ''); return 'rgba(' + parseInt(h.substr(0, 2), 16) + ',' + parseInt(h.substr(2, 2), 16) + ',' + parseInt(h.substr(4, 2), 16) + ',' + a + ')'; }
  function drawBall(ctx, x, y, s) {
    var r = 9 * s; var g = ctx.createRadialGradient(x - r / 3, y - r / 3, 1, x, y, r); g.addColorStop(0, '#fdba74'); g.addColorStop(1, '#c2410c');
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill(); ctx.strokeStyle = 'rgba(0,0,0,0.5)'; ctx.lineWidth = 1.2; ctx.stroke();
  }
  function drawShot(S, ctx, cur, u, pos) {
    var e = cur.e, P = S.data.players;
    var sp = pos[e.p]; if (!sp) return;
    var s = proj(sp[0], sp[1]);
    var hoop = toCourt(e.team, e.q, 5.25, 25), h = proj(hoop[0], hoop[1]); var hx = h[0], hy = h[1] - 34;
    var f = (u - 0.42) / 0.33;
    if (f < 0 && e.ast != null && pos[e.ast] && u < 0.3) {
      var a0 = proj(pos[e.ast][0], pos[e.ast][1]), g0 = ease(u / 0.3);
      drawBall(ctx, lerp(a0[0] + 12, s[0] + 12, g0), lerp(a0[1] - 14, s[1] - 14, g0) - Math.sin(Math.PI * g0) * 30, s[2]);
    }
    else if (f < 0) drawBall(ctx, s[0] + 12, s[1] - 14, s[2]);
    else if (f <= 1) {
      var x = lerp(s[0], hx, f), y = lerp(s[1] - 14, hy, f) - Math.sin(Math.PI * f) * (110 + 2 * Math.abs(s[0] - hx) / 10);
      drawBall(ctx, x, y, lerp(s[2], 1, f));
    } else {
      var g2 = Math.min(1, (u - 0.75) / 0.25);
      if (e.made) drawBall(ctx, hx, hy + 30 * g2, 1);
      else drawBall(ctx, hx + (e.blk != null ? -40 : 28) * g2, hy - Math.sin(Math.PI * g2) * 40 + 40 * g2, 1);
      if (e.made && g2 < 0.6) { ctx.strokeStyle = hexA('#95e04a', 1 - g2 / 0.6); ctx.lineWidth = 4; ctx.beginPath(); ctx.ellipse(hx, hy, 18 + 40 * g2, 8 + 18 * g2, 0, 0, 2 * Math.PI); ctx.stroke(); }
    }
    if (u > 0.75) {   // issue au-dessus du cercle
      var g3 = Math.min(1, (u - 0.75) / 0.25);
      ctx.globalAlpha = 1 - 0.3 * g3; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.font = 'italic 800 ' + (e.made ? 54 : 30) + 'px ' + SANS;
      ctx.fillStyle = e.made ? C.acc : (e.blk != null ? C.loss : C.mu);
      ctx.fillText(e.made ? '+' + e.v : (e.blk != null ? 'CONTRÉ' : 'RATÉ'), hx, hy - 60 - 30 * g3);
      ctx.globalAlpha = 1;
    }
    callout(S, ctx, e.p, s[0], s[1], (e.lab || '').toUpperCase(), Math.min(1, u / 0.15));
    if (e.blk != null && u > 0.6 && pos[e.blk]) { var bq = proj(pos[e.blk][0], pos[e.blk][1]); callout(S, ctx, e.blk, bq[0], bq[1] + 50, 'CONTRE', 1, true); }
  }
  function drawScoreboard(S, ctx, cur, idx, ua, ux) {
    var D = S.data, m = D.meta, e = cur.e;
    var prevS = idx > 0 ? S.tl[idx - 1].e.s : [0, 0];
    var sc = (cur.big && ((e.k === 'shot' && ux < 0.75) || (e.k === 'ft' && ux < 0.7))) ? prevS : e.s;
    var x0 = 560, w = 800, y0 = 26;
    ctx.fillStyle = C.panel; roundRect(ctx, x0, y0, w, 112, 14); ctx.fill();
    ctx.strokeStyle = C.line; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.fillStyle = S.colors[1]; ctx.fillRect(x0, y0 + 108, w / 2 - 110, 4);
    ctx.fillStyle = S.colors[0]; ctx.fillRect(x0 + w / 2 + 110, y0 + 108, w / 2 - 110, 4);
    ctx.textBaseline = 'middle';
    var fit = function (txt) { var px = 30; ctx.font = '700 30px ' + SANS; while (ctx.measureText(txt).width > 150 && px > 18) { px -= 2; ctx.font = '700 ' + px + 'px ' + SANS; } };
    ctx.fillStyle = C.tx; ctx.textAlign = 'left'; fit(m.away.short || m.away.abbr); ctx.fillText(m.away.short || m.away.abbr, x0 + 26, y0 + 42);
    ctx.textAlign = 'right'; fit(m.home.short || m.home.abbr); ctx.fillText(m.home.short || m.home.abbr, x0 + w - 26, y0 + 42);
    // Fautes d'equipe du quart-temps ; 5 fautes = l'adversaire tire des lancers a chaque faute (BONUS)
    ctx.font = '500 16px ' + MONO; ctx.fillStyle = C.mu; ctx.textAlign = 'left'; ctx.fillText('FAUTES ' + cur.fouls[1], x0 + 26, y0 + 82);
    ctx.textAlign = 'right'; ctx.fillText('FAUTES ' + cur.fouls[0], x0 + w - 26, y0 + 82);
    ctx.font = '700 14px ' + MONO; ctx.fillStyle = C.acc;
    if (cur.fouls[0] >= 5) { ctx.textAlign = 'left'; ctx.fillText('BONUS', x0 + 150, y0 + 82); }
    if (cur.fouls[1] >= 5) { ctx.textAlign = 'right'; ctx.fillText('BONUS', x0 + w - 150, y0 + 82); }
    ctx.fillStyle = C.tx; ctx.font = '700 64px ' + SANS; ctx.textAlign = 'right'; ctx.fillText(sc[1], x0 + w / 2 - 128, y0 + 58);
    ctx.textAlign = 'left'; ctx.fillText(sc[0], x0 + w / 2 + 128, y0 + 58);
    ctx.fillStyle = '#060807'; ctx.fillRect(x0 + w / 2 - 110, y0, 220, 112);
    var gnow = lerp(cur.g0, cur.g, ua);
    var clock = Math.min(e.q <= 4 ? 720 : 300, e.c + (cur.g - gnow));
    ctx.fillStyle = C.tx; ctx.textAlign = 'center'; ctx.font = '700 30px ' + MONO;
    ctx.fillText((e.q <= 4 ? 'Q' + e.q : 'PROL') + ' ' + fmtClock(clock), x0 + w / 2, y0 + 44);
    var poss = cur.team === 0 ? m.home.abbr : m.away.abbr;
    var shot = Math.max(0, Math.min(24, cur.sc0 - (gnow - cur.ps)));
    var showSc = e.k !== 'end' && e.k !== 'ft' && clock > shot;
    ctx.font = '600 15px ' + MONO; ctx.fillStyle = C.acc;
    ctx.fillText('BALLE ' + poss + (showSc ? '  ·  TIR ' + Math.ceil(shot) : ''), x0 + w / 2, y0 + 84);
    // Probabilite de victoire : etiquettes + courbe
    var y1 = y0 + 128;
    ctx.fillStyle = C.panel; roundRect(ctx, x0, y1, w, 62, 12); ctx.fill(); ctx.strokeStyle = C.line; ctx.stroke();
    var wp = e.wp;
    ctx.font = '700 30px ' + SANS; ctx.textAlign = 'left'; ctx.fillStyle = S.colors[1]; ctx.fillText(pct(1 - wp, 0), x0 + 20, y1 + 32);
    // Seconde valeur = 100 - premiere (sinon l'arrondi donne parfois 51 % + 50 %)
    ctx.textAlign = 'right'; ctx.fillStyle = S.colors[0]; ctx.fillText((100 - Math.round(100 * (1 - wp))) + ' %', x0 + w - 20, y1 + 32);
    ctx.font = '600 11px ' + MONO; ctx.fillStyle = C.di; ctx.textAlign = 'center'; ctx.fillText('PROBABILITÉ DE VICTOIRE', x0 + w / 2, y1 + 11);
    var cx0 = x0 + 140, cw = w - 280;
    ctx.strokeStyle = C.line; ctx.lineWidth = 1; ctx.setLineDash([3, 5]); ctx.beginPath(); ctx.moveTo(cx0, y1 + 40); ctx.lineTo(cx0 + cw, y1 + 40); ctx.stroke(); ctx.setLineDash([]);
    ctx.lineWidth = 2.5; ctx.beginPath();
    for (var i = 0; i <= idx; i += Math.max(1, Math.floor(idx / 300))) {
      var it = S.tl[i]; var gx = cx0 + cw * Math.min(1, it.g / 2880), gy = y1 + 57 - 34 * it.e.wp;
      i ? ctx.lineTo(gx, gy) : ctx.moveTo(gx, gy);
    }
    ctx.strokeStyle = C.acc; ctx.stroke();
  }
  function drawBox(S, ctx, cur, team, x, align) {
    var D = S.data, P = D.players, B = {}, w = 430, x0 = align === 'left' ? x : x - w, y0 = 250;
    S.cols.forEach(function (c, i) { B[c] = cur.box[i]; });
    var mine = P.map(function (p, j) { return j; }).filter(function (j) { return P[j].team === team && (B.MIN[j] > 0 || cur.on.indexOf(j) >= 0); });
    var starters = D.starters.filter(function (j) { return P[j].team === team; });
    var bench = mine.filter(function (j) { return starters.indexOf(j) < 0; }).sort(function (a, b) { return B.MIN[b] - B.MIN[a]; }).slice(0, 6);
    var rows = starters.concat(bench), h = 70 + rows.length * 30 + 36;
    ctx.fillStyle = C.panel; roundRect(ctx, x0, y0, w, h, 12); ctx.fill(); ctx.strokeStyle = C.line; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.fillStyle = S.colors[team]; ctx.fillRect(x0, y0, w, 4);
    var tm = team === 0 ? D.meta.home : D.meta.away;
    ctx.fillStyle = C.tx; ctx.font = '700 24px ' + SANS; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(tm.short || tm.abbr, x0 + 18, y0 + 30);
    var cx = [200, 245, 300, 350, 390, 418], hd = ['MIN', 'PTS', 'TIRS', 'REB', 'PD', 'F'];
    ctx.font = '600 13px ' + MONO; ctx.fillStyle = C.di; ctx.textAlign = 'right';
    hd.forEach(function (s, i) { ctx.fillText(s, x0 + cx[i], y0 + 32); });
    var y = y0 + 64;
    rows.forEach(function (j, r) {
      if (r === starters.length) { ctx.fillStyle = C.di; ctx.font = '600 12px ' + MONO; ctx.textAlign = 'left'; ctx.fillText('BANC', x0 + 18, y + 4); y += 22; }
      var onc = cur.on.indexOf(j) >= 0;
      if (onc) { ctx.fillStyle = S.colors[team]; ctx.fillRect(x0 + 6, y - 11, 4, 22); }
      if (cur.e.k === 'sub' && (cur.e['in'].indexOf(j) >= 0 || cur.e.out.indexOf(j) >= 0)) {
        var isIn = cur.e['in'].indexOf(j) >= 0;
        ctx.fillStyle = isIn ? C.acc : C.di; roundRect(ctx, x0 + 12, y - 9, 28, 18, 4); ctx.fill();
        ctx.fillStyle = '#0b1208'; ctx.font = '800 11px ' + MONO; ctx.textAlign = 'center'; ctx.fillText(isIn ? 'IN' : 'OUT', x0 + 26, y + 1);
      }
      ctx.globalAlpha = onc ? 1 : 0.55;
      ctx.fillStyle = C.mu; ctx.font = '500 14px ' + MONO; ctx.textAlign = 'right'; ctx.fillText(P[j].num || '', x0 + 40, y + 1);
      ctx.fillStyle = C.tx; ctx.font = '600 19px ' + SANS; ctx.textAlign = 'left'; ctx.fillText(P[j].short, x0 + 48, y + 1);
      ctx.font = '500 17px ' + MONO; ctx.textAlign = 'right';
      var vals = [Math.round(B.MIN[j]), B.PTS[j], B.FGM[j] + '-' + B.FGA[j], B.REB[j], B.AST[j], B.PF[j]];
      vals.forEach(function (v, i) { ctx.fillStyle = i === 1 ? C.tx : C.mu; ctx.font = (i === 1 ? '700 ' : '500 ') + '17px ' + MONO; ctx.fillText(v, x0 + cx[i], y + 1); });
      ctx.globalAlpha = 1;
      y += 30;
    });
  }
  function drawTicker(S, ctx, cur, u) {
    var e = cur.e, D = S.data, y = 950, x0 = 300, w = 1320;
    if (e.k === 'end') {
      ctx.fillStyle = C.acc; roundRect(ctx, 760, y, 400, 70, 12); ctx.fill();
      ctx.fillStyle = '#0b1208'; ctx.font = '800 30px ' + SANS; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(e.txt.toUpperCase(), 960, y + 36);
      return;
    }
    var team = e.team != null ? e.team : 0, tm = team === 0 ? D.meta.home : D.meta.away;
    ctx.fillStyle = C.panel; roundRect(ctx, x0, y, w, 70, 12); ctx.fill(); ctx.strokeStyle = C.line; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.fillStyle = S.colors[team]; ctx.fillRect(x0, y, 6, 70);
    ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
    ctx.font = 'italic 800 30px ' + SANS; ctx.fillStyle = C.acc; var lab = 'BALLE ' + (tm.short || tm.abbr).toUpperCase();
    ctx.fillText(lab, x0 + 24, y + 36);
    var lw = ctx.measureText(lab).width;
    ctx.font = '500 20px ' + SANS; ctx.fillStyle = C.mu; ctx.fillText(e.ctx || '', x0 + 36 + lw, y + 37);
    var tx = x0 + 60 + lw + ctx.measureText(e.ctx || '').width;
    ctx.fillStyle = C.line; ctx.fillRect(tx - 14, y + 14, 2, 42);
    ctx.font = '600 25px ' + SANS; ctx.fillStyle = C.tx;
    var txt = e.txt, maxw = x0 + w - tx - (e.badge ? 190 : 30);
    while (ctx.measureText(txt).width > maxw && txt.length > 10) txt = txt.slice(0, -2);
    if (txt !== e.txt) txt += '…';
    ctx.fillText(txt, tx, y + 37);
    if (e.badge && cur.big) {
      var bad = ['CONTRE', 'INTERCEPTION', 'EXCLU', 'FAUTE OFF.'].indexOf(e.badge) >= 0;
      ctx.font = 'italic 800 24px ' + SANS; var bw = ctx.measureText(e.badge).width + 28;
      ctx.fillStyle = bad ? C.loss : C.acc; roundRect(ctx, x0 + w - bw - 16, y + 16, bw, 38, 6); ctx.fill();
      ctx.fillStyle = bad ? '#fff' : '#0b1208'; ctx.textAlign = 'center'; ctx.fillText(e.badge, x0 + w - 16 - bw / 2, y + 36);
    }
  }
  function drawIntro(S, ctx, t) {
    var D = S.data, m = D.meta, a = t < DUR.intro - 0.6 ? 1 : (DUR.intro - t) / 0.6;
    ctx.globalAlpha = Math.max(0, a) * 0.92; ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, H); ctx.globalAlpha = Math.max(0, a);
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = '600 22px ' + MONO; ctx.fillStyle = C.acc; ctx.fillText('MATCH TYPE · ' + fmtN(D.summary.n) + ' SIMULATIONS', 960, 380);
    ctx.font = '700 92px ' + SANS; ctx.fillStyle = C.tx; ctx.fillText((m.away.short || m.away.abbr) + '  @  ' + (m.home.short || m.home.abbr), 960, 480);
    ctx.font = '500 26px ' + SANS; ctx.fillStyle = C.mu;
    var sc = m.scenario;
    if (sc) {
      var win = sc.home_wins ? m.home : m.away;
      ctx.font = '700 34px ' + SANS; ctx.fillStyle = S.colors[sc.home_wins ? 0 : 1];
      ctx.fillText('Scénario : victoire ' + (win.short || win.abbr) + ' · ' + pct(sc.p, 0) + ' des simulations', 960, 580);
      ctx.font = '500 26px ' + SANS; ctx.fillStyle = C.mu;
      ctx.fillText('Parmi ces simulations, celle au score le plus proche de leur médiane, chaque joueur proche de sa moyenne.', 960, 630);
    } else {
      ctx.fillText('Le vainqueur choisi par la majorité des simulations, un score proche de la médiane,', 960, 580);
      ctx.fillText('chaque joueur proche de sa moyenne.', 960, 618);
    }
    ctx.globalAlpha = 1;
  }
  function drawQuarters(S, ctx, x0, y0, upto, total, alpha) {
    var D = S.data, m = D.meta, Q = S.quarters.slice(0, upto), n = Q.length;
    var cw = 92, w = 260 + cw * (n + 1), h = 190;
    x0 = 960 - w / 2;
    ctx.globalAlpha = alpha == null ? 1 : alpha;
    ctx.fillStyle = 'rgba(10,13,11,0.94)'; roundRect(ctx, x0, y0, w, h, 16); ctx.fill(); ctx.strokeStyle = C.acc; ctx.lineWidth = 2; ctx.stroke();
    ctx.textBaseline = 'middle'; ctx.font = '600 18px ' + MONO; ctx.fillStyle = C.di; ctx.textAlign = 'center';
    Q.forEach(function (q, i) { ctx.fillText(i < 4 ? 'Q' + (i + 1) : 'P' + (i - 3), x0 + 240 + cw * i + cw / 2, y0 + 36); });
    ctx.fillStyle = C.acc; ctx.fillText('TOTAL', x0 + 240 + cw * n + cw / 2, y0 + 36);
    [[1, m.away], [0, m.home]].forEach(function (r, k) {
      var y = y0 + 88 + k * 62, team = r[0];
      ctx.fillStyle = S.colors[team]; ctx.fillRect(x0 + 24, y - 18, 6, 36);
      ctx.textAlign = 'left'; ctx.fillStyle = C.tx; ctx.font = '700 30px ' + SANS; ctx.fillText(r[1].short || r[1].abbr, x0 + 44, y);
      ctx.textAlign = 'center'; ctx.font = '600 30px ' + MONO;
      Q.forEach(function (q, i) {
        var mine = q[team], theirs = q[1 - team];
        ctx.fillStyle = mine > theirs ? C.tx : C.mu; ctx.fillText(mine, x0 + 240 + cw * i + cw / 2, y);
      });
      ctx.fillStyle = C.tx; ctx.font = '800 32px ' + MONO; ctx.fillText(total[team], x0 + 240 + cw * n + cw / 2, y);
    });
    ctx.globalAlpha = 1;
  }
  function drawFinalBox(S, ctx, t) {
    var D = S.data, P = D.players, last = S.tl[S.tl.length - 1], B = {};
    S.cols.forEach(function (c, i) { B[c] = last.box[i]; });
    var al = Math.min(1, t / 0.5);
    ctx.globalAlpha = al; ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, H);
    [[1, 50], [0, 990]].forEach(function (tt) {
      var team = tt[0], x0 = tt[1], w = 880, tm = team === 0 ? D.meta.home : D.meta.away;
      ctx.fillStyle = C.tx; ctx.font = '700 40px ' + SANS; ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic'; ctx.fillText(tm.name || tm.abbr, x0, 120);
      ctx.font = '700 64px ' + SANS; ctx.textAlign = 'right'; ctx.fillText(D.final[team], x0 + w, 124);
      ctx.fillStyle = S.colors[team]; ctx.fillRect(x0, 140, w, 4);
      var cols = [['MIN', 330], ['PTS', 400], ['TIRS', 490], ['3PTS', 570], ['LF', 645], ['REB', 715], ['PD', 775], ['INT', 830], ['CTR', 880]];
      ctx.font = '600 15px ' + MONO; ctx.fillStyle = C.di;
      cols.forEach(function (c) { ctx.fillText(c[0], x0 + c[1], 176); });
      var rows = P.map(function (p, j) { return j; }).filter(function (j) { return P[j].team === team && B.MIN[j] >= 0.5; })
        .sort(function (a, b) { var sa = D.starters.indexOf(a) >= 0, sb = D.starters.indexOf(b) >= 0; return sa !== sb ? (sb - sa) : B.MIN[b] - B.MIN[a]; }).slice(0, 13);
      var y = 214, tot = {};
      rows.forEach(function (j) {
        ctx.textAlign = 'left'; ctx.fillStyle = C.mu; ctx.font = '500 16px ' + MONO; ctx.fillText(P[j].num || '', x0, y);
        ctx.fillStyle = C.tx; ctx.font = '600 22px ' + SANS; ctx.fillText(P[j].short, x0 + 40, y);
        var v = [Math.round(B.MIN[j]), B.PTS[j], B.FGM[j] + '-' + B.FGA[j], B.TPM[j] + '-' + B.TPA[j], B.FTM[j] + '-' + B.FTA[j], B.REB[j], B.AST[j], B.STL[j], B.BLK[j]];
        ctx.textAlign = 'right';
        v.forEach(function (x, i) { ctx.fillStyle = i === 1 ? C.tx : C.mu; ctx.font = (i === 1 ? '700 ' : '500 ') + '20px ' + MONO; ctx.fillText(x, x0 + cols[i][1], y); });
        y += 40;
      });
    });
    if (S.quarters.length) drawQuarters(S, ctx, 0, 800, S.quarters.length, D.final, al);
    ctx.textAlign = 'center'; ctx.fillStyle = C.di; ctx.font = '500 17px ' + MONO;
    ctx.fillText('Match simulé, pas un résultat réel · construit à partir du play-by-play 2025-27', 960, H - 30);
    ctx.globalAlpha = 1;
  }
  function drawSummary(S, ctx, t) {
    var D = S.data, m = D.meta, sm = D.summary;
    ctx.globalAlpha = Math.min(1, t / 0.6);
    ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, H);
    ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = C.mu; ctx.font = '600 30px ' + SANS; ctx.fillText('Sur l’ensemble des ' + fmtN(sm.n) + ' simulations', 960, 300);
    var items = [[sm.p_home, (m.home.short || m.home.abbr) + ' gagne', S.colors[0]], [1 - sm.p_home, (m.away.short || m.away.abbr) + ' gagne', S.colors[1]]];
    if (sm.p_over != null) items.push([sm.p_over, 'Plus de ' + sm.total_line + ' pts', C.tx]);
    items.forEach(function (it, i) {
      var x = 960 + (i - (items.length - 1) / 2) * 520, num = (100 * it[0]).toFixed(1).replace('.', ',');
      ctx.font = '700 140px ' + SANS; var wn = ctx.measureText(num).width;
      ctx.font = '700 70px ' + SANS; var wp = ctx.measureText(' %').width;
      var left = x - (wn + wp) / 2;
      ctx.textAlign = 'left';
      ctx.fillStyle = C.tx; ctx.font = '700 140px ' + SANS; ctx.fillText(num, left, 470);
      ctx.fillStyle = it[2]; ctx.font = '700 70px ' + SANS; ctx.fillText(' %', left + wn, 470);
      ctx.textAlign = 'center'; ctx.font = '700 34px ' + SANS; ctx.fillText(it[1], x, 540);
    });
    ctx.fillStyle = C.mu; ctx.font = '500 30px ' + SANS;
    ctx.fillText('Score moyen : ' + (m.home.short || m.home.abbr) + ' ' + String(sm.avg[0]).replace('.', ',') + ', ' + (m.away.short || m.away.abbr) + ' ' + String(sm.avg[1]).replace('.', ','), 960, 650);
    if (sm.books_p_home != null) { ctx.fillStyle = C.di; ctx.fillText('Les bookmakers avant le match : ' + (m.home.short || m.home.abbr) + ' ' + pct(sm.books_p_home, 0), 960, 700); }
    ctx.fillStyle = C.tx; ctx.font = '700 56px ' + SANS; ctx.fillText('Sports', 960 - 50, 860); ctx.fillStyle = C.acc; ctx.textAlign = 'left'; ctx.fillText('Picks', 960 - 50 + ctx.measureText('Sports').width / 2 + 2, 860);
    ctx.globalAlpha = 1;
  }
  function roundRect(ctx, x, y, w, h, r) { ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath(); }

  /* ── Lecteur (UI) ───────────────────────────────────────────────────── */
  function open(host, data, fileName) {
    var S = prepare(data, 'full');
    host.innerHTML = '';
    var wrap = document.createElement('div'); wrap.className = 'rp';
    var cv = document.createElement('canvas'); cv.width = W; cv.height = H; cv.className = 'rp-cv';
    var bar = document.createElement('div'); bar.className = 'rp-bar';
    bar.innerHTML = '<button class="rp-b" data-a="play">▶</button><input class="rp-seek" type="range" min="0" max="1000" value="0">' +
      '<span class="rp-time">0:00</span><button class="rp-b" data-a="speed">1×</button>' +
      '<button class="rp-b" data-a="mode" title="Match complet ou résumé">Résumé 3 min</button>' +
      '<button class="rp-b" data-a="fs" title="Plein écran">⛶</button>' +
      '<button class="rp-b rp-dl" data-a="dl">Télécharger MP4</button><span class="rp-msg"></span>';
    wrap.appendChild(cv); wrap.appendChild(bar); host.appendChild(wrap);
    var ctx = cv.getContext('2d'), t = 0, playing = false, speed = 1, last = 0, raf = 0;
    var btn = bar.querySelector('[data-a=play]'), seek = bar.querySelector('.rp-seek'), tm = bar.querySelector('.rp-time'), msg = bar.querySelector('.rp-msg');
    function fmt(s) { var m = Math.floor(s / 60), x = Math.floor(s % 60); return m + ':' + (x < 10 ? '0' : '') + x; }
    function render() { draw(S, ctx, t); seek.value = Math.round(1000 * t / S.total); tm.textContent = fmt(t) + ' / ' + fmt(S.total); }
    function loop(now) { if (!playing) return; var dt = (now - last) / 1000; last = now; t = Math.min(S.total, t + dt * speed); render(); if (t >= S.total) { playing = false; btn.textContent = '↺'; return; } raf = requestAnimationFrame(loop); }
    bar.addEventListener('click', function (ev) {
      var a = ev.target.getAttribute('data-a'); if (!a) return;
      if (a === 'play') { if (t >= S.total) t = 0; playing = !playing; btn.textContent = playing ? '❚❚' : '▶'; if (playing) { last = performance.now(); raf = requestAnimationFrame(loop); } }
      if (a === 'speed') { speed = speed === 1 ? 2 : speed === 2 ? 4 : 1; ev.target.textContent = speed + '×'; }
      if (a === 'mode') {
        S = prepare(data, S.mode === 'full' ? 'resume' : 'full'); t = 0; render();
        ev.target.textContent = S.mode === 'full' ? 'Résumé 3 min' : 'Match complet';
      }
      if (a === 'dl') exportMp4(S, ev.target, msg, fileName);
      if (a === 'fs') { var el = cv; (el.requestFullscreen || el.webkitRequestFullscreen || function () {}).call(el); }
    });
    seek.addEventListener('input', function () { t = S.total * seek.value / 1000; render(); });
    (document.fonts ? document.fonts.ready : Promise.resolve()).then(render);
    return { draw: function (tt) { draw(S, ctx, tt); }, total: S.total,
             stop: function () { playing = false; cancelAnimationFrame(raf); } };
  }

  /* ── Export MP4 (WebCodecs + mp4-muxer), repli WebM temps reel ──────── */
  var MUXER_URL = 'https://cdn.jsdelivr.net/npm/mp4-muxer@5.1.3/build/mp4-muxer.min.js';
  function loadScript(src) { return new Promise(function (ok, ko) { var s = document.createElement('script'); s.src = src; s.onload = ok; s.onerror = ko; document.head.appendChild(s); }); }
  function save(blob, name) { var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 2000); }
  async function exportMp4(S, btn, msg, fileName) {
    btn.disabled = true;
    var cv = document.createElement('canvas'); cv.width = W; cv.height = H; var ctx = cv.getContext('2d');
    try {
      if (!window.VideoEncoder) throw new Error('webcodecs');
      if (!window.Mp4Muxer) await loadScript(MUXER_URL);
      var codecs = ['avc1.640028', 'avc1.4d0028', 'avc1.42E028'], cfg = null;
      for (var i = 0; i < codecs.length && !cfg; i++) {
        var c = { codec: codecs[i], width: W, height: H, bitrate: S.total > 300 ? 2500000 : 6000000, framerate: FPS };
        var sup = await VideoEncoder.isConfigSupported(c); if (sup.supported) cfg = c;
      }
      if (!cfg) throw new Error('codec');
      var muxer = new Mp4Muxer.Muxer({ target: new Mp4Muxer.ArrayBufferTarget(), video: { codec: 'avc', width: W, height: H }, fastStart: 'in-memory' });
      var enc = new VideoEncoder({ output: function (chunk, meta) { muxer.addVideoChunk(chunk, meta); }, error: function (e) { throw e; } });
      enc.configure(cfg);
      var nf = Math.ceil(S.total * FPS);
      for (var f = 0; f < nf; f++) {
        draw(S, ctx, f / FPS);
        var vf = new VideoFrame(cv, { timestamp: Math.round(f * 1e6 / FPS), duration: Math.round(1e6 / FPS) });
        enc.encode(vf, { keyFrame: f % (FPS * 2) === 0 }); vf.close();
        // Attendre que l'encodeur rattrape : sinon des milliers d'images 1080p s'empilent en memoire
        while (enc.encodeQueueSize > 4) await new Promise(function (r) { setTimeout(r, 4); });
        if (f % 30 === 0) { msg.textContent = 'Encodage ' + Math.round(100 * f / nf) + ' %'; await new Promise(function (r) { setTimeout(r, 0); }); }
      }
      await enc.flush(); muxer.finalize();
      save(new Blob([muxer.target.buffer], { type: 'video/mp4' }), fileName + '.mp4');
      msg.textContent = 'Vidéo MP4 prête';
    } catch (err) {
      if (!window.MediaRecorder) { msg.textContent = 'Export vidéo non pris en charge par ce navigateur'; btn.disabled = false; return; }
      msg.textContent = 'Enregistrement en temps réel (' + Math.round(S.total) + ' s)…';
      var mime = MediaRecorder.isTypeSupported('video/mp4') ? 'video/mp4' : 'video/webm';
      var rec = new MediaRecorder(cv.captureStream(FPS), { mimeType: mime, videoBitsPerSecond: S.total > 300 ? 2500000 : 6000000 }), parts = [];
      rec.ondataavailable = function (e) { parts.push(e.data); };
      rec.onstop = function () { save(new Blob(parts, { type: mime }), fileName + (mime === 'video/mp4' ? '.mp4' : '.webm')); msg.textContent = 'Vidéo prête'; };
      rec.start(); var t0 = performance.now();
      await new Promise(function (done) { (function tick() { var tt = (performance.now() - t0) / 1000; draw(S, ctx, Math.min(tt, S.total)); if (tt < S.total) requestAnimationFrame(tick); else { rec.stop(); done(); } })(); });
    }
    btn.disabled = false;
  }

  window.SPReplay = { open: open, prepare: prepare, draw: draw, W: W, H: H };
})();
