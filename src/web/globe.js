/* Orthographic globe: the report's primary navigation control.
 *
 * Uses d3-geo (vendored) for projection, clipping and path generation. That is
 * worth a dependency: the projection itself is easy, but clipping polygons
 * against the horizon - the antimeridian, rings that wrap the limb, features
 * that straddle both - is exactly the kind of geometry that a hand-rolled
 * version gets subtly and invisibly wrong.
 *
 * What this file deliberately does NOT do is compute anything. Marker colour is
 * a CSS class chosen from a skill value Python already calculated; radius is a
 * function of a sample size Python already counted. No statistic is derived
 * here.
 */
(function (global) {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var uid = 0;

  function el(name, attrs) {
    var n = document.createElementNS(SVG_NS, name);
    for (var k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    return n;
  }

  function reducedMotion() {
    return global.matchMedia &&
      global.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  /* Skill -> tier class. Cutoffs match the qualitative bands the glossary
   * publishes as rules of thumb, so the map and the scorecards tell the same
   * story. */
  function tier(bss) {
    if (bss < 0) return 'q0';
    if (bss < 0.2) return 'q1';
    if (bss < 0.35) return 'q2';
    if (bss < 0.5) return 'q3';
    return 'q4';
  }

  function Globe(node, opts) {
    this.node = node;
    this.cities = opts.cities || [];
    this.excluded = opts.excluded || [];
    this.land = opts.land;
    this.onSelect = opts.onSelect || function () {};
    this.size = 420;
    this.selected = null;
    this._raf = null;
    this._dragged = false;
    this._id = 'globe-clip-' + (++uid);

    this.baseScale = this.size / 2 - 6;
    this.projection = global.d3.geoOrthographic()
      .translate([this.size / 2, this.size / 2])
      .clipAngle(90);
    this.path = global.d3.geoPath(this.projection);

    this.maxN = this.cities.reduce(function (m, c) {
      return Math.max(m, c.n || 0);
    }, 1);

    // Open on the centroid of the covered capitals rather than a fixed
    // longitude, so the map still makes sense if the city set ever changes.
    var c = this._centroid();
    this.rotation = [-c[0], -c[1]];
    // ...and zoomed to fit them. Fifteen European capitals on a whole-Earth
    // globe land within a few pixels of one another - Amsterdam and Luxembourg
    // overlap outright - so an unzoomed default is not merely cramped, it makes
    // several markers impossible to click.
    this.zoom = this._fitZoom(c);
    this.homeZoom = this.zoom;

    this._build();
    this._bindDrag();
    this._bindZoom();
    this.render();
  }

  Globe.prototype.ZOOM_MIN = 1;
  Globe.prototype.ZOOM_MAX = 8;

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

  /* Zoom that brings the whole city set comfortably inside the viewport.
   *
   * In an orthographic projection a point an angle theta from the centre lands
   * at radius scale*sin(theta), so this fit is exact rather than tuned by eye.
   * Clamped, because a single city would otherwise imply infinite magnification.
   */
  Globe.prototype._fitZoom = function (centre) {
    if (this.cities.length < 2) return 1;
    var geoDistance = global.d3.geoDistance, max = 0;
    this.cities.forEach(function (c) {
      max = Math.max(max, geoDistance(centre, [c.lon, c.lat]));
    });
    if (max < 1e-6) return 1;
    // Fill 62% of the radius: enough margin that edge markers are not clipped,
    // and that the sphere still reads as a sphere rather than a flat disc.
    return Math.max(1, Math.min(4, 0.62 / Math.sin(Math.min(max, Math.PI / 2))));
  };

  Globe.prototype.setZoom = function (z, about) {
    var next = Math.max(this.ZOOM_MIN, Math.min(this.ZOOM_MAX, z));
    if (next === this.zoom) return;
    this.zoom = next;
    this.render();
    if (this.zoomOut) {
      this.zoomOut.disabled = this.zoom <= this.ZOOM_MIN + 1e-6;
      this.zoomIn.disabled = this.zoom >= this.ZOOM_MAX - 1e-6;
    }
  };

  Globe.prototype._build = function () {
    var s = this.size;
    var svg = el('svg', {
      viewBox: '0 0 ' + s + ' ' + s,
      role: 'group',
      'aria-label': 'Interactive globe. Select a capital to load its calibration report.'
    });

    // Zooming past 1x makes the sphere larger than the viewport, so it has to
    // be clipped back to a disc; without this the land spills into the page as
    // a rectangle and stops reading as a globe at all.
    var defs = el('defs');
    var clip = el('clipPath', { id: this._id });
    clip.appendChild(el('circle', { cx: s / 2, cy: s / 2, r: s / 2 - 6 }));
    defs.appendChild(clip);
    svg.appendChild(defs);

    var g = el('g', { 'clip-path': 'url(#' + this._id + ')' });
    this.sphere = el('path', { class: 'sphere' });
    this.grat = el('path', { class: 'graticule' });
    this.landPath = el('path', { class: 'land' });
    this.leaders = el('path', { class: 'leaders' });
    this.markers = el('g', { class: 'markers' });

    g.appendChild(this.sphere);
    g.appendChild(this.landPath);
    g.appendChild(this.grat);
    g.appendChild(this.leaders);
    g.appendChild(this.markers);
    svg.appendChild(g);

    // The limb, drawn outside the clip so the edge stays crisp at any zoom.
    svg.appendChild(el('circle', {
      class: 'limb', cx: s / 2, cy: s / 2, r: s / 2 - 6
    }));

    this.node.appendChild(svg);
    this.svg = svg;

    this.graticule = global.d3.geoGraticule10();
    this._buildMarkers();
    this._buildControls();
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
      var c = self._centroid();
      self.rotation = [-c[0], -c[1]];
      self.zoom = self.homeZoom;
      self.render();
    });

    this.node.appendChild(wrap);
    this.zoomOut.disabled = this.zoom <= this.ZOOM_MIN + 1e-6;
  };

  Globe.prototype._buildMarkers = function () {
    var self = this;
    this.nodes = [];

    function make(d, isExcluded) {
      var g = el('g', {
        class: 'mk' + (isExcluded ? ' excluded' : ' ' + tier(d.bss)),
        tabindex: isExcluded ? null : '0',
        role: isExcluded ? 'img' : 'button'
      });
      // Radius encodes record length, but the range is deliberately narrow:
      // these capitals sit close together, and a wide range would trade a
      // secondary variable for the ability to hit the marker at all.
      var r = isExcluded ? 3.5 : 4 + 3.5 * Math.sqrt((d.n || 0) / self.maxN);
      g.appendChild(el('circle', { class: 'halo', r: r + 2.5 }));
      g.appendChild(el('circle', { class: 'dot', r: r }));

      var tip = isExcluded
        ? d.name + '\nExcluded: ' + d.why
        : d.name + ' (' + d.country + ')\nSkill score ' + d.bss.toFixed(2) +
          '\n' + d.n + ' days, rain on ' + Math.round(d.base_rate * 100) + '%' +
          '\nRank ' + d.rank_lo.toFixed(0) + '-' + d.rank_hi.toFixed(0) +
          ' of ' + d.n_cities;
      g.setAttribute('data-tip', tip);
      g.setAttribute('aria-label', tip.replace(/\n/g, '. '));

      if (!isExcluded) {
        g.addEventListener('click', function () {
          // A drag that ends over a marker still fires a click. Without this
          // guard, turning the globe by grabbing a continent navigates away.
          if (self._dragged) return;
          self.onSelect(d.slug);
        });
        g.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            self.onSelect(d.slug);
          }
        });
        // No raise-on-hover here, deliberately. Reordering the node between
        // pointerup and click makes Chromium dispatch the click on the parent
        // instead of the marker, so the globe silently stops navigating. The
        // separation pass in render() already guarantees markers do not
        // overlap, which is what raising was for.
      }
      self.nodes.push({ d: d, g: g, r: r, excluded: isExcluded });
      self.markers.appendChild(g);
      return g;
    }

    // Excluded first so included markers paint on top of them.
    this.excluded.forEach(function (d) { make(d, true); });
    this.cities.forEach(function (d) { make(d, false); });
  };

  /* SVG has no z-index: paint order is document order, so raising a marker
   * means moving it. */
  Globe.prototype._raise = function (g) {
    if (g.parentNode && g.parentNode.lastChild !== g) g.parentNode.appendChild(g);
  };

  /* -- interaction ------------------------------------------------------- */
  Globe.prototype._bindDrag = function () {
    var self = this, dragging = false, last = null, moved = 0;

    // Drag state is tracked through window listeners rather than
    // setPointerCapture. Capturing the pointer on the container retargets the
    // subsequent mouseup - and therefore the click - to the container itself,
    // so every marker silently stops responding while still looking and
    // hovering exactly as though it worked.
    this.node.addEventListener('pointerdown', function (e) {
      if (e.target.closest && e.target.closest('.globe-ctl')) return;
      dragging = true; moved = 0; self._dragged = false;
      last = [e.clientX, e.clientY];
    });

    global.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var dx = e.clientX - last[0], dy = e.clientY - last[1];
      moved += Math.abs(dx) + Math.abs(dy);
      last = [e.clientX, e.clientY];
      // Scale rotation by the projection scale so the drag feels 1:1 with the
      // surface under the cursor at any zoom level.
      var k = 90 / self.projection.scale();
      self.rotation = [
        self.rotation[0] + dx * k,
        // Clamped: letting latitude pass the pole flips the globe inside out,
        // which is disorienting and serves no purpose.
        Math.max(-90, Math.min(90, self.rotation[1] - dy * k))
      ];
      self.render();
    });

    function end() {
      if (!dragging) return;
      dragging = false;
      self._dragged = moved > 6;
    }
    global.addEventListener('pointerup', end);
    global.addEventListener('pointercancel', end);

    // Keyboard rotation and zoom, so the globe is not mouse-only.
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
      if (used) { e.preventDefault(); self.render(); }
    });
  };

  Globe.prototype._bindZoom = function () {
    var self = this;
    this.node.addEventListener('wheel', function (e) {
      // Only capture the wheel once the globe has focus or the pointer is over
      // it AND the gesture is deliberate; hijacking page scroll is a well-earned
      // grievance against embedded maps.
      if (!e.ctrlKey && Math.abs(e.deltaY) < 4) return;
      e.preventDefault();
      self.setZoom(self.zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
    }, { passive: false });
  };

  /* -- rendering --------------------------------------------------------- */
  Globe.prototype.render = function () {
    this.projection.rotate(this.rotation).scale(this.baseScale * this.zoom);

    this.sphere.setAttribute('d', this.path({ type: 'Sphere' }) || '');
    this.grat.setAttribute('d', this.path(this.graticule) || '');
    if (this.land) this.landPath.setAttribute('d', this.path(this.land) || '');

    var proj = this.projection;
    var centre = [-this.rotation[0], -this.rotation[1]];
    var geoDistance = global.d3.geoDistance;
    var live = [];

    this.nodes.forEach(function (m) {
      var p = proj([m.d.lon, m.d.lat]);
      // A point is on the far side when it is more than a quarter turn from the
      // projection centre. Without this test the back hemisphere folds onto the
      // front and cities appear mirrored on the wrong continent.
      var behind = geoDistance(centre, [m.d.lon, m.d.lat]) > Math.PI / 2;
      if (!p || behind) {
        m.g.setAttribute('display', 'none');
        m.g.setAttribute('aria-hidden', 'true');
      } else {
        m.g.removeAttribute('display');
        m.g.removeAttribute('aria-hidden');
        m.true_ = p;
        m.x = p[0];
        m.y = p[1];
        live.push(m);
      }
    });

    this._separate(live);

    var d = '';
    live.forEach(function (m) {
      m.g.setAttribute('transform',
        'translate(' + m.x.toFixed(1) + ',' + m.y.toFixed(1) + ')');
      var dx = m.x - m.true_[0], dy = m.y - m.true_[1];
      if (dx * dx + dy * dy > 1) {
        d += 'M' + m.true_[0].toFixed(1) + ',' + m.true_[1].toFixed(1) +
             'L' + m.x.toFixed(1) + ',' + m.y.toFixed(1);
      }
    });
    this.leaders.setAttribute('d', d);
  };

  /* Push overlapping markers apart, and draw a leader back to the true spot.
   *
   * Some of these capitals are genuinely close together - Helsinki and Tallinn
   * are 80 km apart, which is under two pixels at any zoom that still shows
   * Europe. Left alone, one sits entirely underneath the other and cannot be
   * clicked or focused at all.
   *
   * Displacement is the standard cartographic answer, and the leader line is
   * what keeps it honest: the dot you click is not quite where the city is, and
   * the line says so. The alternative - silently moving a marker - would be a
   * map that quietly lies about position, which is not a trade this report gets
   * to make.
   */
  Globe.prototype._separate = function (live) {
    if (live.length < 2) return;
    var PAD = 2.5, PASSES = 12;

    for (var pass = 0; pass < PASSES; pass++) {
      var moved = false;
      for (var i = 0; i < live.length; i++) {
        for (var j = i + 1; j < live.length; j++) {
          var a = live[i], b = live[j];
          var dx = b.x - a.x, dy = b.y - a.y;
          var min = a.r + b.r + PAD;
          var dist = Math.sqrt(dx * dx + dy * dy);
          if (dist >= min) continue;
          if (dist < 1e-6) {
            // Exactly coincident: nudge along a fixed angle derived from the
            // pair's order so the layout stays deterministic between renders.
            dx = Math.cos(i + j); dy = Math.sin(i + j); dist = 1;
          }
          var push = (min - dist) / 2;
          var ux = dx / dist * push, uy = dy / dist * push;
          a.x -= ux; a.y -= uy;
          b.x += ux; b.y += uy;
          moved = true;
        }
      }
      if (!moved) break;
    }
  };

  /* Turn the globe so a city faces the viewer. */
  Globe.prototype.focus = function (slug, animate) {
    var target = null;
    this.nodes.forEach(function (m) {
      if (!m.excluded && m.d.slug === slug) target = m.d;
    });
    if (!target) return;

    this.setSelected(slug);
    var to = [-target.lon, -target.lat];
    if (animate === false || reducedMotion()) {
      this.rotation = to;
      this.render();
      return;
    }

    var from = this.rotation.slice(), self = this;
    // Take the short way round; without this a move across the antimeridian
    // spins most of the way about the planet to arrive next door.
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
      if (m.excluded) return;
      var on = m.d.slug === slug;
      m.g.classList.toggle('sel', on);
      m.g.setAttribute('aria-pressed', on ? 'true' : 'false');
      if (on) self._raise(m.g);
    });
  };

  Globe.prototype.resize = function () { this.render(); };

  global.Globe = Globe;
})(window);
