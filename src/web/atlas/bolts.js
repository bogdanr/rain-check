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
  // Flash rate per frame age: the newest storms are the busy ones.
  var AGE_RATE = [1, 0.45, 0.2];
  // Lightning's colour through cloud (scattered, bluish) and at its core.
  var GLOW = [175, 195, 255], CORE = [235, 240, 255];

  function hash(i) {
    i = Math.imul(i ^ (i >>> 16), 0x45d9f3b); i = Math.imul(i ^ (i >>> 16), 0x45d9f3b);
    return ((i ^ (i >>> 16)) >>> 0) / 4294967296;
  }

  /* A soft round sprite in one colour, drawn once and stamped many times. */
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

  function Bolts(canvas, stage) {
    this.c = canvas; this.st = stage; this.g = canvas.getContext('2d');
    this.data = null; this.on = false; this.live = false; this.raf = 0;
    this.sun = null; this.stale = false; this._cam = ''; this.cloudAt = null;
    this.reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.glow = sprite(GLOW, false);
    this.core = sprite(CORE, true);
  }

  Bolts.prototype.setData = function (b) {
    this.data = b; this._cam = ''; this._shade(); this._kick();
  };

  /* Where the cloud is (a lon/lat -> 0..1 cover function, or null when the
   * clouds are hidden): a flash fills thick cloud with light and shows its
   * core only through thin cloud. Read once per cell, not per frame. */
  Bolts.prototype.setClouds = function (fn) { this.cloudAt = fn; this._cam = ''; this._shade(); };
  Bolts.prototype._shade = function () {
    if (!this.data) return;
    var fn = this.cloudAt, clear = 0;
    this.data.frames.forEach(function (f) {
      f.cells.forEach(function (c) {
        var v = fn ? fn(c.lon, c.lat) : null;
        if (v != null && v < 0.25) clear++;
        // Lightning only where there is cloud: under ~25% cover the cell is
        // not drawn (the cloud frame and the flashes disagree on where the
        // storm is); with the clouds hidden, every cell shows.
        c.cl = v == null ? 0.7 : v < 0.25 ? 0 : Math.max(0.35, v);
      });
    });
    this.c.dataset.clear = String(clear);   // cells held back over clear sky (for the check)
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
    this.c.dataset.drawn = '0';
  };

  Bolts.prototype._frame = function (now) {
    var st = this.st, cam = st.cam, dpr = Math.min(devicePixelRatio || 1, 1.5);
    var w = Math.round(innerWidth * dpr), h = Math.round(innerHeight * dpr);
    // Nothing moves in reduced motion but the camera: redraw only for it.
    var key = [cam.lon, cam.lat, cam.k, cam.cx, cam.cy, cam.dim, w, h].map(function (v) { return v.toFixed(3); }).join();
    if (this.c.width !== w || this.c.height !== h) { this.c.width = w; this.c.height = h; }
    if (this.reduce && key === this._cam) return;
    this._cam = key;
    var g = this.g;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, w, h);
    // Hidden behind the reading chapters and when docked, like the volume.
    var fade = Math.max(0, Math.min(1, (0.6 - cam.dim) / 0.25)) * Math.max(0, Math.min(1, (cam.k - 0.12) / 0.06));
    if (this.stale) fade *= 0.45;
    if (fade <= 0) { this.c.dataset.drawn = '0'; return; }
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.globalCompositeOperation = 'lighter';
    var R = st._radius(), cellPx = Math.max(1.2, R * 0.3 * DEG), s = now / 1000;
    var sun = this.sun, drawn = 0, frames = this.data.frames;
    this._outline(g, fade);
    for (var fi = 0; fi < frames.length; fi++) {
      var cells = frames[fi].cells, age = Math.min(2, Math.round((frames[0].t - frames[fi].t) / 300000));
      for (var i = 0; i < cells.length && drawn < MAX_DRAW; i++) {
        var c = cells[i], p = st.project(c.lon, c.lat);
        if (p[2] <= 0.02 || c.cl === 0) continue;
        drawn++;
        var limb = Math.min(1, p[2] * 4);
        // A flash is brilliant at night and barely shows against sunlit cloud.
        var night = 1;
        if (sun) {
          var v = [Math.cos(c.lat * DEG) * Math.sin(c.lon * DEG), Math.sin(c.lat * DEG), Math.cos(c.lat * DEG) * Math.cos(c.lon * DEG)];
          night = 0.3 + 0.7 * Math.max(0, Math.min(1, 0.5 - (v[0] * sun[0] + v[1] * sun[1] + v[2] * sun[2]) * 3));
        }
        var dens = Math.min(1, c.n / 20), cl = c.cl == null ? 0.7 : c.cl, f;
        if (this.reduce) {
          // No flicker: a faint, steady blue light in the storm's cloud.
          f = 0.3 * [1, 0.6, 0.35][age];
        } else {
          // Flashes at random moments, rate rising with the count; often a
          // second stroke down the same channel a moment later.
          var rate = (0.08 + 0.7 * dens) * AGE_RATE[age], u = s * rate + hash(c.k * 7 + fi), b = Math.floor(u);
          var at = hash(c.k * 131 + b) * 0.8, dt = (u - b - at) / rate;
          f = dt >= 0 ? Math.exp(-dt / FLASH_S) : 0;
          if (hash(c.k * 17 + b) > 0.55 && dt > 0.07) f = Math.max(f, Math.exp(-(dt - 0.07) / FLASH_S) * 0.85);
          f *= 0.6 + 0.4 * hash(c.k * 29 + b);   // not every flash is as bright
        }
        if (f < 0.03) continue;
        var a = f * limb * night * fade;
        // The cloud lit from inside: wider and softer the thicker it is.
        var r = cellPx * (3 + 4 * cl) * (0.8 + 0.5 * dens) + 3;
        g.globalAlpha = Math.min(1, a * (0.45 + 0.4 * cl));
        g.drawImage(this.glow, p[0] - r, p[1] - r, r * 2, r * 2);
        // The channel itself shows only through thin cloud.
        var k = a * (1 - cl) * 1.4;
        if (k > 0.03) {
          var rc = cellPx * 1.2 + 1.5;
          g.globalAlpha = Math.min(1, k);
          g.drawImage(this.core, p[0] - rc, p[1] - rc, rc * 2, rc * 2);
        }
      }
    }
    g.globalAlpha = 1; g.globalCompositeOperation = 'source-over';
    this.c.dataset.drawn = String(drawn);
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
