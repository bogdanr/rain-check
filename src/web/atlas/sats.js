/* Weather satellites on the Atlas globe: a 2-D canvas over the WebGL stage.
 *
 * Twelve real satellites (src/satellites.py says which and why), in the two
 * orbit families every forecast in the audit is built on:
 *
 *   geostationary  one ring over the equator, each satellite fixed over its
 *                  longitude, so it turns with the Earth. Really 35,786 km
 *                  up - 6.6 Earth radii from the centre - which would put the
 *                  ring far outside the window, so it is drawn at GEO_R and
 *                  every place that shows it says so. GEO_R sits well clear
 *                  of the low orbits (at most ~1.13 R) so the two families
 *                  never read as one shell.
 *   polar          low orbits at true scale (about 820 km, an eighth of a
 *                  radius): the sun-synchronous sounders in their three
 *                  planes and GPM's inclined rain-radar orbit. Each shows the
 *                  last few minutes of its path.
 *
 * Orbits are drawn only for the satellite under the pointer (the full low
 * orbit, or the geostationary ring), so twelve orbits never clutter the
 * globe the cities are read from.
 *
 * Positions come from CelesTrak mean elements (OMM), propagated here as a
 * Kepler orbit with the secular J2 drift of the node and perigee and the
 * catalogue's drag term. That is not SGP4, and does not need to be: with
 * elements a day old it is within a few tens of km, under a pixel at globe
 * scale. Past STALE days the position is no longer trusted and only the
 * orbit is drawn (a sun-synchronous plane stays where J2 puts it; the phase
 * along it does not).
 *
 * Like the lightning, it is its own layer so it never makes the stage
 * redraw: it repaints when the camera moves and once a second otherwise
 * (low orbits cross a pixel every few seconds). Reduced motion: the clock
 * stops at load and only the camera redraws it.
 *
 * Hover (and tap) shows a satellite's card. Cities keep priority: a city
 * within reach of the pointer always wins, so no satellite hides a click. */
