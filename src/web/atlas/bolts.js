/* Live lightning on the Atlas globe: a 2-D canvas over the WebGL stage.
 *
 * The data is AtlasSky.bolts(): up to three 5-minute frames of lit cells
 * from Meteosat's Lightning Imager, newest first. Lightning seen from orbit
 * is not a dot and never warm: it is a cloud lit from inside for a fraction
 * of a second, blue-white. So each cell flashes at random moments - more
 * often where more flashes were counted, less often in the older frames -
 * as a wide, soft glow through the cloud around it, with a small hot core
 * only where the cloud is thin. Nothing glows steadily, so between flashes
 * the storm is just its cloud. (Warm yellow-to-red is a lightning *map*
 * convention for age, not what anyone sees.)
 *
 * It is its own layer on purpose: the stage redraws only when something
 * changed and refines the cloud volume over several still frames, and a
 * flicker drawn inside it would force a full ray-march every frame. This
 * canvas animates by itself, never marks the stage dirty, and runs only
 * while there is something to show. Reduced motion: steady dots only,
 * redrawn when the camera moves.
 *
 * Positions come from stage.project(), the same projection the markers
 * use, read after the stage's own frame so they never lag the globe. */
(function (global) {
  'use strict';
  var DEG = Math.PI / 180;
  var MAX_DRAW = 5000;             // cells considered per frame, busiest first
  var FLASH_S = 0.08;
  var STORM_DEG = 1.5;             // cells within one grid box of this size are one storm
  // Flash rate per frame age: the newest storms are the busy ones.
  var AGE_RATE = [1, 0.45, 0.2];
  /* The lightning look (the ?tune=1 panel edits it live, setLook):
   *  bright   - how much light a flash puts out, x BRIGHT_K (1 = the tuned
   *             baseline). It scales the flash's strength before two soft
   *             limits (here and in the stage's light pass), so doubling it
   *             brightens faint flashes a lot and bright ones only a little
   *  size     - glow radius, x the lit cloud's size
   *  rate     - flashes per second of the busiest storm
   *  day      - how much of a flash shows against sunlit cloud (0..1)
   *  minCover - cloud cover under which a cell is not drawn (the 3-D clouds
   *             wear thin cover away, so a glow there hangs in clear sky)
   *  contrast - how far strong storms stand out from weak ones (0 = alike)
   *  spread   - how uneven single flashes are (0 = all equal) */
  var LOOK = { bright: 0.8, size: 0.8, rate: 0.06, day: 1.0, minCover: 0.2, contrast: 2.0, spread: 1.4 };
  // The raw strength that bright = 1 stands for (tuned on the ?tune=1 panel:
  // what was 3 on the old scale).
  var BRIGHT_K = 3.0;
  var SHAPES = 6;                  // irregular glow shapes, picked per flash
  // Lightning's colour through cloud (scattered, bluish) and at its core.
  var GLOW = [175, 195, 255], CORE = [235, 240, 255];

  function hashStr(s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
    return h >>> 0;
  }

  function hash(i) {
    i = Math.imul(i ^ (i >>> 16), 0x45d9f3b); i = Math.imul(i ^ (i >>> 16), 0x45d9f3b);
    return ((i ^ (i >>> 16)) >>> 0) / 4294967296;
  }

  /* A soft round sprite in one colour, drawn once and stamped many times
   * (the channel's core, seen through thin cloud). */
  function sprite(rgb, core) {
    var S = 64, c = document.createElement('canvas'); c.width = c.height = S;
    var g = c.getContext('2d'), gr = g.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
    var s = rgb.join(',');
    gr.addColorStop(0, 'rgba(' + (core ? '255,255,255' : s) + ',1)');
    gr.addColorStop(core ? 0.12 : 0.25, 'rgba(' + s + ',' + (core ? 0.9 : 0.75) + ')');
    gr.addColorStop(0.5, 'rgba(' + s + ',0.22)');
    gr.addColorStop(1, 'rgba(' + s + ',0)');
    g.fillStyle = gr; g.fillRect(0, 0, S, S);
    return c;
  }

  /* Cloud lit from inside: a few overlapping lobes, each falling off with
   * the square of the distance (1 / (1 + (r/r0)^2)) - light spreading
   * through cloud, not a disc with a rim - tapered smoothly to nothing at
   * the sprite's edge. Whiter where it is brightest, bluer in the fringe.
   * Made once per shape at load (SHAPES of them, 96 px each). */
  function glowShape(seed) {
    var S = 96, c = document.createElement('canvas'); c.width = c.height = S;
    var g = c.getContext('2d'), im = g.createImageData(S, S), d = im.data, lobes = [], i, x, y;
    var n = 3 + Math.floor(hash(seed * 7 + 1) * 4);
    for (i = 0; i < n; i++) {
      var an = hash(seed * 31 + i * 3) * 6.283, rr = i ? 0.1 + 0.22 * hash(seed * 13 + i) : 0;
      lobes.push([Math.cos(an) * rr, Math.sin(an) * rr, i ? 0.05 + 0.07 * hash(seed * 17 + i) : 0.08, i ? 0.35 + 0.5 * hash(seed * 23 + i) : 1]);
    }
    var buf = new Float32Array(S * S), mx = 0;
    for (y = 0; y < S; y++) for (x = 0; x < S; x++) {
      var u = (x + 0.5) / S * 2 - 1, v = (y + 0.5) / S * 2 - 1, e = 0;
      for (i = 0; i < n; i++) {
        var du = u - lobes[i][0], dv = v - lobes[i][1], q = (du * du + dv * dv) / (lobes[i][2] * lobes[i][2]);
        e += lobes[i][3] / (1 + q);
      }
      var r2 = u * u + v * v, taper = r2 >= 1 ? 0 : (1 - r2) * (1 - r2);
      buf[y * S + x] = e * taper; if (buf[y * S + x] > mx) mx = buf[y * S + x];
    }
    for (i = 0; i < S * S; i++) {
      var k = buf[i] / mx, w = Math.min(1, k * 1.4);
      d[i * 4] = GLOW[0] + (CORE[0] - GLOW[0]) * w;
      d[i * 4 + 1] = GLOW[1] + (CORE[1] - GLOW[1]) * w;
      d[i * 4 + 2] = GLOW[2] + (CORE[2] - GLOW[2]) * w;
      d[i * 4 + 3] = Math.round(255 * k);
    }
    g.putImageData(im, 0, 0);
    return c;
  }

  function clamp01(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }

  function Bolts(canvas, stage) {
    this.c = canvas; this.st = stage; this.g = canvas.getContext('2d');
    this.data = null; this.on = false; this.live = false; this.raf = 0;
    this.sun = null; this.stale = false; this._cam = ''; this.cloudAt = null;
    this.reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.look = Object.assign({}, LOOK); this.storms = [];
    this.shapes = [];
    for (var i = 0; i < SHAPES; i++) this.shapes.push(glowShape(i + 1));
    this.core = sprite(CORE, true);
  }

  Bolts.prototype.setData = function (b) {
    this.data = b; this._cam = ''; this._shade(); this._kick();
  };

  /* Where the cloud is (a lon/lat -> 0..1 cover function, or null when the
   * clouds are hidden): a flash fills thick cloud with light and shows its
   * core only through thin cloud. Read once per cell, not per frame. */
  Bolts.prototype.setClouds = function (fn) { this.cloudAt = fn; this._cam = ''; this._shade(); };
  Bolts.prototype.setLook = function (o) {
    this.look = o ? Object.assign({}, this.look, o) : Object.assign({}, LOOK);
    this._cam = ''; this._shade(); return this.look;
  };
  Bolts.prototype.lookDefaults = function () { return Object.assign({}, LOOK); };
  /* Per cell: how thick its cloud is (c.cl, 0 = not drawn). Then the cells
   * are grouped into storms - one grid box of STORM_DEG per storm and frame
   * - because a storm seen from orbit flashes in one place at a time, and
   * one glow per cell (hundreds per storm, added together) read as a
   * continent-sized blaze. */
  Bolts.prototype._shade = function () {
    if (!this.data) return;
    var fn = this.cloudAt, clear = 0, lo = this.look.minCover, storms = [];
    this.data.frames.forEach(function (f, fi) {
      var age = Math.min(2, Math.round((this.data.frames[0].t - f.t) / 300000)), by = {};
      f.cells.forEach(function (c) {
        var o = fn ? fn(c.lon, c.lat) : null, v = o && o.cloud != null ? o.cloud : null;
        c.top = o && o.top != null ? o.top : null; c.rain = o && o.rain != null ? o.rain : null;
        if (v != null && v < lo) clear++;
        // Lightning only where there is cloud drawn; with the clouds hidden,
        // every cell shows.
        c.cl = v == null ? 0.7 : v < lo ? 0 : Math.max(0.35, v);
        if (!c.cl) return;
        var key = Math.floor(c.lon / STORM_DEG) + ':' + Math.floor(c.lat / STORM_DEG), s = by[key];
        if (!s) { s = by[key] = { cells: [], n: 0, age: age, key: key, id: (hashStr(key) ^ (fi * 7919)) >>> 0 }; storms.push(s); }
        s.cells.push(c); s.n += c.n;
      });
    }, this);
    // How strong each storm is (0..1), from what the satellites say about
    // it: its flash count (the most direct sign), whether it has lasted
    // through the frames (15 minutes of lightning is an organised storm),
    // and - coarser, so weighted less - the rain under it and how cold and
    // tall its top is. Signs we do not have (one frame only, clouds hidden)
    // drop out and the rest are re-weighted.
    var nf = this.data.frames.length, seen = {};
    storms.forEach(function (sm) { seen[sm.key] = (seen[sm.key] || 0) + 1; });
    storms.forEach(function (sm) {
      var w = 0.45, sc = 0.45 * Math.min(1, Math.log(1 + sm.n) / Math.log(200));
      if (nf > 1) { w += 0.25; sc += 0.25 * (seen[sm.key] - 1) / (nf - 1); }
      var top = -1, rain = 0, nr = 0;
      sm.cells.forEach(function (c) {
        if (c.top != null && c.top > top) top = c.top;
        if (c.rain != null) { rain += c.rain; nr++; }
      });
      if (nr) { w += 0.15; sc += 0.15 * Math.min(1, Math.log(1 + rain / nr) / Math.log(31)); }   // ~30 mm/h: a downpour
      if (top >= 0) { w += 0.15; sc += 0.15 * clamp01((top - 0.45) / 0.4); }
      sm.score = sc / w;
    });
    // Busiest first, so MAX_DRAW keeps the storms that matter.
    storms.sort(function (a, b) { return b.n - a.n; });
    // How many of them fit in MAX_DRAW cells, and each one's flash rate and
    // next flash (so a frame only looks at storms that are flashing).
    var L = this.look, used = 0, nUse = 0;
    while (nUse < storms.length && used < MAX_DRAW) used += storms[nUse++].cells.length;
    storms.forEach(function (sm) {
      sm.ss = clamp01(0.5 + (sm.score - 0.5) * L.contrast);
      sm.rate = L.rate * (0.2 + 1.3 * sm.ss) * AGE_RATE[sm.age];
      sm.next = 0;
    });
    this.nUse = nUse; this.nCells = used;
    this.storms = storms;
    this.c.dataset.clear = String(clear);   // cells held back over clear sky (for the check)
    this.c.dataset.storms = String(storms.length);
    this.c.dataset.strong = String(storms.filter(function (sm) { return sm.score > 0.6; }).length);
  };

  /* on: the reader wants the layer; live: the live sky is on. */
  Bolts.prototype.show = function (on, live) { this.on = !!on; this.live = !!live; this._cam = ''; this._kick(); };
  Bolts.prototype.setSun = function (v) { this.sun = v; this._cam = ''; };
  Bolts.prototype.setStale = function (s) { this.stale = !!s; this._cam = ''; };

  Bolts.prototype._active = function () {
    return this.on && this.live && this.data && !this.st.failed;
  };

  Bolts.prototype._kick = function () {
    var self = this;
    if (!this._active()) {
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = 0; this._clear(); return;
    }
    if (this.raf) return;
    this.raf = requestAnimationFrame(function f(t) {
      if (!self._active()) { self.raf = 0; self._clear(); return; }
      self._frame(t);
      self.raf = requestAnimationFrame(f);
    });
  };

  Bolts.prototype._clear = function () {
    this.g.clearRect(0, 0, this.c.width, this.c.height);
    if (this.st.setFlashes) this.st.setFlashes(null, '');
    this.c.dataset.drawn = '0';
  };

  /* The storms flashing now (the first nUse, busiest first). Each storm's
   * next flash is kept, so between flashes a storm costs one comparison:
   * the layer used to evaluate every storm's flash law every frame (about
   * 20 ms of script a second on the city view; perf_atlas.py). */
  Bolts.prototype._due = function (t) {
    var out = [], storms = this.storms;
    for (var i = 0; i < this.nUse; i++) {
      var sm = storms[i];
      if (t < sm.next) continue;
      var u = t * sm.rate + hash(sm.id), b = Math.floor(u), dt = (u - b - hash(sm.id * 131 + b) * 0.7) / sm.rate;
      if (dt < 0) sm.next = t - dt;                       // later in this cycle
      else if (dt >= 0.8) sm.next = (b + 1 + hash(sm.id * 131 + b + 1) * 0.7 - hash(sm.id)) / sm.rate;
      else out.push(sm);                                  // flashing: look again next frame
    }
    return out;
  };

  Bolts.prototype._frame = function (now) {
    var st = this.st, cam = st.cam, dpr = st.dpr ? st.dpr() : Math.min(devicePixelRatio || 1, 1.5);
    var w = Math.round(innerWidth * dpr), h = Math.round(innerHeight * dpr);
    // Compared as numbers, not a formatted string: this runs every frame.
    // (_cam = '' elsewhere asks for a repaint.)
    var kv = this._kv || (this._kv = []), key = this._cam, now3 = [cam.lon, cam.lat, cam.k, cam.cx, cam.cy, cam.dim, w, h];
    if (!key) key = now3.join();
    else for (var ki = 0; ki < 8; ki++) if (!(Math.abs(now3[ki] - kv[ki]) <= 5e-4)) { key = now3.join(); break; }
    if (key !== this._cam) this._kv = now3;
    if (this.c.width !== w || this.c.height !== h) { this.c.width = w; this.c.height = h; this._cam = ''; }
    // Nothing moves in reduced motion but the camera: redraw only for it.
    // Otherwise redraw for the camera, a storm flashing, or to put out the
    // last flash; a still camera between flashes costs nothing.
    var due = this.reduce ? this.storms.slice(0, this.nUse) : this._due(now / 1000);
    var moved = key !== this._cam;
    if (!moved && (this.reduce || (!due.length && !this._lit))) return;
    this._cam = key;
    // With the 3-D clouds on, a flash is light on the scene: the stage lights
    // the cloud drawn there (and faintly the ground), so it takes the cloud's
    // shape. Without them (flat layer, a driver with no volume), the old
    // sprites stand in.
    var scene = !!(st.flashOK && st.flashOK()), list = scene ? [] : null;
    // In scene mode this canvas holds only the box outline, so a flash that
    // leaves the camera still does not repaint it.
    var paint = moved || !scene || this._mode !== 'scene';
    this._mode = scene ? 'scene' : 'sprite';
    var g = this.g;
    if (paint) { g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, w, h); }
    // Hidden behind the reading chapters and when docked, like the volume.
    var fade = Math.max(0, Math.min(1, (0.6 - cam.dim) / 0.25)) * Math.max(0, Math.min(1, (cam.k - 0.12) / 0.06));
    if (this.stale) fade *= 0.45;
    if (fade <= 0) { this.c.dataset.drawn = '0'; this._lit = 0; if (st.setFlashes) st.setFlashes(null, ''); return; }
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.globalCompositeOperation = 'lighter';
    var R = st._radius(), cellPx = Math.max(1.2, R * 0.3 * DEG), t = now / 1000;
    var sun = this.sun, L = this.look, drawn = this.nCells || 0, lit = 0, storms = due;
    if (this.c.dataset.mode !== this._mode) this.c.dataset.mode = this._mode;
    if (paint) this._outline(g, fade);
    for (var si = 0; si < storms.length; si++) {
      var sm = storms[si], cells = sm.cells;
      // The storm's strength, stretched or flattened by the contrast
      // control (0: every storm alike; _shade).
      var ss = sm.ss, f = 0, amp = 0, c = null, b = 0, grow = 0;
      if (this.reduce) {
        // No flicker: a faint, steady light at the storm's busiest cell.
        f = 0.25 * [1, 0.6, 0.35][sm.age]; amp = 0.4 + 0.4 * ss; c = cells[0];
      } else {
        // One flash at a time per storm, strong storms far more often.
        var rate = sm.rate;
        var u = t * rate + hash(sm.id), dt;
        b = Math.floor(u); dt = (u - b - hash(sm.id * 131 + b) * 0.7) / rate;
        if (dt >= 0 && dt < 0.8) {
          // 1-4 return strokes down the same channel, 40-120 ms apart and
          // each fainter than the first, strong storms more of them: the
          // flicker of real lightning, 0.2-0.5 s in all.
          var ns = 1 + Math.floor(hash(sm.id * 17 + b) * (1.2 + 2.8 * ss)), t0 = 0;
          for (var k2 = 0; k2 < ns; k2++) {
            var sa = k2 ? 0.35 + 0.6 * hash(sm.id * 41 + b * 5 + k2) : 1, ds = dt - t0;
            if (ds >= 0) f = Math.max(f, sa * Math.min(1, ds / 0.012) * Math.exp(-ds / (FLASH_S * (0.7 + 0.6 * hash(sm.id * 43 + b + k2)))));
            t0 += 0.04 + 0.08 * hash(sm.id * 47 + b * 3 + k2);
          }
          grow = Math.min(1, dt / 0.3);
          // How strong this flash is: mostly faint, a few bright, the bright
          // ones in strong storms (a skewed draw, its skew set by strength).
          var q = Math.pow(hash(sm.id * 29 + b), 4 - 2.6 * ss);
          amp = (1 - L.spread + L.spread * (0.15 + 1.25 * q)) * (0.45 + 0.75 * ss);
        }
        if (f >= 0.03) {
          // Where in the storm it strikes: busier cells more often.
          var pick = hash(sm.id * 53 + b) * sm.n, acc = 0;
          for (var i = 0; i < cells.length; i++) { acc += cells[i].n; if (acc >= pick) { c = cells[i]; break; } }
          c = c || cells[0];
        }
      }
      if (f < 0.03 || !c) continue;
      var p = st.project(c.lon, c.lat);
      if (p[2] <= 0.02) continue;
      var limb = Math.min(1, p[2] * 4);
      // A flash is brilliant at night and all but lost against sunlit cloud.
      var night = 1;
      if (sun) {
        var v = [Math.cos(c.lat * DEG) * Math.sin(c.lon * DEG), Math.sin(c.lat * DEG), Math.cos(c.lat * DEG) * Math.cos(c.lon * DEG)];
        var dk = Math.max(0, Math.min(1, 0.5 - (v[0] * sun[0] + v[1] * sun[1] + v[2] * sun[2]) * 3));
        night = L.day + (1 - L.day) * dk;
      }
      var cl = c.cl, a = f * amp * limb * night * fade * L.bright * BRIGHT_K;
      if (a < 0.01) continue;
      lit++;
      // The lit patch: larger for a stronger flash and thicker cloud,
      // widening a little as the strokes go on; an irregular shape, turned
      // and squashed at random, nudged off the data grid.
      var r = cellPx * (2 + 4 * cl) * (0.55 + 0.6 * Math.sqrt(amp)) * (1 + 0.25 * grow) * L.size + 2;
      var jx = (hash(sm.id * 59 + b) - 0.5) * cellPx * 1.6, jy = (hash(sm.id * 61 + b) - 0.5) * cellPx * 1.6;
      if (scene) {
        // Light spreads wider through cloud than the sprite's visible core.
        list.push({ x: p[0] + jx, y: p[1] + jy, r: r * 1.8, a: 1 - Math.exp(-a * 1.6), cl: cl });
        continue;
      }
      // A soft limit, not a cap: bright flashes stay brighter than medium.
      g.globalAlpha = 0.95 * (1 - Math.exp(-a * (0.6 + 0.5 * cl) * 1.6));
      g.save();
      g.translate(p[0] + jx, p[1] + jy);
      g.rotate(hash(sm.id * 67 + b) * 6.283);
      g.scale(1, 0.65 + 0.35 * hash(sm.id * 71 + b));
      g.drawImage(this.shapes[Math.floor(hash(sm.id * 73 + b) * SHAPES)], -r, -r, r * 2, r * 2);
      g.restore();
      // The channel itself shows only through thin cloud, and white-hot at
      // the heart of the strongest flashes.
      var k = a * (1 - cl) * 1.2 + Math.max(0, a - 0.8) * 0.6;
      if (k > 0.03) {
        var rc = cellPx * 1.0 + 1.2;
        g.globalAlpha = Math.min(0.9, k);
        g.drawImage(this.core, p[0] + jx - rc, p[1] + jy - rc, rc * 2, rc * 2);
      }
    }
    if (scene) {
      // The brightest 16 (the stage's limit); a key so an unchanged frame
      // (no flash) does not make the stage redraw.
      list.sort(function (x, y) { return y.a - x.a; }).length = Math.min(list.length, 16);
      st.setFlashes(list, list.map(function (e) { return e.x.toFixed(1) + ',' + e.y.toFixed(1) + ',' + e.a.toFixed(3); }).join(';'));
    } else if (st.setFlashes) st.setFlashes(null, '');
    if (lit !== this._lit || moved) { this.c.dataset.lit = String(lit); this.c.dataset.drawn = String(drawn); }
    this._lit = lit;
    g.globalAlpha = 1; g.globalCompositeOperation = 'source-over';
  };

  /* Where the satellite stops seeing: a faint dashed edge of its box, so
   * an empty Pacific reads as "no data", not "no storms". */
  Bolts.prototype._outline = function (g, fade) {
    var b = this.data.box, st = this.st, pts = [], i;
    for (i = 0; i <= 28; i++) pts.push([b[0] + (b[2] - b[0]) * i / 28, b[3]]);
    for (i = 0; i <= 28; i++) pts.push([b[2], b[3] - (b[3] - b[1]) * i / 28]);
    for (i = 0; i <= 28; i++) pts.push([b[2] - (b[2] - b[0]) * i / 28, b[1]]);
    for (i = 0; i <= 28; i++) pts.push([b[0], b[1] + (b[3] - b[1]) * i / 28]);
    g.save();
    g.globalCompositeOperation = 'source-over';
    g.globalAlpha = 0.16 * fade; g.strokeStyle = '#b8c8ff'; g.lineWidth = 1; g.setLineDash([2, 6]);
    g.beginPath();
    var pen = false;
    for (i = 0; i < pts.length; i++) {
      var p = st.project(pts[i][0], pts[i][1]);
      if (p[2] <= 0.02) { pen = false; continue; }
      if (pen) g.lineTo(p[0], p[1]); else g.moveTo(p[0], p[1]);
      pen = true;
    }
    g.stroke();
    g.restore();
  };

  global.AtlasBolts = Bolts;
})(window);
