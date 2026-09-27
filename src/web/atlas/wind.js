/* Wind on the Atlas globe, as the sea shows it. The field goes to the stage
 * (stage.setWind), which draws it in the ocean itself: the Sun's glint
 * spreads and dulls as the wind rises, whitecaps brighten storm belts, and
 * over a resting, near view wave crests run downwind (stage.js, FS and WFS).
 * This file only decodes the data, keeps the field on the page clock and
 * hands it over; it draws nothing unless asked for the debug arrows.
 *
 * The data is a model forecast, not an observation: Open-Meteo's 10 m wind
 * on a 10-degree grid (about 1,000 km), baked by the site build every 3 hours
 * (src/wind_grid.py documents the file). That grid resolves the trade winds,
 * the westerlies and monsoon flow, and the broad direction near a city; it
 * cannot show hurricanes, fronts or sea breezes.
 *
 * Time: the field is interpolated linearly between the hourly frames for
 * the page's clock, in tenths of an hour. Past the last frame the wind
 * leaves the sea (the legend says the forecast has run out).
 *
 * ?wind=debug adds one arrow per grid point on a 2-D canvas over the stage,
 * pointing downwind, longer and brighter when stronger - to check the data
 * and the waves' direction against it, not for readers. */
(function (global) {
  'use strict';
  var DEG = Math.PI / 180;
  var COLOR = '215,235,255';

  function Wind(canvas, stage, opts) {
    opts = opts || {};
    this.c = canvas; this.st = stage; this.g = canvas.getContext('2d');
    this.now = opts.now || Date.now;
    this.debug = !!opts.debug;
    this.data = null; this.on = false; this.live = false; this.raf = 0;
    this._cam = ''; this.field = null; this.fi = -1; this._sent = null;
  }

  /* d: the parsed wind.json. Unpacked once into Float32 u, v per frame. */
  Wind.prototype.setData = function (d) {
    var raw = atob(d.uv), n = d.lat[2] * d.lon[2], fr = [], k, i;
    for (k = 0; k < d.n; k++) {
      var u = new Float32Array(n), v = new Float32Array(n);
      for (i = 0; i < n; i++) {
        var a = raw.charCodeAt((k * n + i) * 2), b = raw.charCodeAt((k * n + i) * 2 + 1);
        u[i] = (a > 127 ? a - 256 : a) * d.scale; v[i] = (b > 127 ? b - 256 : b) * d.scale;
      }
      fr.push({ u: u, v: v });
    }
    this.data = d; this.frames = fr; this.fi = -1; this._cam = '';
    this.c.dataset.frames = String(d.n);
    this.tick(); this._sync();
  };

  /* Valid times: t0 .. t0 + (n - 1) dt, in ms. */
  Wind.prototype.span = function () {
    var d = this.data; if (!d) return null;
    return [d.t0 * 1000, (d.t0 + (d.n - 1) * d.dt) * 1000];
  };
  Wind.prototype.expired = function () { var s = this.span(); return !!s && this.now() > s[1] + this.data.dt * 500; };

  /* Re-interpolate for the clock (called every minute): a new field only
   * when it has moved by a tenth of an hour, so the sea does not re-shade
   * every minute for nothing. */
  Wind.prototype.tick = function () {
    var d = this.data; if (!d) return;
    var f = (this.now() / 1000 - d.t0) / d.dt;
    f = Math.max(0, Math.min(d.n - 1, Math.round(f * 10) / 10));
    if (f === this.fi && this.field) { this._sync(); return; }
    var a = Math.floor(f), b = Math.min(d.n - 1, a + 1), w = f - a, A = this.frames[a], B = this.frames[b];
    var n = A.u.length, u = new Float32Array(n), v = new Float32Array(n);
    for (var i = 0; i < n; i++) { u[i] = A.u[i] + (B.u[i] - A.u[i]) * w; v[i] = A.v[i] + (B.v[i] - A.v[i]) * w; }
    this.field = { u: u, v: v }; this.fi = f; this._cam = '';
    this.c.dataset.hour = String(f);
    this._sync();
  };

  /* Bilinear (u, v) at any point, columns wrapping round the date line;
   * null beyond the outermost rows (the grid stops at 75 degrees). */
  Wind.prototype.at = function (lon, lat) {
    var d = this.data, F = this.field; if (!d || !F) return null;
    var nr = d.lat[2], nc = d.lon[2];
    var y = (lat - d.lat[0]) / d.lat[1];
    if (y < 0 || y > nr - 1) return null;
    var x = (lon - d.lon[0]) / d.lon[1];
    x = ((x % nc) + nc) % nc;
    var i = Math.min(nr - 2, Math.floor(y)), j = Math.floor(x), j2 = (j + 1) % nc, fy = y - i, fx = x - j;
    function m(ar) {
      var a = ar[i * nc + j], b = ar[i * nc + j2], c = ar[(i + 1) * nc + j], e = ar[(i + 1) * nc + j2];
      return (a + (b - a) * fx) * (1 - fy) + (c + (e - c) * fx) * fy;
    }
    return [m(F.u), m(F.v)];
  };

  /* on: the reader wants the layer; live: the live sky is on. */
  Wind.prototype.show = function (on, live) { this.on = !!on; this.live = !!live; this._cam = ''; this._sync(); };

  /* The debug arrows on or off (the ?tune=1 panel). */
  Wind.prototype.setDebug = function (on) { this.debug = !!on; this._cam = ''; this._kick(); };

  Wind.prototype._active = function () {
    return this.on && this.live && this.data && this.field && !this.st.failed && !this.expired();
  };

  /* Hand the field to the stage (or take it away) when either changed. */
  Wind.prototype._sync = function () {
    var act = this._active(), F = act ? this.field : null;
    if (F !== this._sent) {
      this._sent = F;
      if (this.st.setWind) this.st.setWind(F ? this.data : null, F);
    }
    this.c.dataset.sea = act ? '1' : '0';
    this._kick();
  };

  /* The debug arrows' loop: only with ?wind=debug, and it redraws only when
   * the camera or the hour moved. */
  Wind.prototype._kick = function () {
    var self = this;
    if (!this.debug || !this._active()) {
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = 0; this._clear(); return;
    }
    if (this.raf) return;
    this.raf = requestAnimationFrame(function f() {
      if (!self.debug || !self._active()) { self.raf = 0; self._clear(); return; }
      self._frame();
      self.raf = requestAnimationFrame(f);
    });
  };

  Wind.prototype._clear = function () {
    this.g.clearRect(0, 0, this.c.width, this.c.height);
    this.c.dataset.arrows = '0'; this._cam = '';
  };

  Wind.prototype._fade = function () {
    // Hidden behind the reading chapters and when docked, like the lightning.
    var cam = this.st.cam;
    return Math.max(0, Math.min(1, (0.6 - cam.dim) / 0.25)) * Math.max(0, Math.min(1, (cam.k - 0.12) / 0.06));
  };

  Wind.prototype._frame = function () {
    var st = this.st, cam = st.cam, dpr = st.dpr ? st.dpr() : Math.min(devicePixelRatio || 1, 1.5);
    var w = Math.round(innerWidth * dpr), h = Math.round(innerHeight * dpr);
    var key = [cam.lon, cam.lat, cam.k, cam.cx, cam.cy, cam.dim, w, h, this.fi].map(function (v) { return (+v).toFixed(3); }).join();
    if (this.c.width !== w || this.c.height !== h) { this.c.width = w; this.c.height = h; this._cam = ''; }
    if (key === this._cam) return;
    this._cam = key;
    var g = this.g, fade = this._fade();
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, w, h);
    if (fade <= 0) { this.c.dataset.arrows = '0'; return; }
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._arrows(g, fade);
  };

  /* One arrow per grid point on the near side, centred on it, pointing
   * where the air goes: length and brightness grow with speed and level off
   * past a gale, so a spacing's worth of globe is never overrun. */
  Wind.prototype._arrows = function (g, fade) {
    var d = this.data, F = this.field, st = this.st, R = st._radius(), n = 0;
    var nr = d.lat[2], nc = d.lon[2];
    var spacing = R * Math.abs(d.lon[1]) * DEG;          // one grid step at the equator, px
    g.lineCap = 'round'; g.lineJoin = 'round';
    g.strokeStyle = 'rgb(' + COLOR + ')';
    for (var i = 0; i < nr; i++) {
      var lat = d.lat[0] + i * d.lat[1], cl = Math.cos(lat * DEG);
      for (var j = 0; j < nc; j++) {
        var lon = d.lon[0] + j * d.lon[1], p = st.project(lon, lat);
        if (p[2] <= 0.05) continue;
        var u = F.u[i * nc + j], v = F.v[i * nc + j], s = Math.sqrt(u * u + v * v);
        if (s < 0.5) {
          // Calm: a small open ring, so "no wind" reads as data, not a gap.
          g.globalAlpha = 0.35 * fade * Math.min(1, p[2] * 4); g.lineWidth = 1;
          g.beginPath(); g.arc(p[0], p[1], 2, 0, 6.283); g.stroke(); n++;
          continue;
        }
        // Screen direction of "downwind" from a small step along it.
        var e = 0.5, q = st.project(lon + u / s * e / Math.max(0.2, cl), lat + v / s * e);
        var dx = q[0] - p[0], dy = q[1] - p[1], dl = Math.sqrt(dx * dx + dy * dy);
        if (dl < 1e-6) continue;
        dx /= dl; dy /= dl;
        var k = Math.min(1, s / 20);                     // 20 m/s (72 km/h): full length
        var len = spacing * (0.22 + 0.5 * Math.sqrt(k)) * Math.min(1, p[2] * 1.5 + 0.3);
        var hx = dx * len / 2, hy = dy * len / 2, ah = Math.min(6, 2.5 + len * 0.18);
        g.globalAlpha = (0.3 + 0.6 * Math.min(1, s / 12)) * fade * Math.min(1, p[2] * 4);
        g.lineWidth = 1 + 0.9 * k;
        g.beginPath();
        g.moveTo(p[0] - hx, p[1] - hy); g.lineTo(p[0] + hx, p[1] + hy);
        var tx = p[0] + hx, ty = p[1] + hy;
        g.moveTo(tx - dx * ah - dy * ah * 0.6, ty - dy * ah + dx * ah * 0.6);
        g.lineTo(tx, ty);
        g.lineTo(tx - dx * ah + dy * ah * 0.6, ty - dy * ah - dx * ah * 0.6);
        g.stroke();
        n++;
      }
    }
    g.globalAlpha = 1;
    this.c.dataset.arrows = String(n);
  };

  global.AtlasWind = Wind;
})(window);
