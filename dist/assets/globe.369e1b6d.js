(function (global) {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var uid = 0;
  var DEG = Math.PI / 180;

  function el(name, attrs) {
    var n = document.createElementNS(SVG_NS, name);
    for (var k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    return n;
  }

  function reducedMotion() {
    return global.matchMedia &&
      global.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function tier(bss) {
    if (bss < 0) return 'q0';
    if (bss < 0.2) return 'q1';
    if (bss < 0.35) return 'q2';
    if (bss < 0.5) return 'q3';
    return 'q4';
  }

  function rgba(str) {
    str = str || '';
    var m = /rgba?\(([^)]+)\)/.exec(str);
    if (m) {
      var p = m[1].split(/[,\/\s]+/).filter(function (s) { return s !== ''; });
      if (p.length < 3) return null;
      return [+p[0], +p[1], +p[2], p.length > 3 ? +p[3] : 1];
    }
    m = /color\(\s*srgb\s+([^)]+)\)/.exec(str);
    if (m) {
      var q = m[1].split(/[\/\s]+/).filter(function (s) { return s !== ''; });
      if (q.length < 3) return null;
      return [Math.round(+q[0] * 255), Math.round(+q[1] * 255),
              Math.round(+q[2] * 255), q.length > 3 ? +q[3] : 1];
    }
    return null;
  }

  function mix(c, target, k) {
    return 'rgba(' + Math.round(c[0] + (target - c[0]) * k) + ',' +
                     Math.round(c[1] + (target - c[1]) * k) + ',' +
                     Math.round(c[2] + (target - c[2]) * k) + ',' + c[3] + ')';
  }

  function css(c) {
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + c[3] + ')';
  }

  var ELEV_SEA = 128, ELEV_MAX_M = 8500, DEPTH_MAX_M = 9000;
  var CIRC_M = 40075017, MERID_M = 20003931;

  var EXAG = 14, SEA_EXAG = 0.08;

  var DISP_MIN_X = 20, DISP_MAX_X = 100;
  var DISP_CAP = 2.4, PARALLAX_LO = 0.88;
  var LIMB_SWEEP = 0.24, LIMB_STEPS = 9;

  var R_EARTH_M = 6371000;

  var DISP_LIN = DISP_MAX_X / R_EARTH_M;
  var DISP_ROOT = DISP_MIN_X * Math.sqrt(ELEV_MAX_M) / R_EARTH_M;
  var DISP_TOP = DISP_MIN_X * ELEV_MAX_M / R_EARTH_M;

  function displace(h) {
    var lin = h * DISP_LIN, root = Math.sqrt(h) * DISP_ROOT;
    return lin < root ? lin : root;
  }

  var GRAZE_LO = 0.62, GRAZE_DEPTH = 0.72;

  var MASK_FADE_Q0 = 2, MASK_FADE_Q1 = 3.5;
  var MASK_FADE_K = 1 / (MASK_FADE_Q1 - MASK_FADE_Q0);

  var MACRO_RADIUS = 5, EXAG_MACRO = 90, MACRO_MIX = 0.62;

  var PROMINENCE_M = 1100, PROMINENCE_GAIN = 0.45;

  function smooth(src, W, H, r) {
    var tmp = new Int16Array(W * H), out = new Int16Array(W * H);
    var inv = 1 / (2 * r + 1), x, y, row, sum, i;

    for (y = 0; y < H; y++) {
      row = y * W;
      sum = 0;
      for (i = -r; i <= r; i++) sum += src[row + (i < 0 ? i + W : i)];
      for (x = 0; x < W; x++) {
        tmp[row + x] = sum * inv;
        i = x + r + 1; if (i >= W) i -= W;
        var j = x - r; if (j < 0) j += W;
        sum += src[row + i] - src[row + j];
      }
    }

    for (x = 0; x < W; x++) {
      sum = 0;
      for (i = -r; i <= r; i++) sum += tmp[(i < 0 ? 0 : i) * W + x];
      for (y = 0; y < H; y++) {
        out[y * W + x] = sum * inv;
        i = y + r + 1; if (i >= H) i = H - 1;
        var j2 = y - r; if (j2 < 0) j2 = 0;
        sum += tmp[i * W + x] - tmp[j2 * W + x];
      }
    }
    return out;
  }

  var LIGHT_TERRAIN = norm3(-0.60, 0.58, 0.55);
  var LIGHT_VIEW = norm3(-0.42, 0.46, 0.78);

  var SNOW_EQUATOR = 5600, SNOW_POLE = 300;

  var ROCK_ALT_LO = 1600, ROCK_ALT_HI = 4200;

  function norm3(x, y, z) {
    var k = 1 / Math.sqrt(x * x + y * y + z * z);
    return [x * k, y * k, z * k];
  }

  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  function Relief(elevImg, biomeImg) {
    var W = this.W = elevImg.width, H = this.H = elevImg.height;
    var pix = readPixels(elevImg);

    var metres = new Float32Array(256);
    for (var c = 0; c < 256; c++) {
      if (c > ELEV_SEA) {
        var t = (c - ELEV_SEA) / 127;
        metres[c] = t * t * ELEV_MAX_M;
      } else {
        var d = (ELEV_SEA - c) / 127;
        metres[c] = -Math.pow(d, 1 / 0.65) * DEPTH_MAX_M;
      }
    }

    var depthM = new Float32Array(258);
    for (var j = 0; j <= 257; j++) {
      var f = j / 256;
      depthM[j] = Math.pow(f > 1 ? 1 : f, 0.65 * 0.72);
    }
    this.depthByM = depthM;
    this.depthIdx = 256 / DEPTH_MAX_M;

    var n = W * H;

    var h = this.h = new Int16Array(n);
    for (var i = 0; i < n; i++) h[i] = metres[pix[i * 4]];

    this.hMacro = smooth(h, W, H, MACRO_RADIUS);

    this.BW = biomeImg.width;
    this.BH = biomeImg.height;
    this.bio = readPixels(biomeImg);

    this.bx = this.BW / W;
    this.by = this.BH / H;
  }

  function readPixels(img) {
    var c = document.createElement('canvas');
    c.width = img.width;
    c.height = img.height;
    var g = c.getContext('2d', { willReadFrequently: true });
    g.drawImage(img, 0, 0);
    return g.getImageData(0, 0, img.width, img.height).data;
  }

  function loadRelief(elevUrl, biomeUrl, done) {
    var wantBitmap = typeof global.createImageBitmap === 'function';

    function one(url) {
      return fetch(url, { cache: 'force-cache' })
        .then(function (r) {
          if (!r.ok) throw new Error(r.status);
          return r.blob();
        })
        .then(function (b) {
          if (wantBitmap) {
            return global.createImageBitmap(b, { colorSpaceConversion: 'none' })
              .catch(function () { return global.createImageBitmap(b); });
          }
          return new Promise(function (res, rej) {
            var im = new Image();
            im.onload = function () { res(im); };
            im.onerror = rej;
            im.src = URL.createObjectURL(b);
          });
        });
    }

    Promise.all([one(elevUrl), one(biomeUrl)]).then(function (imgs) {
      done(new Relief(imgs[0], imgs[1]));
    }).catch(function () {

    });
  }

  function Globe(node, opts) {
    this.node = node;

    this.cities = opts.cities || [];
    this.land = opts.land;

    this._detailUrl = opts.landDetailUrl || null;
    this._fineLand = null;
    this._fineState = null;
    this.onSelect = opts.onSelect || function () {};
    this.selected = null;
    this._raf = null;
    this._frame = null;
    this._dragged = false;
    this._id = 'globe-clip-' + (++uid);
    this.stats = { land: 0, markers: 0, clustered: 0, ms: 0, relief: false };

    this.relief = null;

    this._detail = 1;
    this._refine = null;

    this.size = this._measure();

    this.radius = Math.round(this.size / 2 - this.size * 0.042) - 4;
    this.baseScale = this.radius;
    this.projection = global.d3.geoOrthographic()
      .translate([this.size / 2, this.size / 2])
      .clipAngle(90);
    this.path = global.d3.geoPath(this.projection);

    var c = this._centroid();
    this.rotation = [-c[0], -c[1]];
    this.zoom = this._fitZoom(c);
    this.homeZoom = this.zoom;
    this.homeRotation = this.rotation.slice();

    this._prepLand();
    this._build();
    this._bindDrag();
    this._bindZoom();
    this._bindTheme();
    this._bindResize();
    this.render();

    if (opts.reliefUrl && opts.biomeUrl) {
      var self = this;
      loadRelief(opts.reliefUrl, opts.biomeUrl, function (r) {
        self.setRelief(r);
      });
    }
  }

  Globe.prototype.setRelief = function (relief) {
    this.relief = relief;
    this.node.classList.add('relief');
    this.invalidate();
  };

  Globe.prototype.ZOOM_MIN = 1;
  Globe.prototype.ZOOM_MAX = 24;

  Globe.prototype.DOT_R = 7;

  Globe.prototype.MIN_SEP = 13;

  Globe.prototype._measure = function () {
    var w = this.node.clientWidth || 0;
    return Math.max(280, Math.min(760, Math.round(w || 520)));
  };

  Globe.prototype._centroid = function () {
    if (!this.cities.length) return [10, 50];
    var x = 0, y = 0, z = 0;
    this.cities.forEach(function (c) {
      var lon = c.lon * Math.PI / 180, lat = c.lat * Math.PI / 180;
      x += Math.cos(lat) * Math.cos(lon);
      y += Math.cos(lat) * Math.sin(lon);
      z += Math.sin(lat);
    });
    var n = this.cities.length;
    x /= n; y /= n; z /= n;
    return [Math.atan2(y, x) * 180 / Math.PI,
            Math.atan2(z, Math.sqrt(x * x + y * y)) * 180 / Math.PI];
  };

  Globe.prototype._fitZoom = function (centre) {
    if (this.cities.length < 2) return 1;
    var geoDistance = global.d3.geoDistance, max = 0;
    this.cities.forEach(function (c) {
      max = Math.max(max, geoDistance(centre, [c.lon, c.lat]));
    });
    if (max < 1e-6) return 1;
    return Math.max(1, Math.min(4, 0.62 / Math.sin(Math.min(max, Math.PI / 2))));
  };

  Globe.prototype._prepLand = function () {
    this._rings = [];
    var rings = this._rings;
    function ring(coords) {
      var x = 0, y = 0, z = 0, n = 0;
      for (var i = 0; i < coords.length; i += Math.max(1, coords.length >> 4)) {
        var lon = coords[i][0] * Math.PI / 180, lat = coords[i][1] * Math.PI / 180;
        x += Math.cos(lat) * Math.cos(lon);
        y += Math.cos(lat) * Math.sin(lon);
        z += Math.sin(lat);
        n++;
      }
      if (!n) return;
      rings.push([Math.atan2(y, x) * 180 / Math.PI,
                  Math.atan2(z, Math.sqrt(x * x + y * y)) * 180 / Math.PI]);
    }
    function geom(g) {
      if (!g) return;
      if (g.type === 'Polygon') ring(g.coordinates[0]);
      else if (g.type === 'MultiPolygon') g.coordinates.forEach(function (p) { ring(p[0]); });
      else if (g.type === 'GeometryCollection') g.geometries.forEach(geom);
    }
    var l = this.land;
    if (!l) return;
    if (l.type === 'FeatureCollection') l.features.forEach(function (f) { geom(f.geometry); });
    else if (l.type === 'Feature') geom(l.geometry);
    else geom(l);
  };

  function indexPolys(geo) {
    var parts = [];

    function poly(rings) {
      var ring = rings[0], x = 0, y = 0, z = 0, i, lon, lat, cl;
      for (i = 0; i < ring.length; i++) {
        lon = ring[i][0] * DEG; lat = ring[i][1] * DEG;
        cl = Math.cos(lat);
        x += cl * Math.cos(lon); y += cl * Math.sin(lon); z += Math.sin(lat);
      }
      var k = Math.sqrt(x * x + y * y + z * z);
      if (!k) return;
      x /= k; y /= k; z /= k;

      var dot = 1;
      for (i = 0; i < ring.length; i++) {
        lon = ring[i][0] * DEG; lat = ring[i][1] * DEG;
        cl = Math.cos(lat);
        var d = cl * Math.cos(lon) * x + cl * Math.sin(lon) * y +
                Math.sin(lat) * z;
        if (d < dot) dot = d;
      }
      parts.push({
        poly: rings, x: x, y: y, z: z,
        r: Math.acos(dot < -1 ? -1 : dot > 1 ? 1 : dot)
      });
    }

    function geom(g) {
      if (!g) return;
      if (g.type === 'Polygon') poly(g.coordinates);
      else if (g.type === 'MultiPolygon') g.coordinates.forEach(poly);
      else if (g.type === 'GeometryCollection') g.geometries.forEach(geom);
      else if (g.type === 'Feature') geom(g.geometry);
      else if (g.type === 'FeatureCollection') {
        g.features.forEach(function (f) { geom(f.geometry); });
      }
    }

    geom(geo);
    return parts;
  }

  Globe.prototype.DETAIL_ZOOM = 2;

  Globe.prototype._geo = function () {
    if (this.zoom < this.DETAIL_ZOOM) return this.land;
    if (this._fineParts) return this._visible(this._fineParts);

    if (this._detailUrl && !this._fineState) {
      var self = this;
      this._fineState = 'loading';
      fetch(this._detailUrl, { cache: 'force-cache' })
        .then(function (r) {
          if (!r.ok) throw new Error(r.status);
          return r.json();
        })
        .then(function (g) {
          self._fineState = 'ready';
          self._fineParts = indexPolys(g);
          self.invalidate();
        })
        .catch(function () {

          self._fineState = 'failed';
        });
    }
    return this.land;
  };

  Globe.prototype._visible = function (parts) {
    var lon = -this.rotation[0] * DEG, lat = -this.rotation[1] * DEG;
    var vx = Math.cos(lat) * Math.cos(lon),
        vy = Math.cos(lat) * Math.sin(lon),
        vz = Math.sin(lat);

    var cap = this.zoom > 1 ? Math.asin(1 / this.zoom) : Math.PI / 2;
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      var lim = cap + p.r;
      if (lim >= Math.PI || p.x * vx + p.y * vy + p.z * vz > Math.cos(lim)) {
        out.push(p.poly);
      }
    }
    return { type: 'MultiPolygon', coordinates: out };
  };

  Globe.prototype.setZoom = function (z, about) {
    var next = Math.max(this.ZOOM_MIN, Math.min(this.ZOOM_MAX, z));
    if (next === this.zoom) return;
    this.zoom = next;
    this.invalidate();
    if (this.zoomOut) {
      this.zoomOut.disabled = this.zoom <= this.ZOOM_MIN + 1e-6;
      this.zoomIn.disabled = this.zoom >= this.ZOOM_MAX - 1e-6;
    }
  };

  Globe.prototype._build = function () {
    var s = this.size;

    this.canvas = document.createElement('canvas');
    this.canvas.className = 'basemap';

    this.canvas.setAttribute('aria-hidden', 'true');
    this.node.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');

    var svg = el('svg', {
      viewBox: '0 0 ' + s + ' ' + s,
      role: 'group',
      'aria-label': 'Interactive globe. Select a city to load its calibration report.'
    });

    var probe = el('g', { class: 'probe', 'aria-hidden': 'true' });
    this.pSphere = el('path', { class: 'sphere' });
    this.pLand = el('path', { class: 'land' });
    this.pGrat = el('path', { class: 'graticule' });
    this.pLimb = el('circle', { class: 'limb', r: 0 });
    probe.appendChild(this.pSphere);
    probe.appendChild(this.pLand);
    probe.appendChild(this.pGrat);
    probe.appendChild(this.pLimb);

    this.pRelief = {};
    ['abyss', 'shelf', 'forest', 'desert', 'rock', 'ice', 'shore', 'atmo']
      .forEach(function (name) {
        var n = el('path', { class: 'r-' + name });
        probe.appendChild(n);
        this.pRelief[name] = n;
      }, this);
    svg.appendChild(probe);

    this.markers = el('g', { class: 'markers' });
    this.clusters = el('g', { class: 'clusters' });

    svg.appendChild(this.clusters);
    svg.appendChild(this.markers);

    this.node.appendChild(svg);
    this.svg = svg;

    this.graticule = global.d3.geoGraticule10();

    this._axes = { type: 'MultiLineString', coordinates: [[], []] };
    for (var d = -180; d <= 180; d += 4) this._axes.coordinates[0].push([d, 0]);
    for (var e2 = -90; e2 <= 90; e2 += 2) this._axes.coordinates[1].push([0, e2]);

    this.readout = el('text', {
      class: 'readout', x: 10, y: 18, 'aria-hidden': 'true'
    });
    svg.appendChild(this.readout);

    this._buildMarkers();
    this._buildControls();
    this._readPalette();
  };

  Globe.prototype._readPalette = function () {
    var g = getComputedStyle;
    var sea = rgba(g(this.pSphere).fill) || [30, 60, 90, 1];
    var landc = rgba(g(this.pLand).fill) || [60, 90, 60, 1];
    this.palette = {
      sea: sea,
      seaLight: mix(sea, 255, 0.16),
      seaDark: mix(sea, 0, 0.28),
      land: g(this.pLand).fill,

      landLight: mix(landc, 255, 0.16),
      landDark: mix(landc, 0, 0.28),
      landLine: g(this.pLand).stroke,
      grat: g(this.pGrat).stroke,
      gratOpacity: +g(this.pGrat).opacity || 0.5,

      gratAdd: (sea[0] * 0.299 + sea[1] * 0.587 + sea[2] * 0.114) < 110,
      limb: g(this.pLimb).stroke,

      limbOn: !/^(transparent$|rgba\(.*,\s*0\))/.test(g(this.pLimb).stroke)
    };
    this._readReliefPalette(sea, landc);
  };

  Globe.prototype._readReliefPalette = function (sea, landc) {
    var g = getComputedStyle, p = this.pRelief, out = {};
    function col(name, fallback) {
      var c = rgba(g(p[name]).fill) || fallback;
      return [c[0], c[1], c[2], c[3] == null ? 1 : c[3]];
    }

    out.abyss = col('abyss', [sea[0] * 0.55, sea[1] * 0.55, sea[2] * 0.6, 1]);
    out.shelf = col('shelf', sea);
    out.forest = col('forest', landc);
    out.desert = col('desert', landc);
    out.rock = col('rock', landc);
    out.ice = col('ice', [235, 242, 248, 1]);
    out.shore = col('shore', [120, 200, 255, 1]);
    out.atmo = col('atmo', [90, 170, 255, 1]);

    out.shoreGain = out.shore[3];
    out.atmoGain = out.atmo[3];

    var cs = g(this.node);
    function num(prop, fallback) {
      var v = parseFloat(cs.getPropertyValue(prop));
      return isFinite(v) ? v : fallback;
    }
    out.ambient = num('--relief-ambient', 0.42);
    out.contrast = num('--relief-contrast', 0.86);
    out.seaAmbient = num('--relief-sea-ambient', 0.80);
    out.sun = num('--relief-sun', 0.42);
    out.spec = num('--relief-spec', 0.5);

    this.relPal = out;
  };

  Globe.prototype._buildControls = function () {
    var self = this;
    var wrap = document.createElement('div');
    wrap.className = 'globe-ctl';

    function button(label, title, fn) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      b.title = title;
      b.setAttribute('aria-label', title);
      b.addEventListener('click', fn);
      wrap.appendChild(b);
      return b;
    }

    this.zoomIn = button('+', 'Zoom in', function () {
      self.setZoom(self.zoom * 1.4);
    });
    this.zoomOut = button('\u2212', 'Zoom out', function () {
      self.setZoom(self.zoom / 1.4);
    });
    button('\u2302', 'Reset the view', function () {
      self.rotation = self.homeRotation.slice();
      self.zoom = self.homeZoom;
      self.invalidate();
    });

    this.node.appendChild(wrap);
    this.zoomOut.disabled = this.zoom <= this.ZOOM_MIN + 1e-6;
  };

  Globe.prototype._buildMarkers = function () {
    var self = this;
    this.nodes = [];

    function make(d) {
      var g = el('g', {
        class: 'mk ' + tier(d.bss),
        tabindex: '0',
        role: 'button'
      });

      var r = self.DOT_R;
      g.appendChild(el('circle', { class: 'halo', r: r + 2.5 }));
      g.appendChild(el('circle', { class: 'dot', r: r }));

      var tip = d.name + ' (' + d.country + ')\nSkill score ' + d.bss.toFixed(2) +
          '\n' + d.n + ' days, rain on ' + Math.round(d.base_rate * 100) + '%' +
          (d.rank_lo == null
            ? '\nProvisional: shorter record, not ranked'
            : '\nRank ' + d.rank_lo.toFixed(0) + '-' + d.rank_hi.toFixed(0) +
              ' of ' + self.cities.filter(function (c) {
                return c.rank_lo != null; }).length);
      g.setAttribute('data-tip', tip);
      g.setAttribute('aria-label', tip.replace(/\n/g, '. '));

      g.setAttribute('data-slug', d.slug);

      g.addEventListener('click', function () {

        if (self._dragged) return;
        self.onSelect(d.slug);
      });
      g.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          self.onSelect(d.slug);
        }
      });
      self.nodes.push({
        d: d, g: g, r: r,

        u: (function () {
          var lon = d.lon * Math.PI / 180, lat = d.lat * Math.PI / 180;
          return [Math.cos(lat) * Math.cos(lon),
                  Math.cos(lat) * Math.sin(lon),
                  Math.sin(lat)];
        })(),
        shown: true, x: 0, y: 0
      });
      self.markers.appendChild(g);
      return g;
    }

    this.cities.forEach(make);

    this.order = this.nodes.slice().sort(function (a, b) {
      return (b.d.n || 0) - (a.d.n || 0);
    });

    this._clusterPool = [];
  };

  Globe.prototype._raise = function (g) {
    if (g.parentNode && g.parentNode.lastChild !== g) g.parentNode.appendChild(g);
  };

  Globe.prototype._bindDrag = function () {
    var self = this, dragging = false, last = null, moved = 0;

    this.node.addEventListener('pointerdown', function (e) {
      if (e.target.closest && e.target.closest('.globe-ctl')) return;
      dragging = true; moved = 0; self._dragged = false;
      self.node.classList.add('dragging');
      last = [e.clientX, e.clientY];
    });

    global.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var dx = e.clientX - last[0], dy = e.clientY - last[1];
      moved += Math.abs(dx) + Math.abs(dy);
      last = [e.clientX, e.clientY];

      var k = 90 / self.projection.scale();
      self.rotation = [
        self.rotation[0] + dx * k,

        Math.max(-90, Math.min(90, self.rotation[1] - dy * k))
      ];
      self.invalidate();
    });

    function end() {
      if (!dragging) return;
      dragging = false;
      self.node.classList.remove('dragging');
      self._dragged = moved > 6;
    }
    global.addEventListener('pointerup', end);
    global.addEventListener('pointercancel', end);

    this.node.setAttribute('tabindex', '0');
    this.node.addEventListener('keydown', function (e) {
      var step = e.shiftKey ? 20 : 8, r = self.rotation, used = true;
      if (e.key === 'ArrowLeft') r[0] -= step;
      else if (e.key === 'ArrowRight') r[0] += step;
      else if (e.key === 'ArrowUp') r[1] = Math.min(90, r[1] + step);
      else if (e.key === 'ArrowDown') r[1] = Math.max(-90, r[1] - step);
      else if (e.key === '+' || e.key === '=') self.setZoom(self.zoom * 1.4);
      else if (e.key === '-' || e.key === '_') self.setZoom(self.zoom / 1.4);
      else used = false;
      if (used) { e.preventDefault(); self.invalidate(); }
    });
  };

  Globe.prototype._bindZoom = function () {
    var self = this;
    this.node.addEventListener('wheel', function (e) {

      if (!e.ctrlKey && Math.abs(e.deltaY) < 4) return;
      e.preventDefault();
      self.setZoom(self.zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
    }, { passive: false });
  };

  Globe.prototype._bindTheme = function () {
    var self = this;
    if (!global.MutationObserver) return;
    this._themeObs = new MutationObserver(function () {
      self._readPalette();
      self.invalidate();
    });
    this._themeObs.observe(document.documentElement,
                           { attributes: true, attributeFilter: ['data-theme', 'data-weather'] });
  };

  Globe.prototype._bindResize = function () {
    var self = this;
    if (!global.ResizeObserver) return;
    this._resObs = new ResizeObserver(function () { self.resize(); });
    this._resObs.observe(this.node);
  };

  Globe.prototype.resize = function () {
    var s = this._measure();
    if (s === this.size) return;
    this.size = s;
    this.radius = Math.round(s / 2 - s * 0.042) - 4;
    this.baseScale = this.radius;
    this.projection.translate([s / 2, s / 2]);
    this.svg.setAttribute('viewBox', '0 0 ' + s + ' ' + s);
    this.invalidate();
  };

  Globe.prototype.invalidate = function () {
    var self = this;
    if (this._frame) return;
    this._frame = requestAnimationFrame(function () {
      self._frame = null;
      self.render();
    });
  };

  Globe.prototype.render = function () {
    var t0 = performance.now();
    this.projection.rotate(this.rotation).scale(this.baseScale * this.zoom);
    this._drawBase();
    this._drawMarkers();
    this.stats.ms = performance.now() - t0;
  };

  Globe.prototype._drawBase = function () {
    var s = this.size, ctx = this.ctx;
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    var px = Math.round(s * dpr);
    if (this.canvas.width !== px) {
      this.canvas.width = px;
      this.canvas.height = px;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, s, s);

    this._limb = null;
    if (this.relief) this._paintRelief();
    else this._paintVector();

    this._paintOverlay();
    this._countVisibleLand();
  };

  Globe.prototype._countVisibleLand = function () {
    var cLon = -this.rotation[0] * DEG, cLat = -this.rotation[1] * DEG;
    var ux = Math.cos(cLat) * Math.cos(cLon),
        uy = Math.cos(cLat) * Math.sin(cLon),
        uz = Math.sin(cLat), n = 0;
    for (var i = 0; i < this._rings.length; i++) {
      var a = this._rings[i][0] * DEG, b = this._rings[i][1] * DEG;
      if (Math.cos(b) * Math.cos(a) * ux + Math.cos(b) * Math.sin(a) * uy +
          Math.sin(b) * uz > 0) n++;
    }
    this.stats.land = n;
  };

  Globe.prototype._paintVector = function () {
    var s = this.size, ctx = this.ctx, p = this.palette;
    var r = this.radius, cx = s / 2, cy = s / 2;
    var geoPath = global.d3.geoPath(this.projection, ctx);

    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.clip();

    var grad = ctx.createRadialGradient(
      cx - r * 0.35, cy - r * 0.4, r * 0.05, cx, cy, r * 1.08);
    grad.addColorStop(0, p.seaLight);
    grad.addColorStop(0.55, css(p.sea));
    grad.addColorStop(1, p.seaDark);
    ctx.beginPath();
    geoPath({ type: 'Sphere' });
    ctx.fillStyle = grad;
    ctx.fill();

    if (this.land) {
      var geo = this._geo();
      ctx.beginPath();
      geoPath(geo);

      var lgrad = ctx.createRadialGradient(
        cx - r * 0.35, cy - r * 0.4, r * 0.05, cx, cy, r * 1.08);
      lgrad.addColorStop(0, p.landLight);
      lgrad.addColorStop(0.55, p.land);
      lgrad.addColorStop(1, p.landDark);
      ctx.fillStyle = lgrad;
      ctx.fill();
      ctx.strokeStyle = p.landLine;
      ctx.lineWidth = 0.6;
      ctx.lineJoin = 'round';
      ctx.stroke();
    }

    var vig = ctx.createRadialGradient(cx, cy, r * 0.72, cx, cy, r);
    vig.addColorStop(0, 'rgba(0,0,0,0)');
    vig.addColorStop(1, 'rgba(0,0,0,0.22)');
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = vig;
    ctx.fill();
    ctx.restore();
  };

  Globe.prototype._edgeMargin = function () {
    return Math.max(4, this.size * 0.019);
  };

  Globe.prototype._dispScale = function () {
    if (!this.relief || this.zoom > 1 + 1e-6) return 0;
    var rad = this.radius * this.zoom;
    var room = this._edgeMargin() * 0.72 / (rad * DISP_TOP);
    return room < 1 ? room : 1;
  };

  Globe.prototype._limbProfile = function () {
    var disp = this._dispScale();
    if (!disp) return null;

    var R = this.relief, h = R.h, W = R.W, H = R.H;
    var rad = this.radius * this.zoom;

    var N = Math.round(2 * Math.PI * rad * this._detail / 1.2);
    N = N < 256 ? 256 : N > 2048 ? 2048 : N;

    var p = this._limbBuf;
    if (!p || p.n !== N) {
      p = this._limbBuf = {
        n: N, max: 1,
        r: new Float32Array(N),
        vx: new Float32Array(N), vy: new Float32Array(N), vz: new Float32Array(N),
        wx: new Float32Array(N), wy: new Float32Array(N), wz: new Float32Array(N),

        hx: new Float32Array(N), hy: new Float32Array(N), hz: new Float32Array(N)
      };
    }

    var lon0 = -this.rotation[0] * DEG, lat0 = -this.rotation[1] * DEG;
    var clo = Math.cos(lon0), slo = Math.sin(lon0);
    var cla = Math.cos(lat0), sla = Math.sin(lat0);
    var fx = cla * clo, fy = cla * slo, fz = sla;
    var ex = -slo, ey = clo;
    var nx = -sla * clo, ny = -sla * slo, nz = cla;

    var INV_TWO_PI = 1 / (2 * Math.PI), INV_PI = 1 / Math.PI;
    var M = LIMB_STEPS, step = 2 * Math.PI / N, max = 1;

    for (var j = 0; j < N; j++) {

      var th = (j + 0.5) * step - Math.PI;
      var ux = Math.cos(th), uy = -Math.sin(th);
      var bR = 1, bvx = ux, bvy = uy, bvz = 0;
      var bwx = ux * ex + uy * nx, bwy = ux * ey + uy * ny, bwz = uy * nz;

      for (var m = 0; m < M; m++) {
        var phi = LIMB_SWEEP * Math.sqrt(m / (M - 1));
        var c = Math.cos(phi), sn = Math.sin(phi);
        var qx = ux * c, qy = uy * c;
        var dwx = qx * ex + qy * nx + sn * fx;
        var dwy = qx * ey + qy * ny + sn * fy;
        var dwz = qy * nz + sn * fz;

        var lat = Math.asin(dwz > 1 ? 1 : dwz < -1 ? -1 : dwz);
        var fu = (Math.atan2(dwy, dwx) * INV_TWO_PI + 0.5) * W - 0.5;
        var fv = (0.5 - lat * INV_PI) * H - 0.5;
        var iu = Math.floor(fu), iv = Math.floor(fv);
        var au = fu - iu, av = fv - iv;
        var u0 = iu < 0 ? iu + W : iu >= W ? iu - W : iu;
        var u1 = u0 + 1 >= W ? 0 : u0 + 1;
        var v0 = iv < 0 ? 0 : iv >= H ? H - 1 : iv;
        var v1 = v0 + 1 >= H ? H - 1 : v0 + 1;
        var r0 = v0 * W, r1 = v1 * W;
        var ht = h[r0 + u0] + (h[r0 + u1] - h[r0 + u0]) * au;
        var hb = h[r1 + u0] + (h[r1 + u1] - h[r1 + u0]) * au;
        var hm = ht + (hb - ht) * av;
        if (hm <= 0) continue;

        var rr = (1 + displace(hm) * disp) * c;
        if (rr > bR) {
          bR = rr;
          bvx = qx; bvy = qy; bvz = sn;
          bwx = dwx; bwy = dwy; bwz = dwz;
        }
      }

      p.r[j] = bR;
      p.vx[j] = bvx; p.vy[j] = bvy; p.vz[j] = bvz;
      p.wx[j] = bwx; p.wy[j] = bwy; p.wz[j] = bwz;
      p.hx[j] = ux * ex + uy * nx;
      p.hy[j] = ux * ey + uy * ny;
      p.hz[j] = uy * nz;
      if (bR > max) max = bR;
    }

    p.max = max;

    var r = p.r, sm = p.smooth;
    if (!sm || sm.length !== N) sm = p.smooth = new Float32Array(N);
    for (var q = 0; q < N; q++) {
      var a2 = r[q - 2 < 0 ? q - 2 + N : q - 2], a1 = r[q - 1 < 0 ? N - 1 : q - 1];
      var b1 = r[q + 1 >= N ? q + 1 - N : q + 1],
          b2 = r[q + 2 >= N ? q + 2 - N : q + 2];
      sm[q] = (a2 + 4 * a1 + 6 * r[q] + 4 * b1 + b2) * 0.0625;
    }
    p.r = sm;
    p.smooth = r;
    return p;
  };

  Globe.prototype._paintRelief = function () {
    var R = this.relief, pal = this.relPal;
    var s = this.size, ctx = this.ctx;

    var n = Math.max(64, Math.round(s * this._detail));
    var buf = this._reliefBuffer(n);
    var out = buf.img.data;
    var step = s / n;

    var cx = s / 2, cy = s / 2;
    var rad = this.radius * this.zoom;

    var prof = this._limb = this._limbProfile();
    var disp = prof ? this._dispScale() : 0;
    var dispCap = DISP_CAP * disp * DISP_TOP;
    var clip = this.radius * (prof ? prof.max : 1);
    var profN = prof ? prof.n : 0;
    var profK = prof ? profN / (2 * Math.PI) : 0;
    var profR = prof && prof.r, profVx = prof && prof.vx,
        profVy = prof && prof.vy, profVz = prof && prof.vz,
        profWx = prof && prof.wx, profWy = prof && prof.wy,
        profWz = prof && prof.wz,
        profHx = prof && prof.hx, profHy = prof && prof.hy,
        profHz = prof && prof.hz;

    var W = R.W, H = R.H, h = R.h, hMac = R.hMacro;
    var bio = R.bio, BW = R.BW, BH = R.BH, bxk = R.bx, byk = R.by;
    var depthByM = R.depthByM, depthIdx = R.depthIdx;

    var lon0 = -this.rotation[0] * DEG, lat0 = -this.rotation[1] * DEG;
    var clo = Math.cos(lon0), slo = Math.sin(lon0);
    var cla = Math.cos(lat0), sla = Math.sin(lat0);
    var fx = cla * clo, fy = cla * slo, fz = sla;
    var ex = -slo, ey = clo;
    var nx = -sla * clo, ny = -sla * slo, nz = cla;

    var ltx = LIGHT_TERRAIN[0], lty = LIGHT_TERRAIN[1], ltz = LIGHT_TERRAIN[2];
    var lvx = LIGHT_VIEW[0], lvy = LIGHT_VIEW[1], lvz = LIGHT_VIEW[2];

    var hvx = lvx, hvy = lvy, hvz = lvz + 1;
    var hk = 1 / Math.sqrt(hvx * hvx + hvy * hvy + hvz * hvz);
    hvx *= hk; hvy *= hk; hvz *= hk;

    var abyss = pal.abyss, shelf = pal.shelf, forest = pal.forest,
        desert = pal.desert, rock = pal.rock, ice = pal.ice,
        shore = pal.shore;

    var shoreGain = pal.shoreGain / this.zoom;

    var mask = this._landMask(n);

    var ambient = pal.ambient, contrast = pal.contrast;
    var seaAmbient = pal.seaAmbient, seaContrast = contrast * 0.4;
    var sun = pal.sun, sunBase = 1 - sun * 0.72, spec = pal.spec;

    var dxBase = CIRC_M / W, dyBase = MERID_M / H;
    var INV_TWO_PI = 1 / (2 * Math.PI), INV_PI = 1 / Math.PI;
    var clip2 = clip * clip, invRad = 1 / rad;

    var covK = rad / step, paraLo2 = PARALLAX_LO * PARALLAX_LO;

    var maskQk = (step / rad) * W * INV_TWO_PI;
    var grazeLo2 = GRAZE_LO * GRAZE_LO, grazeK = 1 / (1 - grazeLo2);

    var i = 0;
    for (var py = 0; py < n; py++) {
      var sy = (py + 0.5) * step - cy;
      for (var pxi = 0; pxi < n; pxi++, i += 4) {
        var sx = (pxi + 0.5) * step - cx;
        var dd = sx * sx + sy * sy;
        if (dd > clip2) { out[i + 3] = 0; continue; }

        var X = sx * invRad, Y = sy * invRad;
        var rho2 = X * X + Y * Y;
        var vx, vy, vz, wx, wy, wz, lat, lon;
        var alpha = 255, fringe = false, shiftPx = 0;

        if (rho2 >= 1) {

          if (!prof) { out[i + 3] = 0; continue; }
          var th = Math.atan2(sy, sx) + Math.PI;
          var pj = (th * profK) | 0;
          if (pj >= profN) pj = profN - 1; else if (pj < 0) pj = 0;
          var pr = profR[pj], rhoF = Math.sqrt(rho2);
          var cov = (pr - rhoF) * covK;
          if (cov <= 0) { out[i + 3] = 0; continue; }
          if (cov < 1) alpha = cov * 255;

          var ft = pr > 1.000001 ? (rhoF - 1) / (pr - 1) : 0;
          if (ft < 0) ft = 0; else if (ft > 1) ft = 1;
          var hxj = profHx[pj], hyj = profHy[pj], hzj = profHz[pj];
          wx = hxj + (profWx[pj] - hxj) * ft;
          wy = hyj + (profWy[pj] - hyj) * ft;
          wz = hzj + (profWz[pj] - hzj) * ft;
          var wl = 1 / Math.sqrt(wx * wx + wy * wy + wz * wz);
          wx *= wl; wy *= wl; wz *= wl;
          vx = profVx[pj]; vy = profVy[pj]; vz = profVz[pj] * ft;
          fringe = true;
          lat = Math.asin(wz > 1 ? 1 : wz < -1 ? -1 : wz);
          lon = Math.atan2(wy, wx);
        } else {

        vz = Math.sqrt(1 - rho2); vx = X; vy = -Y;
        wx = vx * ex + vy * nx + vz * fx;
        wy = vx * ey + vy * ny + vz * fy;
        wz = vy * nz + vz * fz;

        lat = Math.asin(wz > 1 ? 1 : wz < -1 ? -1 : wz);
        lon = Math.atan2(wy, wx);

        if (rho2 > paraLo2 && disp) {
          var qu = ((lon * INV_TWO_PI + 0.5) * W) | 0;
          var qv = ((0.5 - lat * INV_PI) * H) | 0;
          if (qu >= W) qu = W - 1; else if (qu < 0) qu = 0;
          if (qv >= H) qv = H - 1; else if (qv < 0) qv = 0;
          var qh = h[qv * W + qu];
          if (qh > 0) {

            var rho = Math.sqrt(rho2), amp = rho / vz;
            var off = displace(qh) * disp * amp;
            if (off > dispCap) off = dispCap;
            var lift = off / amp;

            var qx = vx * (1 - lift), qy = vy * (1 - lift),
                qz = vz + off * rho;
            var ql = 1 / Math.sqrt(qx * qx + qy * qy + qz * qz);
            qx *= ql; qy *= ql; qz *= ql;
            wx = qx * ex + qy * nx + qz * fx;
            wy = qx * ey + qy * ny + qz * fy;
            wz = qy * nz + qz * fz;
            lat = Math.asin(wz > 1 ? 1 : wz < -1 ? -1 : wz);
            lon = Math.atan2(wy, wx);
            shiftPx = lift * rad;
          }
        }

        }

        var fu = (lon * INV_TWO_PI + 0.5) * W;
        var fv = (0.5 - lat * INV_PI) * H;
        var tu = fu | 0, tv = fv | 0;
        if (tu >= W) tu = W - 1; else if (tu < 0) tu = 0;
        if (tv >= H) tv = H - 1; else if (tv < 0) tv = 0;

        var gu = fu - 0.5, gv = fv - 0.5;
        var iu = Math.floor(gu), iv = Math.floor(gv);
        var au = gu - iu, av = gv - iv;
        var u0 = iu < 0 ? iu + W : iu >= W ? iu - W : iu;
        var u1 = u0 + 1 >= W ? 0 : u0 + 1;
        var v0 = iv < 0 ? 0 : iv >= H ? H - 1 : iv;
        var v1 = v0 + 1 >= H ? H - 1 : v0 + 1;
        var r0 = v0 * W, r1 = v1 * W;
        var h00 = h[r0 + u0], h10 = h[r0 + u1],
            h01 = h[r1 + u0], h11 = h[r1 + u1];
        var hTop = h00 + (h10 - h00) * au;
        var hBot = h01 + (h11 - h01) * au;
        var hm = hTop + (hBot - hTop) * av;

        var isLand;
        if (fringe) {

          isLand = true;
          if (hm < 1) hm = 1;
        } else if (!mask) {
          isLand = hm > 0;
        } else {

          var lm = mask[i + 3] * (1 / 255);
          var lr = hm > 12 ? 1 : hm < -12 ? 0 : (hm + 12) * (1 / 24);
          var t = (shiftPx - 0.6) * (1 / 1.0);

          var tg = (maskQk / vz - MASK_FADE_Q0) * MASK_FADE_K;
          if (tg > t) t = tg;
          if (t < 0) t = 0; else if (t > 1) t = 1;
          var landness = lm + (lr - lm) * (t * t * (3 - 2 * t));
          isLand = landness > 0.5;

          if (isLand) { if (hm < 0) hm = 0; }
          else if (hm > 0) hm = -20;
        }

        var m00 = hMac[r0 + u0], m10 = hMac[r0 + u1],
            m01 = hMac[r1 + u0], m11 = hMac[r1 + u1];
        var mTop = m00 + (m10 - m00) * au;
        var mBot = m01 + (m11 - m01) * au;
        var hmac = mTop + (mBot - mTop) * av;

        var um = u0 - 1 < 0 ? W - 1 : u0 - 1;
        var up = u1 + 1 >= W ? 0 : u1 + 1;
        var rm = (v0 - 1 < 0 ? 0 : v0 - 1) * W;
        var rp = (v1 + 1 >= H ? H - 1 : v1 + 1) * W;

        var gx00 = h[r0 + u1] - h[r0 + um], gx10 = h[r0 + up] - h[r0 + u0],
            gx01 = h[r1 + u1] - h[r1 + um], gx11 = h[r1 + up] - h[r1 + u0];
        var gy00 = h[r1 + u0] - h[rm + u0], gy10 = h[r1 + u1] - h[rm + u1],
            gy01 = h[rp + u0] - h[r0 + u0], gy11 = h[rp + u1] - h[r0 + u1];
        var du = ((gx00 + (gx10 - gx00) * au) * (1 - av) +
                  (gx01 + (gx11 - gx01) * au) * av) * 0.5;
        var dv = ((gy00 + (gy10 - gy00) * au) * (1 - av) +
                  (gy01 + (gy11 - gy01) * au) * av) * 0.5;

        var px00 = hMac[r0 + u1] - hMac[r0 + um],
            px10 = hMac[r0 + up] - hMac[r0 + u0],
            px01 = hMac[r1 + u1] - hMac[r1 + um],
            px11 = hMac[r1 + up] - hMac[r1 + u0];
        var py00 = hMac[r1 + u0] - hMac[rm + u0],
            py10 = hMac[r1 + u1] - hMac[rm + u1],
            py01 = hMac[rp + u0] - hMac[r0 + u0],
            py11 = hMac[rp + u1] - hMac[r0 + u1];
        var mdu = ((px00 + (px10 - px00) * au) * (1 - av) +
                   (px01 + (px11 - px01) * au) * av) * 0.5;
        var mdv = ((py00 + (py10 - py00) * au) * (1 - av) +
                   (py01 + (py11 - py01) * au) * av) * 0.5;

        var cosLat = Math.sqrt(wx * wx + wy * wy);
        if (cosLat < 0.06) cosLat = 0.06;
        var k = (isLand ? EXAG : SEA_EXAG);
        var sx_ = du * k / (dxBase * cosLat);
        var sy_ = -dv * k / dyBase;

        var nlen = Math.sqrt(sx_ * sx_ + sy_ * sy_ + 1);
        var hill = (-sx_ * ltx - sy_ * lty + ltz) / nlen;
        if (hill < 0) hill = 0;

        if (isLand) {
          var mx_ = mdu * (EXAG_MACRO / (dxBase * cosLat));
          var my_ = -mdv * (EXAG_MACRO / dyBase);
          var mlen = Math.sqrt(mx_ * mx_ + my_ * my_ + 1);
          var mhill = (-mx_ * ltx - my_ * lty + ltz) / mlen;
          if (mhill < 0) mhill = 0;
          hill = hill * (1 - MACRO_MIX) + mhill * MACRO_MIX;

          var prom = (hm - hmac) * (1 / PROMINENCE_M);
          if (prom > 1) prom = 1; else if (prom < -1) prom = -1;
          hill *= 1 + PROMINENCE_GAIN * prom;
          if (hill < 0) hill = 0;
        }

        if (isLand && rho2 > grazeLo2) {
          var tvx = fx - vz * wx, tvy = fy - vz * wy, tvz = fz - vz * wz;
          var invCos = 1 / cosLat;
          var te = (wx * tvy - wy * tvx) * invCos;
          var tn = cosLat * tvz - wz * (wx * tvx + wy * tvy) * invCos;
          var vis = (-sx_ * te - sy_ * tn + vz) / nlen;
          vis *= 3;
          if (vis > 1) vis = 1; else if (vis < 0) vis = 0;
          vis = vis * vis * (3 - 2 * vis);
          var gz = (rho2 - grazeLo2) * grazeK;
          if (gz > 1) gz = 1;
          hill *= 1 - gz * GRAZE_DEPTH * (1 - vis);
        }

        var sph = vx * lvx + vy * lvy + vz * lvz;
        if (sph < 0) sph = 0;

        var bu = fu * bxk - 0.5, bv = fv * byk - 0.5;
        var ju = Math.floor(bu), jv = Math.floor(bv);
        var bau = bu - ju, bav = bv - jv;
        var bu0 = ju < 0 ? ju + BW : ju >= BW ? ju - BW : ju;
        var bu1 = bu0 + 1 >= BW ? 0 : bu0 + 1;
        var bv0 = jv < 0 ? 0 : jv >= BH ? BH - 1 : jv;
        var bv1 = bv0 + 1 >= BH ? BH - 1 : bv0 + 1;
        var q0 = bv0 * BW, q1 = bv1 * BW;
        var b00 = (q0 + bu0) * 4, b10 = (q0 + bu1) * 4,
            b01 = (q1 + bu0) * 4, b11 = (q1 + bu1) * 4;
        var w10 = bau * (1 - bav), w00 = (1 - bau) * (1 - bav),
            w11 = bau * bav, w01 = (1 - bau) * bav;
        var veg = (bio[b00] * w00 + bio[b10] * w10 +
                   bio[b01] * w01 + bio[b11] * w11) * (1 / 255);
        var snow = (bio[b00 + 1] * w00 + bio[b10 + 1] * w10 +
                    bio[b01 + 1] * w01 + bio[b11 + 1] * w11) * (1 / 255);
        var coast = (bio[b00 + 2] * w00 + bio[b10 + 2] * w10 +
                     bio[b01 + 2] * w01 + bio[b11 + 2] * w11) * (1 / 255);

        var cr, cg, cb, shade;
        if (isLand) {

          var steep = 1 - 1 / nlen;

          var alt = (hm - ROCK_ALT_LO) * (1 / (ROCK_ALT_HI - ROCK_ALT_LO));
          alt = alt < 0 ? 0 : alt > 1 ? 1 : alt;
          alt = alt * alt * (3 - 2 * alt);
          var bare = alt * 0.55 + steep * 0.60;
          if (bare > 0.90) bare = 0.90;

          cr = desert[0] + (forest[0] - desert[0]) * veg;
          cg = desert[1] + (forest[1] - desert[1]) * veg;
          cb = desert[2] + (forest[2] - desert[2]) * veg;
          cr += (rock[0] - cr) * bare;
          cg += (rock[1] - cg) * bare;
          cb += (rock[2] - cb) * bare;

          var line = SNOW_EQUATOR - (SNOW_EQUATOR - SNOW_POLE) *
                     Math.pow(wz < 0 ? -wz : wz, 2.2);

          var cap = (hm - line) * (1 / 2200);
          cap = cap < 0 ? 0 : cap > 1 ? 1 : cap;
          cap *= 0.72 - 0.34 * steep;
          var sn = snow * (0.55 + 0.45 * snow);
          if (cap > sn) sn = cap;
          cr += (ice[0] - cr) * sn;
          cg += (ice[1] - cg) * sn;
          cb += (ice[2] - cb) * sn;
          shade = (ambient + contrast * hill) * (sunBase + sun * sph);
        } else {
          var di = (-hm * depthIdx) | 0;
          var dep = depthByM[di > 256 ? 256 : di];
          cr = shelf[0] + (abyss[0] - shelf[0]) * dep;
          cg = shelf[1] + (abyss[1] - shelf[1]) * dep;
          cb = shelf[2] + (abyss[2] - shelf[2]) * dep;

          if (snow > 0.02) {
            cr += (ice[0] - cr) * snow;
            cg += (ice[1] - cg) * snow;
            cb += (ice[2] - cb) * snow;
          }

          shade = (seaAmbient + seaContrast * hill) * (sunBase + sun * sph * 0.7);

          if (spec > 0) {
            var sp = vx * hvx + vy * hvy + vz * hvz;
            if (sp > 0) {
              sp *= sp; sp *= sp; sp *= sp; sp *= sp; sp *= sp;
              shade += sp * spec;
            }
          }
        }

        cr *= shade; cg *= shade; cb *= shade;

        if (!isLand && coast > 0.9) {
          var e = (coast - 0.9) * 10;
          e = e * e * shoreGain;
          cr += shore[0] * e; cg += shore[1] * e; cb += shore[2] * e;
        }

        out[i] = cr;
        out[i + 1] = cg;
        out[i + 2] = cb;
        out[i + 3] = alpha;
      }
    }

    buf.ctx.putImageData(buf.img, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(buf.canvas, 0, 0, s, s);
    this.stats.relief = true;
    this._scheduleRefine();
  };

  Globe.prototype._landMask = function (n) {
    var geo = this._geo();
    if (!geo) return null;
    var m = this._mask;
    if (!m || m.canvas.width !== n) {
      var c = document.createElement('canvas');
      c.width = n; c.height = n;
      m = this._mask = {
        canvas: c, ctx: c.getContext('2d', { willReadFrequently: true })
      };
    }
    var g = m.ctx, k = n / this.size;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, n, n);
    g.setTransform(k, 0, 0, k, 0, 0);
    g.beginPath();
    global.d3.geoPath(this.projection, g)(geo);
    g.fillStyle = '#fff';
    g.fill();
    g.setTransform(1, 0, 0, 1, 0, 0);
    return g.getImageData(0, 0, n, n).data;
  };

  Globe.prototype._reliefBuffer = function (n) {
    var b = this._buf;
    if (!b || b.canvas.width !== n) {
      var c = document.createElement('canvas');
      c.width = n; c.height = n;
      var g = c.getContext('2d', { willReadFrequently: true });
      b = this._buf = { canvas: c, ctx: g, img: g.createImageData(n, n) };
    }
    return b;
  };

  Globe.prototype._scheduleRefine = function () {
    var self = this, ms = this.stats.ms;

    if (ms > 26 && this._detail > 0.4) this._detail = Math.max(0.4, this._detail * 0.7);
    else if (ms < 9 && this._detail < 1) this._detail = Math.min(1, this._detail * 1.25);

    if (this._detail >= 1 || this._refine) return;
    this._refine = setTimeout(function () {
      self._refine = null;
      var was = self._detail;
      self._detail = 1;
      self.render();

      self._detail = was;
    }, 130);
  };

  Globe.prototype._paintOverlay = function () {
    var s = this.size, ctx = this.ctx, p = this.palette, rp = this.relPal;
    var r = this.radius, cx = s / 2, cy = s / 2;
    var geoPath = global.d3.geoPath(this.projection, ctx);

    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.clip();

    var add = this.relief && p.gratAdd;
    if (add) ctx.globalCompositeOperation = 'lighter';

    ctx.beginPath();
    geoPath(this.graticule);
    ctx.strokeStyle = p.grat;
    ctx.globalAlpha = p.gratOpacity * (this.relief ? (add ? 0.9 : 0.55) : 1);
    ctx.lineWidth = 0.5;
    ctx.stroke();

    ctx.beginPath();
    geoPath(this._axes);
    ctx.globalAlpha = Math.min(1, p.gratOpacity * 1.5);
    ctx.lineWidth = 0.8;
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
    ctx.restore();

    var lp = this._limb;
    if (!p.limbOn) { this._paintReadout(); return; }
    ctx.beginPath();
    if (lp) {
      var lstep = 2 * Math.PI / lp.n;
      for (var lj = 0; lj < lp.n; lj++) {
        var lth = (lj + 0.5) * lstep - Math.PI, lr = r * lp.r[lj];
        var lx = cx + Math.cos(lth) * lr, ly = cy + Math.sin(lth) * lr;
        if (lj) ctx.lineTo(lx, ly); else ctx.moveTo(lx, ly);
      }
      ctx.closePath();
    } else {
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
    }
    ctx.strokeStyle = p.limb;
    ctx.globalAlpha = 0.8;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.globalAlpha = 1;

    this._paintReadout();
  };

  Globe.prototype._paintReadout = function () {
    if (this.readout) {
      var lat = -this.rotation[1], lon = ((-this.rotation[0] + 540) % 360) - 180;
      this.readout.textContent =
        Math.abs(lat).toFixed(1) + '\u00b0' + (lat >= 0 ? 'N' : 'S') + '  ' +
        Math.abs(lon).toFixed(1) + '\u00b0' + (lon >= 0 ? 'E' : 'W') +
        '   \u00d7' + (this.zoom < 10 ? this.zoom.toFixed(1) : Math.round(this.zoom));
    }
  };

  Globe.prototype._drawMarkers = function () {
    var proj = this.projection, live = [];
    var cLon = -this.rotation[0] * Math.PI / 180,
        cLat = -this.rotation[1] * Math.PI / 180;
    var ux = Math.cos(cLat) * Math.cos(cLon),
        uy = Math.cos(cLat) * Math.sin(cLon),
        uz = Math.sin(cLat);

    var cx = this.size / 2, cy = this.size / 2, rad = this.radius;
    var rad2 = rad * rad;

    for (var i = 0; i < this.nodes.length; i++) {
      var m = this.nodes[i];

      m.front = m.u[0] * ux + m.u[1] * uy + m.u[2] * uz > 0;
      if (!m.front) { m.want = false; continue; }
      var p = proj([m.d.lon, m.d.lat]);
      if (!p) { m.want = false; continue; }
      var ddx = p[0] - cx, ddy = p[1] - cy;
      if (ddx * ddx + ddy * ddy > rad2) { m.want = false; continue; }
      m.x = p[0]; m.y = p[1];
      m.want = true;
      live.push(m);
    }

    var clusters = this._declutter(live);

    var shownCount = 0;
    for (i = 0; i < this.nodes.length; i++) {
      var k = this.nodes[i];
      if (k.want) {
        k.g.setAttribute('transform',
          'translate(' + k.x.toFixed(1) + ',' + k.y.toFixed(1) + ')');
        shownCount++;
      }
      if (k.want !== k.shown) {
        k.shown = k.want;
        if (k.want) {
          k.g.removeAttribute('display');
          k.g.removeAttribute('aria-hidden');
        } else {
          k.g.setAttribute('display', 'none');
          k.g.setAttribute('aria-hidden', 'true');
        }
      }
    }

    this._drawClusters(clusters);
    this.stats.markers = shownCount;
  };

  Globe.prototype._declutter = function (live) {
    var sep = this.MIN_SEP, sep2 = sep * sep, grid = {}, clusters = [];
    var hidden = 0, placed = [];

    function key(cx, cy) { return cx + ',' + cy; }

    var order = this.order, sel = null;
    for (var s = 0; s < order.length; s++) {
      if (order[s].d.slug === this.selected) {
        sel = order[s];
        break;
      }
    }

    for (var i = -1; i < order.length; i++) {
      var m = i < 0 ? sel : order[i];
      if (!m || !m.want || (i >= 0 && m === sel)) continue;
      var gx = Math.floor(m.x / sep), gy = Math.floor(m.y / sep);
      var host = null;

      for (var ax = gx - 1; ax <= gx + 1 && !host; ax++) {
        for (var ay = gy - 1; ay <= gy + 1 && !host; ay++) {
          var cell = grid[key(ax, ay)];
          if (!cell) continue;
          for (var j = 0; j < cell.length; j++) {
            var o = cell[j];
            var dx = o.x - m.x, dy = o.y - m.y;
            if (dx * dx + dy * dy < sep2) { host = o; break; }
          }
        }
      }

      if (host) {
        m.want = false;
        hidden++;
        if (!host._hidden) { host._hidden = 0; }
        host._hidden++;
        if (host._hidden === 1) clusters.push(host);
      } else {
        m._hidden = 0;
        placed.push(m);
        (grid[key(gx, gy)] || (grid[key(gx, gy)] = [])).push(m);
      }
    }

    hidden += this._placeBadges(clusters, placed);

    this.stats.clustered = hidden;
    return clusters;
  };

  Globe.prototype._placeBadges = function (clusters, placed) {
    var BR = 9;
    var REACH = 40;
    var extra = 0;

    for (var c = 0; c < clusters.length; c++) {
      var host = clusters[c];
      var dist = host.r + BR + 2;

      var near = [];
      for (var p = 0; p < placed.length; p++) {
        var o = placed[p];
        if (o === host) continue;
        var ox = o.x - host.x, oy = o.y - host.y;
        if (ox * ox + oy * oy < REACH * REACH) near.push(o);
      }

      var best = null, bestClear = -Infinity;
      for (var a = 0; a < 8; a++) {

        var ang = (-Math.PI / 4) + a * (Math.PI / 4);
        var bx = host.x + Math.cos(ang) * dist,
            by = host.y + Math.sin(ang) * dist;

        var clear = Infinity, k, d;
        for (k = 0; k < near.length; k++) {
          d = Math.sqrt((near[k].x - bx) * (near[k].x - bx) +
                        (near[k].y - by) * (near[k].y - by)) - near[k].r - BR;

          if (near[k]._hidden > 0) d -= 6;
          if (d < clear) clear = d;
        }
        for (k = 0; k < c; k++) {
          d = Math.sqrt((clusters[k]._bx - bx) * (clusters[k]._bx - bx) +
                        (clusters[k]._by - by) * (clusters[k]._by - by)) - 2 * BR;
          if (d < clear) clear = d;
        }

        if (clear > bestClear) { bestClear = clear; best = [bx, by]; }
        if (clear >= 0) break;
      }

      host._bx = best[0];
      host._by = best[1];

      if (bestClear < 0) {
        for (var q = 0; q < near.length; q++) {
          var t = near[q];
          if (!t.want || t._hidden > 0) continue;
          var tx = t.x - host._bx, ty = t.y - host._by;
          if (tx * tx + ty * ty < (BR + t.r) * (BR + t.r)) {
            t.want = false;
            extra++;
            host._hidden++;
          }
        }
      }
    }
    return extra;
  };

  Globe.prototype._drawClusters = function (clusters) {
    var self = this, pool = this._clusterPool;

    while (pool.length < clusters.length) {
      var g = el('g', { class: 'cl', role: 'button', tabindex: '0' });
      var bg = el('circle', { class: 'clbg', r: 8 });
      var tx = el('text', { class: 'cltx', y: 3.2, 'text-anchor': 'middle' });
      g.appendChild(bg);
      g.appendChild(tx);
      (function (node) {
        function act() {
          if (self._dragged) return;

          if (node._at) self.zoomTo(node._at[0], node._at[1]);
        }
        node.addEventListener('click', act);
        node.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(); }
        });
      })(g);
      pool.push({ g: g, tx: tx, on: false });
      this.clusters.appendChild(g);
    }

    for (var i = 0; i < pool.length; i++) {
      var slot = pool[i];
      if (i < clusters.length) {
        var host = clusters[i];
        var n = host._hidden;
        var label = n + ' more ' + (n === 1 ? 'place' : 'places') +
                    ' here. Zoom in to separate them.';

        slot.g.setAttribute('transform',
          'translate(' + host._bx.toFixed(1) + ',' + host._by.toFixed(1) + ')');
        slot.tx.textContent = n > 99 ? '99+' : '+' + n;
        slot.g.setAttribute('aria-label', label);
        slot.g.setAttribute('data-tip', label);
        slot.g._at = [host.d.lon, host.d.lat];
        if (!slot.on) { slot.g.removeAttribute('display'); slot.on = true; }
      } else if (slot.on) {
        slot.g.setAttribute('display', 'none');
        slot.on = false;
      }
    }
  };

  Globe.prototype.zoomTo = function (lon, lat) {
    this.setZoom(this.zoom * 2.2);
    this._spin([-lon, -lat]);
  };

  Globe.prototype.focus = function (slug, animate) {
    var target = null;
    this.nodes.forEach(function (m) {
      if (m.d.slug === slug) target = m.d;
    });
    if (!target) return;
    this.setSelected(slug);
    this._spin([-target.lon, -target.lat], animate);
  };

  Globe.prototype._spin = function (to, animate) {
    var self = this;
    if (animate === false || reducedMotion()) {
      this.rotation = to;
      this.invalidate();
      return;
    }
    var from = this.rotation.slice();

    var dLon = ((to[0] - from[0] + 540) % 360) - 180;
    var dLat = to[1] - from[1];
    var t0 = performance.now(), dur = 620;

    if (this._raf) cancelAnimationFrame(this._raf);
    (function step(t) {
      var k = Math.min(1, (t - t0) / dur);
      var e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      self.rotation = [from[0] + dLon * e, from[1] + dLat * e];
      self.render();
      if (k < 1) self._raf = requestAnimationFrame(step);
    })(t0);
  };

  Globe.prototype.setSelected = function (slug) {
    this.selected = slug;
    var self = this;
    this.nodes.forEach(function (m) {
      var on = m.d.slug === slug;
      m.g.classList.toggle('sel', on);
      m.g.setAttribute('aria-pressed', on ? 'true' : 'false');
      if (on) self._raise(m.g);
    });

    this.invalidate();
  };

  global.Globe = Globe;
})(window);