(function (global) {
  'use strict';
  var DEG = Math.PI / 180, TAU = 2 * Math.PI;
  var MU = 398600.4418, RE = 6378.137, J2 = 1.08262668e-3;
  // Drawn radius of the geostationary ring, in Earth radii (really ~6.61).
  var GEO_R = 1.6;
  var STALE_D = { geo: 60, sso: 10, leo: 7 };        // days before a position is withdrawn
  var TRAIL_S = 14 * 60, RING_N = 120;

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function gmst(t) { return (280.46061837 + 360.98564736629 * (t / 86400000 + 2440587.5 - 2451545.0)) % 360 * DEG; }

  /* OMM mean elements -> what the propagator needs, in km, rad and seconds. */
  function compile(omm) {
    var n = omm.MEAN_MOTION * TAU / 86400, e = omm.ECCENTRICITY, i = omm.INCLINATION * DEG;
    var a = Math.pow(MU / (n * n), 1 / 3), p = a * (1 - e * e), k = 1.5 * J2 * (RE / p) * (RE / p) * n;
    var si = Math.sin(i);
    return {
      t0: Date.parse(String(omm.EPOCH).slice(0, 23) + 'Z'),
      n: n, a: a, e: e, i: i, ci: Math.cos(i), si: si,
      O0: omm.RA_OF_ASC_NODE * DEG, w0: omm.ARG_OF_PERICENTER * DEG, M0: omm.MEAN_ANOMALY * DEG,
      // MEAN_MOTION_DOT is half the rate of change of n, in rev/day^2.
      nd: (omm.MEAN_MOTION_DOT || 0) * TAU / (86400 * 86400),
      Od: -k * Math.cos(i), wd: k * (2 - 2.5 * si * si)
    };
  }

  /* Inertial position (km) at time t (ms), plus the node and the argument of latitude. */
  function eci(el, t) {
    var dt = (t - el.t0) / 1000;
    var M = el.M0 + el.n * dt + el.nd * dt * dt, O = el.O0 + el.Od * dt, w = el.w0 + el.wd * dt;
    var E = M, e = el.e;
    for (var j = 0; j < 8; j++) E -= (E - e * Math.sin(E) - M) / (1 - e * Math.cos(E));
    var r = el.a * (1 - e * Math.cos(E));
    var nu = 2 * Math.atan2(Math.sqrt(1 + e) * Math.sin(E / 2), Math.sqrt(1 - e) * Math.cos(E / 2));
    var u = w + nu, cu = Math.cos(u), su = Math.sin(u), cO = Math.cos(O), sO = Math.sin(O);
    return { x: r * (cO * cu - sO * su * el.ci), y: r * (sO * cu + cO * su * el.ci), z: r * su * el.si, r: r, O: O, u: u };
  }

  /* Inertial -> the stage's world frame (x = lon 90 east, y = north, z = lon 0),
   * turned by the sidereal angle g, scaled by s. */
  function world(p, g, s) {
    var cg = Math.cos(g), sg = Math.sin(g);
    var xe = p.x * cg + p.y * sg, ye = -p.x * sg + p.y * cg;
    return [ye * s, p.z * s, xe * s];
  }

  /* World -> view rotation, as stage.js w2v(). */
  function w2v(c) {
    var l0 = c.lon * DEG, p0 = c.lat * DEG, cl = Math.cos(l0), sl = Math.sin(l0), cp = Math.cos(p0), sp = Math.sin(p0);
    return [[cl, 0, -sl], [-sp * sl, cp, -sp * cl], [cp * sl, sp, cp * cl]];
  }

  function rgb(css, fb) {
    var s = String(css || '').trim(), m;
    if (/^#[0-9a-f]{6}$/i.test(s)) return [parseInt(s.slice(1, 3), 16), parseInt(s.slice(3, 5), 16), parseInt(s.slice(5, 7), 16)];
    if ((m = /rgba?\(([^)]+)\)/.exec(s))) { var q = m[1].split(/[ ,\/]+/); return [+q[0], +q[1], +q[2]]; }
    return fb;
  }

  function Sats(canvas, stage, opts) {
    this.c = canvas; this.st = stage; this.g = canvas.getContext('2d'); this.opts = opts || {};
    this.list = []; this.src = null; this.on = false; this.live = false; this.raf = 0;
    this.hot = null; this.pts = []; this._key = '';
    this.reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.t0 = this._clock();
    var cs = getComputedStyle(document.documentElement);
    this.col = { geo: rgb(cs.getPropertyValue('--sat-geo'), [255, 196, 120]), leo: rgb(cs.getPropertyValue('--sat-leo'), [140, 255, 214]) };
    canvas._sats = this;                 // for the browser checks
    if (this.opts.hit) this._bindHover(this.opts.hit);
  }

  Sats.prototype.GEO_R = GEO_R;

  Sats.prototype._clock = function () { return this.opts.now ? this.opts.now() : Date.now(); };

  /* The instant drawn: the reader's clock, frozen at load under reduced motion. */
  Sats.prototype.time = function () { return this.reduce ? this.t0 : this._clock(); };

  /* The roster with its elements. src: 'live' (fetched from CelesTrak just
   * now) or 'baked' (the copy the site was built with). */
  Sats.prototype.setData = function (sats, src) {
    this.list = (sats || []).filter(function (s) { return s && s.omm; }).map(function (s) {
      return { s: s, el: compile(s.omm), kind: s.kind };
    });
    this.src = src; this._key = ''; this._kick();
  };

  Sats.prototype.show = function (on, live) { this.on = !!on; this.live = !!live; this._key = ''; if (!this._active()) this._unhover(); this._kick(); };

  Sats.prototype._active = function () { return this.on && this.live && this.list.length && !this.st.failed; };

  Sats.prototype.stale = function (x, t) { return Math.abs(t - x.el.t0) > (STALE_D[x.kind] || 7) * 86400000; };

  /* How many satellites have a trusted position right now. */
  Sats.prototype.fresh = function () {
    var t = this.time(), self = this;
    return this.list.filter(function (x) { return !self.stale(x, t); }).length;
  };

  /* Oldest element epoch in use (ms). */
  Sats.prototype.oldest = function () {
    return this.list.reduce(function (m, x) { return Math.min(m, x.el.t0); }, Infinity);
  };

  Sats.prototype._kick = function () {
    var self = this;
    if (!this._active()) {
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = 0; this._clear(); return;
    }
    if (this.raf) return;
    this.raf = requestAnimationFrame(function f() {
      if (!self._active()) { self.raf = 0; self._clear(); return; }
      self._frame();
      self.raf = requestAnimationFrame(f);
    });
  };

  Sats.prototype._clear = function () {
    this.g.clearRect(0, 0, this.c.width, this.c.height);
    this.c.dataset.drawn = '0'; this.c.dataset.orbit = ''; this.pts = [];
  };

  Sats.prototype._frame = function () {
    var cam = this.st.cam, dpr = Math.min(devicePixelRatio || 1, 1.5), t = this.time();
    var w = Math.round(innerWidth * dpr), h = Math.round(innerHeight * dpr);
    // Redraw for the camera, the hovered satellite, and once a second of the clock.
    var key = [cam.lon, cam.lat, cam.k, cam.cx, cam.cy, cam.dim].map(function (v) { return v.toFixed(3); }).join() +
      ',' + w + ',' + h + ',' + Math.floor(t / 1000) + ',' + this.hot;
    if (key === this._key) return;
    this._key = key;
    if (this.c.width !== w || this.c.height !== h) { this.c.width = w; this.c.height = h; }
    var g = this.g;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, w, h);
    this.pts = [];
    // Hidden behind the reading chapters and when docked, like the lightning.
    var fade = clamp((0.6 - cam.dim) / 0.25, 0, 1) * clamp((cam.k - 0.12) / 0.06, 0, 1);
    if (fade <= 0) { this.c.dataset.drawn = '0'; return; }
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._draw(g, t, fade);
  };

  Sats.prototype._draw = function (g, t, fade) {
    var cam = this.st.cam, M = w2v(cam), R = this.st._radius();
    var cx = cam.cx * innerWidth, cy = cam.cy * innerHeight, G = gmst(t), drawn = 0, self = this;
    // A world point -> [screen x, screen y, visibility]: 2 in front, 1 behind
    // the Earth but clear of its disc, 0 hidden by it.
    function scr(v) {
      var x = M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2];
      var y = M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2];
      var z = M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2];
      return [cx + x * R, cy - y * R, z >= 0 ? 2 : x * x + y * y > 1.0 ? 1 : 0];
    }
    function line(pts, col, a, width) {
      // One stroke per visibility class; the far side is fainter, the hidden part absent.
      [[2, 1], [1, 0.45]].forEach(function (cls) {
        g.beginPath();
        var pen = false;
        for (var i = 1; i < pts.length; i++) {
          var p = pts[i - 1], q = pts[i];
          if (Math.min(p[2], q[2]) !== cls[0]) { pen = false; continue; }
          if (!pen) g.moveTo(p[0], p[1]);
          g.lineTo(q[0], q[1]); pen = true;
        }
        g.strokeStyle = 'rgba(' + col + ',' + (a * cls[1] * fade).toFixed(3) + ')';
        g.lineWidth = width; g.stroke();
      });
    }
    var cG = this.col.geo.join(','), cL = this.col.leo.join(',');
    var hotX = null;
    this.list.forEach(function (x) { if (x.s.id === self.hot) hotX = x; });

    // The geostationary ring, shared by all six: only while one is hovered.
    if (hotX && hotX.kind === 'geo') {
      var ring = [];
      for (var k = 0; k <= RING_N; k++) {
        var lo = k / RING_N * TAU;
        ring.push(scr([Math.sin(lo) * GEO_R, 0, Math.cos(lo) * GEO_R]));
      }
      g.setLineDash([2, 4]);
      line(ring, cG, 0.75, 1);
      g.setLineDash([]);
      // Say it is not to scale where the ring shows beside the disc.
      var lab = null;
      ring.forEach(function (p) { if (p[2] === 2 && (!lab || p[0] > lab[0])) lab = p; });
      if (lab && lab[0] < innerWidth - 150) {
        g.font = '500 9.5px ui-monospace, SFMono-Regular, Menlo, monospace';
        g.fillStyle = 'rgba(' + cG + ',' + (0.6 * fade).toFixed(3) + ')';
        g.fillText('geostationary \u00b7 not to scale', lab[0] + 8, lab[1] + 3);
      }
    }
    this.c.dataset.orbit = hotX ? String(hotX.s.id) : '';

    this.list.forEach(function (x) {
      var el = x.el, geo = x.kind === 'geo', col = geo ? cG : cL, hot = x === hotX;
      var p = eci(el, t), s = geo ? GEO_R / p.r : 1 / RE;
      // Low orbits: the whole orbit while hovered; the last minutes of path always.
      if (!geo && hot) {
        var orb = [];
        var cO = Math.cos(p.O), sO = Math.sin(p.O);
        for (var k = 0; k <= RING_N; k++) {
          var u = k / RING_N * TAU, cu = Math.cos(u), su = Math.sin(u);
          orb.push(scr(world({ x: el.a * (cO * cu - sO * su * el.ci), y: el.a * (sO * cu + cO * su * el.ci), z: el.a * su * el.si }, G, 1 / RE)));
        }
        line(orb, col, 0.7, 1.2);
      }
      if (self.stale(x, t)) return;
      if (!geo) {
        var tr = [];
        for (var j = 20; j >= 0; j--) tr.push(scr(world(eci(el, t - TRAIL_S * j / 20), G, 1 / RE)));
        for (var q = 1; q < tr.length; q++) {
          if (!Math.min(tr[q - 1][2], tr[q][2])) continue;
          g.beginPath(); g.moveTo(tr[q - 1][0], tr[q - 1][1]); g.lineTo(tr[q][0], tr[q][1]);
          g.strokeStyle = 'rgba(' + col + ',' + (0.75 * q / tr.length * fade * (Math.min(tr[q - 1][2], tr[q][2]) === 2 ? 1 : 0.5)).toFixed(3) + ')';
          g.lineWidth = 1.6; g.stroke();
        }
      }
      var here = scr(world(p, G, s));
      if (!here[2]) return;
      // Which way it is flying, on screen: the solar panels lie across it.
      var ahead = scr(world(eci(el, t + (geo ? 1800 : 30) * 1000), G, s));
      var dx = ahead[0] - here[0], dy = ahead[1] - here[1], dl = Math.sqrt(dx * dx + dy * dy) || 1;
      var nx = -dy / dl, ny = dx / dl, a = (here[2] === 2 ? 1 : 0.55) * fade, big = hot ? 1.35 : 1;
      var glow = g.createRadialGradient(here[0], here[1], 0, here[0], here[1], 9 * big);
      glow.addColorStop(0, 'rgba(' + col + ',' + (0.55 * a).toFixed(3) + ')');
      glow.addColorStop(1, 'rgba(' + col + ',0)');
      g.fillStyle = glow;
      g.beginPath(); g.arc(here[0], here[1], 9 * big, 0, TAU); g.fill();
      g.strokeStyle = 'rgba(' + col + ',' + (0.95 * a).toFixed(3) + ')';
      g.lineWidth = 2;
      g.beginPath(); g.moveTo(here[0] - nx * 5.5 * big, here[1] - ny * 5.5 * big); g.lineTo(here[0] + nx * 5.5 * big, here[1] + ny * 5.5 * big); g.stroke();
      g.fillStyle = 'rgba(255,255,255,' + a.toFixed(3) + ')';
      g.beginPath(); g.arc(here[0], here[1], 2.1 * big, 0, TAU); g.fill();
      if (hot) {
        g.strokeStyle = 'rgba(255,255,255,' + (0.8 * a).toFixed(3) + ')'; g.lineWidth = 1;
        g.beginPath(); g.arc(here[0], here[1], 11, 0, TAU); g.stroke();
      }
      self.pts.push({ x: x, sx: here[0], sy: here[1] });
      drawn++;
    });
    this.c.dataset.drawn = String(drawn);
  };

  /* The drawn satellite nearest a screen point, within maxPx. */
  Sats.prototype.nearest = function (px, py, maxPx) {
    var best = null, bd = maxPx * maxPx;
    this.pts.forEach(function (p) {
      var d = (p.sx - px) * (p.sx - px) + (p.sy - py) * (p.sy - py);
      if (d < bd) { bd = d; best = p.x; }
    });
    return best;
  };

  /* What the card says about one satellite at time t. */
  Sats.prototype.facts = function (x, t) {
    var el = x.el, p = eci(el, t), G = gmst(t), v = world(p, G, 1 / p.r);
    var lat = Math.asin(clamp(v[1], -1, 1)) / DEG, lon = Math.atan2(v[0], v[2]) / DEG;
    var f = {
      h: p.r - RE, v: Math.sqrt(MU * (2 / p.r - 1 / el.a)), period: TAU / el.n / 60,
      lat: lat, lon: lon, age: t - el.t0, stale: this.stale(x, t)
    };
    if (x.kind === 'sso' && global.AtlasSky) {
      // Local solar time where the orbit crosses the equator going north.
      var sun = global.AtlasSky.bodies(t).sun.lon;
      f.ltan = ((12 + ((p.O - G) / DEG - sun) / 15) % 24 + 48) % 24;
    }
    return f;
  };

  function ll(lat, lon) {
    return Math.abs(lat).toFixed(1) + '\u00b0' + (lat >= 0 ? 'N' : 'S') + ' ' + Math.abs(lon).toFixed(1) + '\u00b0' + (lon >= 0 ? 'E' : 'W');
  }
  function hm(hours) {
    var m = Math.round(hours * 60) % 1440;
    return ('0' + Math.floor(m / 60)).slice(-2) + ':' + ('0' + m % 60).slice(-2);
  }
  function age(ms) {
    var h = Math.abs(ms) / 3600000;
    return h < 48 ? Math.max(1, Math.round(h)) + ' h' : Math.round(h / 24) + ' days';
  }
  function km(v) { return Math.round(v).toLocaleString('en') + ' km'; }

  Sats.prototype.tipHTML = function (x) {
    var s = x.s, f = this.facts(x, this.time()), rows = [];
    var kind = s.kind === 'geo' ? 'geostationary' : s.kind === 'sso' ? 'polar \u00b7 sun-synchronous' : 'low orbit \u00b7 ' + Math.round(x.el.i / DEG) + '\u00b0 inclined';
    if (s.kind === 'geo') {
      rows.push(['over', Math.abs(f.lon).toFixed(1) + '\u00b0' + (f.lon >= 0 ? 'E' : 'W') + ' on the equator, turning with the Earth']);
      // Distance from the Earth's centre, true against drawn.
      rows.push(['height', km(f.h) + ' <span class="dim">\u00b7 not to scale: the ring is drawn ' +
        ((f.h + RE) / RE / GEO_R).toFixed(0) + '\u00d7 smaller than it is</span>']);
    } else {
      rows.push(['over', f.stale ? '<span class="dim">unknown: its orbit data is ' + age(f.age) + ' old</span>' : ll(f.lat, f.lon)]);
      rows.push(['height', km(f.h) + ' <span class="dim">\u00b7 to scale</span>']);
      rows.push(['speed', f.v.toFixed(1) + ' km/s \u00b7 once round in ' + Math.round(f.period) + ' min']);
      if (f.ltan != null) rows.push(['equator', 'north-bound at ' + hm(f.ltan) + ', south-bound at ' + hm(f.ltan + 12) + ' local time, every orbit']);
    }
    rows.push(['carries', esc(s.what)]);
    return '<b>' + esc(s.name) + '</b> <span class="dim mono">' + esc(s.op) + '</span><br>' +
      '<span class="mono">' + kind + (s.role ? ' \u00b7 ' + esc(s.role.toLowerCase()) : '') + '</span>' +
      '<dl>' + rows.map(function (r) { return '<dt>' + r[0] + '</dt><dd>' + r[1] + '</dd>'; }).join('') + '</dl>' +
      '<p class="sat-why">' + esc(s.rain) + '</p>' +
      '<span class="dim mono">orbit from CelesTrak, ' + age(f.age) + ' old' + (this.src === 'baked' ? ', as built' : '') + '</span>';
  };

  /* Hover and tap. Registered after the stage's own listener, so a city's
   * label has already been decided when this runs; a city nearby wins. */
  Sats.prototype._bindHover = function (hit) {
    var self = this, down = null;
    function at(e, reach) {
      if (!self._active() || hit.classList.contains('grabbing')) return self._unhover();
      if (self.st.nearest(e.clientX, e.clientY, reach)) return self._unhover(true);
      var x = self.nearest(e.clientX, e.clientY, reach);
      if (!x) return self._unhover();
      self._hover(x, e.clientX, e.clientY);
    }
    hit.addEventListener('pointermove', function (e) { if (e.pointerType === 'mouse' && !e.buttons) at(e, 12); });
    hit.addEventListener('pointerdown', function (e) { down = [e.clientX, e.clientY]; });
    hit.addEventListener('pointerup', function (e) {
      if (e.pointerType === 'mouse' || !down) return;
      var moved = Math.abs(e.clientX - down[0]) + Math.abs(e.clientY - down[1]);
      down = null;
      if (moved < 6) at(e, 22);
    });
    hit.addEventListener('pointerleave', function () { self._unhover(); });
  };

  Sats.prototype._hover = function (x, px, py) {
    var tip = this.opts.tip;
    this.hot = x.s.id;
    this.opts.hit.classList.add('sat');
    if (!tip) return;
    tip.classList.remove('tip-text'); tip.innerHTML = this.tipHTML(x);
    tip.dataset.globe = 1; tip.dataset.sat = x.s.id;
    tip.hidden = false;
    tip.style.left = Math.max(8, Math.min(innerWidth - tip.offsetWidth - 8, px + 16)) + 'px';
    tip.style.top = Math.max(8, Math.min(innerHeight - tip.offsetHeight - 8, py + 16)) + 'px';
  };

  /* keepTip: a city took the pointer, and the tip is now the city's. */
  Sats.prototype._unhover = function (keepTip) {
    var tip = this.opts.tip;
    this.hot = null;
    if (this.opts.hit) this.opts.hit.classList.remove('sat');
    if (tip && tip.dataset.sat) {
      delete tip.dataset.sat;
      if (!keepTip) { tip.hidden = true; delete tip.dataset.globe; }
    }
  };

  global.AtlasSats = Sats;
})(window);
