(function () {
  'use strict';

  var cfg = JSON.parse(document.getElementById('site-config').textContent);

  cfg.cities = cfg.inline ? [{
    slug: cfg.inline.slug, name: cfg.inline.name, country: cfg.inline.country,
    lat: cfg.inline.lat, lon: cfg.inline.lon, bss: cfg.inline.bss,
    n: cfg.inline.n, base_rate: cfg.inline.base_rate,
    rank_lo: cfg.inline.rank_lo, rank_hi: cfg.inline.rank_hi
  }] : [];
  cfg.cityUrls = {};

  var ready = cfg.cityIndexUrl
    ? fetch(cfg.cityIndexUrl).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      }).then(function (ix) {
        cfg.cities = ix.rows.map(function (row) {
          var c = {};
          ix.cols.forEach(function (k, i) { c[k] = row[i]; });
          return c;
        });
        Object.keys(ix.hashes).forEach(function (slug) {
          cfg.cityUrls[slug] = cfg.base + 'data/cities/' + slug + '.' +
                               ix.hashes[slug] + '.json';
        });
        return cfg;
      }).catch(function () {

        return cfg;
      })
    : Promise.resolve(cfg);

  var root = document.documentElement;
  var cache = {};

  var current = (cfg.inline && cfg.inline.slug) || cfg.defaultSlug;
  var globe = null;

  if (cfg.inline) cache[cfg.inline.slug] = cfg.inline;

  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };

  var THEMES = ['observatory', 'daylight', 'blueprint'];
  var store = {
    get: function (k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  };

  function applyTheme(name) {
    if (THEMES.indexOf(name) < 0) name = THEMES[0];
    root.setAttribute('data-theme', name);
    store.set('wc-theme', name);
    $$('[data-theme-set]').forEach(function (b) {
      b.setAttribute('aria-pressed', b.dataset.themeSet === name ? 'true' : 'false');
    });
  }

  var lastSky = null;

  function applySky(cls) {
    lastSky = cls || null;

    if (lastSky) root.setAttribute('data-sky', lastSky);
    else root.removeAttribute('data-sky');
  }

  function initThemeControls() {
    applyTheme(store.get('wc-theme', 'observatory'));

    var narrow = window.matchMedia('(max-width: 700px)');
    $$('[data-theme-set]').forEach(function (b) {
      b.addEventListener('click', function () {
        if (narrow.matches) {
          var i = THEMES.indexOf(root.getAttribute('data-theme'));
          applyTheme(THEMES[(i + 1) % THEMES.length]);
        } else {
          applyTheme(b.dataset.themeSet);
        }
      });
    });
  }

  function initTooltip() {
    var tip = $('#tip');
    if (!tip) return;

    document.addEventListener('mousemove', function (e) {
      var el = e.target.closest && e.target.closest('[data-tip]');
      if (!el) { tip.style.opacity = 0; return; }
      tip.innerHTML = el.dataset.tip.replace(/\n/g, '<br>');
      tip.style.opacity = 1;
      tip.style.left = Math.min(e.clientX + 14, innerWidth - 250) + 'px';
      tip.style.top = Math.min(e.clientY + 16, innerHeight - 90) + 'px';
    });
    document.addEventListener('mouseleave', function () { tip.style.opacity = 0; });
    document.addEventListener('scroll', function () { tip.style.opacity = 0; }, true);
  }

  function payload(slug) {
    if (cache[slug]) return Promise.resolve(cache[slug]);
    return ready.then(function () {
      var url = cfg.cityUrls[slug];
      if (!url) throw new Error('unknown city ' + slug);
      return fetch(url);
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (p) { cache[slug] = p; return p; });
  }

  function renderCity(p, push) {
    current = p.slug;

    var card = $('#city-card');
    if (card && p.html.card) card.innerHTML = p.html.card;

    $('#city-answer').innerHTML = p.html.answer;
    $('#city-curve').innerHTML = p.html.curve;
    $('#city-season').innerHTML = p.html.season;

    var btn = $('#citybtn .name');
    if (btn) btn.textContent = p.name;
    var cc = $('#citybtn .cc');
    if (cc) cc.textContent = p.country;

    var depth = $('#depth');
    if (depth) {
      depth.innerHTML = '<span>Available for ' + esc(p.name) + ':</span>' +
        p.depth.map(function (d) {
          return '<span class="tag good">' + esc(d) + '</span>';
        }).join('') +
        (p.deep ? '' : '<span class="muted">Hourly, event and benchmark tracks ' +
          'are Bucharest-only \u2014 they depend on station records that do not ' +
          'exist for other capitals.</span>');
    }

    document.title = 'How good is the ' + p.name + ' weather forecast?';
    var h1 = $('#h1-city');
    if (h1) h1.textContent = p.name;
    var ds = $('#deep-sel');
    if (ds) {

      ds.innerHTML = p.name === 'Bucharest' ? '' :
        'You currently have <b>' + esc(p.name) + '</b> selected; its own ' +
        'results are above.';
    }
    $$('[data-deep-only]').forEach(function (n) { n.hidden = !p.deep; });

    if (globe) globe.focus(p.slug, true);
    highlightCharts(p.slug, p.name);
    applyCompare();
    if (push) history.pushState({ slug: p.slug }, '', cityUrl(p.slug));
    loadForecast(p);
  }

  function overlayFor(svg, chart) {
    if (!chart) return null;
    var kind = svg.getAttribute('data-chart');
    if (kind === 'reliability' && chart.rel) {
      return { cls: 'cc-line', body: '<polyline points="' + chart.rel +
               '" fill="none"/>', end: lastPoint(chart.rel) };
    }
    if (kind === 'baserate' && chart.base) {
      return { cls: 'cc-dot', body: '<circle cx="' + chart.base[0] +
               '" cy="' + chart.base[1] + '" r="5"/>', end: chart.base };
    }
    if (kind === 'fingerprint' && chart.fp) {
      return { cls: 'cc-dot', body: '<circle cx="' + chart.fp[0] +
               '" cy="' + chart.fp[1] + '" r="5"/>', end: chart.fp };
    }
    if (kind === 'lead' && chart.lead) {
      return { cls: 'cc-line', body: '<polyline points="' + chart.lead +
               '" fill="none"/>', end: lastPoint(chart.lead) };
    }
    return null;
  }

  function lastPoint(pts) {
    var last = pts.split(' ').pop().split(',');
    return [parseFloat(last[0]), parseFloat(last[1])];
  }

  function endLabel(svg, end, name) {
    var vb = (svg.getAttribute('viewBox') || '0 0 860 470').split(' ');
    var w = parseFloat(vb[2]);
    var t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    var flip = end[0] > w - 90;
    t.setAttribute('class', 'cc-endlabel');
    t.setAttribute('x', end[0] + (flip ? -8 : 8));
    t.setAttribute('y', end[1] - 8);
    if (flip) t.setAttribute('text-anchor', 'end');
    t.textContent = name;
    return t;
  }

  function inject(svg, slug, name, chart, extraCls) {
    var top = svg.querySelector('.cc-top');
    var o = overlayFor(svg, chart);
    if (!top || !o) return;
    var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('class', 'cc ' + o.cls + ' ' + extraCls);
    g.setAttribute('data-city', slug);
    if (name) g.setAttribute('data-tip', name);
    g.innerHTML = o.body;
    if (name && o.end) g.appendChild(endLabel(svg, o.end, name));
    top.appendChild(g);
  }

  function highlightCharts(slug, name) {
    var chart = cache[slug] && cache[slug].chart;
    $$('.citychart').forEach(function (svg) {
      var prev = svg.querySelector('.cc.on');
      if (prev) {
        prev.classList.remove('on');
        var lbl = prev.querySelector('.cc-endlabel');
        if (lbl) lbl.parentNode.removeChild(lbl);
      }
      var old = svg.querySelector('.cc-injected');
      if (old) old.parentNode.removeChild(old);

      svg.querySelectorAll('.chart-point-label[data-for]').forEach(function (t) {
        t.style.display = t.getAttribute('data-for') === slug ? 'none' : '';
      });
      var g = svg.querySelector('.cc[data-city="' + slug + '"]:not(.cc-cmp)');
      if (g) {
        g.classList.add('on');
        var top = svg.querySelector('.cc-top');
        if (top) top.appendChild(g);
        var c = g.querySelector('circle');
        if (name && c) {
          g.appendChild(endLabel(svg, [parseFloat(c.getAttribute('cx')),
                                       parseFloat(c.getAttribute('cy'))], name));
        }
      } else {
        inject(svg, slug, name, chart, 'cc-injected on');
      }
    });
    $$('.chartkey span:not(.cmp-key) b').forEach(function (b) {
      b.textContent = name || 'your selection';
    });

    $$('.chart-note').forEach(function (n) {
      var txt = chart && chart.note && chart.note[n.dataset.note];
      n.hidden = !txt;
      n.textContent = txt ? name + ': ' + txt : '';
    });
  }

  var compare = null;

  function setCompare(slug) {
    compare = (!slug || slug === compare) ? null : slug;
    if (compare && !cache[compare]) {
      payload(compare).then(applyCompare).catch(function () { compare = null; });
    } else {
      applyCompare();
    }
  }

  function applyCompare() {
    var name = compare ? cityName(compare) : '';
    $$('.citychart').forEach(function (svg) {
      var old = svg.querySelector('.cc-cmp');
      if (old) old.parentNode.removeChild(old);
      if (!compare) return;
      inject(svg, compare, name + ' (comparison)',
             cache[compare] && cache[compare].chart, 'cc-cmp');
    });
    $$('.cmp-key').forEach(function (k) {
      k.hidden = !compare;
      var n = k.querySelector('.cmp-name');
      if (n) n.textContent = name;
      k.title = compare ? 'Click to remove the comparison' : '';
    });
  }

  function cityName(slug) {
    var c = cfg.cities.filter(function (x) { return x.slug === slug; })[0];
    return c ? c.name : '';
  }

  function initLazyCharts() {
    var boxes = $$('.lazychart');
    if (!boxes.length) return;

    function load(box) {
      if (box.dataset.state) return;
      box.dataset.state = 'loading';
      fetch(box.dataset.chartSrc).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      }).then(function (svg) {
        box.innerHTML = svg;
        box.dataset.state = 'ready';

        highlightCharts(current, cityName(current));
        applyCompare();
      }).catch(function () {

        box.dataset.state = '';
      });
    }

    if (!window.IntersectionObserver) { boxes.forEach(load); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        io.unobserve(e.target);
        load(e.target);
      });
    }, { rootMargin: '800px 0px' });
    boxes.forEach(function (b) { io.observe(b); });
  }

  function initLazyFolds() {
    $$('details[data-fold-src]').forEach(function (d) {
      d.addEventListener('toggle', function () {
        if (!d.open || d.dataset.state) return;
        d.dataset.state = 'loading';
        fetch(d.dataset.foldSrc).then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.text();
        }).then(function (html) {
          d.insertAdjacentHTML('beforeend', html);
          d.dataset.state = 'ready';
        }).catch(function () {

          d.dataset.state = '';
        });
      });
    });
  }

  function cityUrl(slug) {
    return slug === cfg.defaultSlug ? cfg.base : cfg.base + 'city/' + slug + '/';
  }

  function slugFromLocation() {
    var base = cfg.base || '/';
    var path = location.pathname;
    if (path.indexOf(base) === 0) path = path.slice(base.length);
    var m = /^city\/([^/]+)\/?$/.exec(path);
    return m ? m[1] : cfg.defaultSlug;
  }

  function go(slug, push) {
    if (slug === current && cache[slug]) return;
    payload(slug).then(function (p) { renderCity(p, push !== false); })
      .catch(function () {

        location.href = cityUrl(slug);
      });
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  var WMO = [
    [[0], 'Clear sky', 'clear', 'clear'],
    [[1, 2], 'Mainly clear', 'clear', 'clear'],
    [[3], 'Overcast', 'cloud', 'cloud'],
    [[45, 48], 'Fog', 'fog', 'fog'],
    [[51, 53, 55, 56, 57], 'Drizzle', 'rain', 'rain'],
    [[61, 63, 65, 66, 67, 80, 81, 82], 'Rain', 'rain', 'rain'],
    [[71, 73, 75, 77, 85, 86], 'Snow', 'snow', 'snow'],
    [[95, 96, 99], 'Thunderstorm', 'storm', 'storm']
  ];

  function decode(code, isDay) {
    for (var i = 0; i < WMO.length; i++) {
      if (WMO[i][0].indexOf(code) >= 0) {
        var sky = WMO[i][3], sc = WMO[i][2];
        if (!isDay && (sky === 'clear' || sky === 'cloud')) {

          if (sc === 'clear') sc = 'night';
          sky = 'night';
        }
        return { label: WMO[i][1], scene: sc, sky: sky };
      }
    }
    return { label: 'Unknown', scene: 'cloud', sky: null };
  }

  var CLOUD = 'M32 68A15 15 0 0 1 36 42A21 21 0 0 1 69 45A13 13 0 0 1 76 68Z';
  var CLOUD_BOX = [32, 42, 76, 68];
  var MOON = 'M63 22A26 26 0 1 0 63 74A32 32 0 1 1 63 22Z';
  var MOON_BOX = [37, 22, 63, 74];
  var BOLT = 'M56 58L40 82H51L46 96L68 70H55Z';
  var BOLT_BOX = [40, 58, 68, 96];
  var W = 900, H = 400;

  function rnd(i) { return ((i * 9301 + 49297) % 233280) / 233280; }

  function place(cx, cy, h, box) {
    var s = h / (box[3] - box[1]);
    return 'translate(' + (cx - (box[0] + box[2]) / 2 * s).toFixed(1) + ' ' +
      (cy - (box[1] + box[3]) / 2 * s).toFixed(1) + ') scale(' + s.toFixed(3) + ')';
  }

  function cloudAt(cx, cy, h, o, dur, amp) {
    return '<g class="a-drift" style="--amp:' + amp + 'px;animation-duration:' +
      dur + 's;animation-delay:-' + (dur / 2).toFixed(1) + 's">' +
      '<g class="a-cloud" opacity="' + o + '">' +
      '<path class="lump" d="' + CLOUD + '" transform="' +
        place(cx - h * 0.62, cy + h * 0.20, h * 0.60, CLOUD_BOX) + '"/>' +
      '<path class="lump" d="' + CLOUD + '" transform="' +
        place(cx + h * 0.70, cy + h * 0.24, h * 0.48, CLOUD_BOX) + '"/>' +
      '<path class="main" d="' + CLOUD + '" transform="' +
        place(cx, cy, h, CLOUD_BOX) + '"/>' +
      '</g></g>';
  }

  function sunAt(x, y, r) {
    var rays = '', i, a;
    for (i = 0; i < 12; i++) {
      a = i * Math.PI / 6;
      rays += '<line x1="' + (x + r * 1.35 * Math.cos(a)).toFixed(1) +
        '" y1="' + (y + r * 1.35 * Math.sin(a)).toFixed(1) +
        '" x2="' + (x + r * 1.72 * Math.cos(a)).toFixed(1) +
        '" y2="' + (y + r * 1.72 * Math.sin(a)).toFixed(1) + '"/>';
    }
    return '<circle class="a-glow" cx="' + x + '" cy="' + y + '" r="' + r * 4.5 + '"/>' +
      '<g class="a-rays" style="transform-origin:' + x + 'px ' + y + 'px;' +
      'stroke-width:' + (r * 0.11).toFixed(1) + '">' + rays + '</g>' +
      '<circle class="a-sun" cx="' + x + '" cy="' + y + '" r="' + r + '"/>';
  }

  function fall(n, flakes, seed) {
    var out = '', i, x, dur;
    for (i = 0; i < n; i++) {
      x = (10 + rnd(i + seed) * (W - 20)).toFixed(1);
      dur = 0.75 + rnd(i + seed + 11) * 0.5;
      out += flakes
        ? '<circle class="a-flake" cx="' + x + '" cy="-10" r="' +
          (1.6 + rnd(i + seed + 5) * 1.8).toFixed(1) + '" style="animation-duration:' +
          (dur * 5).toFixed(2) + 's;animation-delay:-' +
          (rnd(i + seed + 3) * 9).toFixed(2) + 's"/>'
        : '<line class="a-rain" x1="' + x + '" y1="-26" x2="' +
          (x - 4).toFixed(1) + '" y2="-8" style="animation-duration:' + dur.toFixed(2) +
          's;animation-delay:-' + (rnd(i + seed + 3) * 2).toFixed(2) + 's"/>';
    }
    return out;
  }

  function stars(n) {
    var out = '', i, dur;
    for (i = 0; i < n; i++) {
      dur = 2.6 + rnd(i + 13) * 3.8;
      out += '<circle class="a-star" cx="' + (20 + rnd(i + 2) * (W - 40)).toFixed(1) +
        '" cy="' + (14 + rnd(i + 19) * 250).toFixed(1) + '" r="' +
        (1.4 + rnd(i + 31) * 2.2).toFixed(1) + '" style="animation-duration:' +
        dur.toFixed(2) + 's;animation-delay:-' +
        (rnd(i + 7) * dur).toFixed(2) + 's"/>';
    }
    return out;
  }

  function bands(n) {
    var out = '', i, y;
    for (i = 0; i < n; i++) {
      y = 70 + i * 62;
      out += '<line class="a-band" x1="' + (-80 + rnd(i) * 120).toFixed(0) +
        '" y1="' + y + '" x2="' + (760 + rnd(i + 9) * 240).toFixed(0) + '" y2="' + y +
        '" style="animation-duration:' + (16 + i * 5) + 's;animation-delay:-' +
        i * 3 + 's"/>';
    }
    return out;
  }

  function art(kind) {
    var g;
    if (kind === 'clear') {
      g = cloudAt(250, 190, 96, .09, 110, 40) + sunAt(762, 96, 30) +
          cloudAt(540, 316, 70, .06, 150, 26);
    } else if (kind === 'night') {
      g = stars(24) + '<path class="a-moon" d="' + MOON + '" transform="' +
          place(778, 92, 64, MOON_BOX) + '"/>' +
          cloudAt(320, 200, 96, .09, 130, 34);
    } else if (kind === 'cloud') {
      g = cloudAt(742, 126, 140, .17, 96, 46) + cloudAt(430, 92, 104, .12, 128, 32) +
          cloudAt(120, 210, 80, .08, 162, 22);
    } else if (kind === 'rain') {
      g = cloudAt(736, 112, 132, .17, 104, 42) + cloudAt(372, 84, 98, .11, 136, 30) +
          fall(46, false, 0);
    } else if (kind === 'snow') {
      g = cloudAt(736, 112, 132, .16, 104, 42) + cloudAt(372, 84, 98, .10, 136, 30) +
          fall(40, true, 40);
    } else if (kind === 'storm') {
      g = cloudAt(724, 104, 146, .23, 100, 38) + cloudAt(352, 84, 104, .15, 132, 28) +
          '<g transform="' + place(846, 182, 118, BOLT_BOX) +
          '"><path class="a-bolt" d="' + BOLT + '"/></g>' + fall(24, false, 80) +
          '<rect class="a-flash" x="0" y="0" width="' + W + '" height="' + H + '"/>';
    } else if (kind === 'fog') {
      g = cloudAt(742, 104, 124, .11, 116, 30) + bands(5);
    } else {
      g = cloudAt(738, 118, 130, .14, 108, 38) + cloudAt(388, 90, 96, .09, 140, 28);
    }
    return '<div class="fcsky" aria-hidden="true"><svg class="fcart" ' +
      'viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMaxYMin slice" ' +
      'focusable="false"><defs><radialGradient id="fcglow">' +
      '<stop offset="0" stop-color="#fff" stop-opacity=".42"/>' +
      '<stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs>' +
      g + '</svg></div><div class="fcscrim" aria-hidden="true"></div>';
  }

  function gapRail(stated, obs) {
    var lo = Math.min(stated, obs), hi = Math.max(stated, obs);

    var at = function (v) { return 'clamp(62px, ' + v + '%, calc(100% - 62px))'; };
    return '<div class="gap"><div class="gaprail">' +
      '<span class="end lo">0%</span><span class="end hi">100%</span>' +
      '<span class="span" data-lo="' + lo + '" data-hi="' + (hi - lo) +
      '" style="left:' + stated + '%;width:0"></span>' +
      '<span class="mk stated" style="left:' + stated + '%"></span>' +
      '<span class="mk obs" data-left="' + obs + '" style="left:' + stated + '%"></span>' +
      '<span class="lab stated" style="left:' + at(stated) + '">forecast <b>' +
      stated + '%</b></span>' +
      '<span class="lab obs" style="left:' + at(obs) + '">here it rained <b>' +
      obs + '%</b></span></div></div>';
  }

  function nextHours(j, n) {
    var h = j.hourly || {}, t = h.time || [], v = h.precipitation_probability || [];
    var now = (j.current && j.current.time) || '';
    var out = [], i;
    for (i = 0; i < t.length && out.length < n; i++) {
      if (t[i] < now || v[i] == null) continue;
      out.push([t[i], v[i]]);
    }
    return out;
  }

  function ribbon(hours) {

    if (hours.length < 6) return '';
    var bars = hours.map(function (h, i) {
      return '<span class="hr" style="--h:' + (h[1] / 100).toFixed(3) +
        ';animation-delay:' + (0.3 + i * 0.035).toFixed(2) + 's" data-tip="' +
        esc(hhmm(h[0]) || '') + ' \u00B7 ' + h[1] + '% chance of rain"></span>';
    }).join('');
    return '<div class="hrwrap">' +
      '<div class="hrs" role="img" aria-label="Forecast chance of rain for ' +
      'each of the next ' + hours.length + ' hours">' + bars + '</div>' +
      '<div class="hrslab"><span>' + esc(hhmm(hours[0][0]) || 'now') + '</span>' +
      '<span>next ' + hours.length + ' hours</span><span>' +
      esc(hhmm(hours[hours.length - 1][0]) || '') + '</span></div></div>';
  }

  function historicalMeaning(p, pop) {
    if (!p.pop_map || pop == null) return null;
    var f = pop / 100;
    for (var i = 0; i < p.pop_map.length; i++) {
      var b = p.pop_map[i];
      if (f >= b.lo && (f <= b.hi || i === p.pop_map.length - 1)) return b;
    }
    return null;
  }

  var reduced = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function countUp(node, to, suffix) {
    if (!node) return;
    if (reduced || to == null) {
      node.textContent = (to == null ? '\u2014' : to) + (to == null ? '' : suffix);
      return;
    }
    var t0 = null;
    requestAnimationFrame(function step(t) {
      if (t0 === null) t0 = t;
      var k = Math.min(1, (t - t0) / 750);
      var e = 1 - Math.pow(1 - k, 3);
      node.textContent = Math.round(to * e) + suffix;
      if (k < 1) requestAnimationFrame(step);
    });
  }

  function skeleton() {
    return '<div class="fcsky sk-sky" aria-hidden="true"></div>' +
      '<div class="fcscrim" aria-hidden="true"></div><div class="fcwrap">' +
      '<div class="fctop"><div class="fcnow">' +
      '<span class="sk" style="width:120px;height:9px"></span>' +
      '<span class="sk" style="width:92px;height:40px;margin:10px 0 8px"></span>' +
      '<span class="sk" style="width:150px;height:11px"></span></div>' +
      '<div class="fcsays"><span class="sk" style="width:104px;height:9px"></span>' +
      '<span class="sk" style="width:190px;height:16px;margin:8px 0 14px"></span>' +
      '<span class="sk" style="width:140px;height:9px"></span>' +
      '<span class="sk" style="width:120px;height:40px;margin-top:8px"></span></div></div>' +
      '<div class="hrs sk-hrs"></div>' +
      '<div class="pop"><span class="sk" style="width:100%;height:30px"></span>' +
      '<span class="sk" style="width:76%;height:11px;margin-top:14px"></span></div></div>';
  }

  function hhmm(iso) {
    var m = /T(\d{2}:\d{2})/.exec(String(iso || ''));
    return m ? m[1] : null;
  }

  function loadForecast(p) {
    var box = $('#fc');
    if (!box || p.lat == null) return;
    box.hidden = false;
    box.classList.remove('nopop');
    box.classList.add('loading');
    box.setAttribute('aria-busy', 'true');
    box.innerHTML = skeleton();

    var url = cfg.forecastApi +
      '?latitude=' + p.lat + '&longitude=' + p.lon +
      '&current=temperature_2m,weather_code,is_day' +
      '&hourly=precipitation_probability' +
      '&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max' +
      '&forecast_days=2&timezone=auto';

    var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = setTimeout(function () { ctrl && ctrl.abort(); }, 8000);

    fetch(url, ctrl ? { signal: ctrl.signal } : undefined)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (j) {
        clearTimeout(timer);
        var cur = j.current || {}, day = j.daily || {};
        var w = decode(cur.weather_code, cur.is_day !== 0);
        var pop = day.precipitation_probability_max
          ? day.precipitation_probability_max[0] : null;
        var hi = day.temperature_2m_max ? day.temperature_2m_max[0] : null;
        var lo = day.temperature_2m_min ? day.temperature_2m_min[0] : null;
        var bin = historicalMeaning(p, pop);
        var at = hhmm(cur.time);
        var hours = nextHours(j, 12);

        var says = '', verdict = '';
        if (pop != null) {
          var obsPct = bin ? Math.round(bin.obs * 100) : null;
          var means, tag = '', lede;

          if (bin != null) {
            var dir = bin.obs > bin.mean ? 'more often' : 'less often';
            means = 'In ' + esc(p.name) + ', forecasts around this level have been ' +
              'followed by rain <b>' + obsPct + '%</b> of the time' +
              ' (' + bin.n + ' days' + (bin.sig ? ', ' + dir + ' than stated' : '') + ').';
            lede = 'In ' + esc(p.name) + ', that has meant';
            tag = bin.sig
              ? '<span class="tag bad">rains ' + dir + ' than stated</span>'
              : '<span class="tag good">in line with the stated chance</span>';
          } else {

            means = 'No comparable group of days in this city\u2019s record.';
            lede = 'In ' + esc(p.name) + ', that has meant';
          }

          says =
            '<div class="fcsays"><div class="k">The forecast says</div>' +
            '<div class="stated"><b>' + pop + '%</b> chance of rain today</div>' +
            '<div class="k k2">' + lede + '</div>' +
            '<div class="obsline">' +
            (bin != null
              ? '<span class="obsnum">0%</span>'
              : '<span class="obsnum none">\u2014</span>') +
            tag + '</div></div>';

          verdict = '<div class="pop">' +
            (bin != null ? gapRail(pop, obsPct) : '') +
            '<div class="means">' + means + '</div></div>';
        } else {
          box.classList.add('nopop');
        }

        box.classList.remove('loading');

        box.classList.toggle('sig', !!(bin && bin.sig));
        box.setAttribute('aria-busy', 'false');
        box.innerHTML =
          art(w.scene) +
          '<div class="fcwrap"><div class="fctop">' +
          '<div class="fcnow">' +
          '<div class="city">' + esc(p.name) + ' now</div>' +
          '<div class="temp">\u2014</div>' +
          '<div class="cond">' + esc(w.label) +
          (hi != null ? ' \u00B7 <span class="hilo">H ' + Math.round(hi) +
            '\u00B0 L ' + Math.round(lo) + '\u00B0</span>' : '') + '</div>' +
          (at ? '<div class="asof">as of ' + esc(at) + ' local time</div>' : '') +
          '</div>' + says + '</div>' + ribbon(hours) + verdict + '</div>';

        countUp($('.temp', box), cur.temperature_2m == null ? null :
          Math.round(cur.temperature_2m), '\u00B0');
        if (bin != null) countUp($('.obsnum', box), Math.round(bin.obs * 100), '%');

        var span = $('#fc .span'), mk = $('#fc .mk.obs');
        if (span && mk) {
          requestAnimationFrame(function () {
            span.style.left = span.dataset.lo + '%';
            span.style.width = span.dataset.hi + '%';
            mk.style.left = mk.dataset.left + '%';
          });
        }

        applySky(w.sky);
      })
      .catch(function () {
        clearTimeout(timer);

        box.hidden = true;
        box.innerHTML = '';
        box.classList.remove('loading');
        box.setAttribute('aria-busy', 'false');
        applySky(null);
      });
  }

  function initPalette() {
    var pal = $('#palette');
    if (!pal) return;
    var input = $('input', pal), list = $('ul', pal), idx = 0, items = [];

    var sections = $$('#rail a').map(function (a) {
      return { id: a.getAttribute('href').slice(1),
               title: a.textContent.trim() };
    });

    function open() {
      pal.hidden = false;
      input.value = '';
      filter('');
      input.focus();
    }
    function close() { pal.hidden = true; }

    function filter(q) {
      q = q.toLowerCase().trim();
      var cities = cfg.cities.filter(function (c) {
        return !q || c.name.toLowerCase().indexOf(q) >= 0 ||
          c.country.toLowerCase().indexOf(q) >= 0;
      }).map(function (c) { return { kind: 'city', c: c }; });
      var secs = !q ? [] : sections.filter(function (s) {
        return s.title.toLowerCase().indexOf(q) >= 0;
      }).map(function (s) { return { kind: 'section', s: s }; });
      items = secs.concat(cities);
      idx = 0;
      list.innerHTML = items.map(function (it, i) {
        if (it.kind === 'section') {
          return '<li role="option" data-anchor="' + it.s.id +
            '" aria-selected="' + (i === 0) + '">' +
            '<span class="swatch sec">#</span>' + esc(it.s.title) +
            '<span class="meta">section</span></li>';
        }
        var c = it.c;
        return '<li role="option" data-slug="' + c.slug + '" aria-selected="' +
          (i === 0) + '"><span class="swatch" style="background:var(--' +
          (c.bss < 0.2 ? 'bad' : c.bss < 0.35 ? 'warn' : 'good') + ')"></span>' +
          esc(c.name) + '<span class="meta">' + esc(c.country) +
          ' \u00B7 skill ' + c.bss.toFixed(2) + '</span></li>';
      }).join('');
    }

    function pick(i, shift) {
      var it = items[i];
      if (!it) return;
      close();
      if (it.kind === 'section') jumpTo(it.s.id);
      else if (shift) setCompare(it.c.slug);
      else go(it.c.slug);
    }

    function move(d) {
      if (!items.length) return;
      idx = (idx + d + items.length) % items.length;
      $$('li', list).forEach(function (li, i) {
        li.setAttribute('aria-selected', i === idx);
        if (i === idx) li.scrollIntoView({ block: 'nearest' });
      });
    }

    input.addEventListener('input', function () { filter(input.value); });
    pal.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { close(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Enter' && items[idx]) {
        e.preventDefault(); pick(idx, e.shiftKey);
      }
    });
    list.addEventListener('click', function (e) {
      var li = e.target.closest('li');
      if (li) pick($$('li', list).indexOf(li), e.shiftKey || e.altKey);
    });
    pal.addEventListener('click', function (e) { if (e.target === pal) close(); });

    var btn = $('#citybtn');
    if (btn) btn.addEventListener('click', open);

    document.addEventListener('keydown', function (e) {
      if (!pal.hidden) return;
      var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(
        (document.activeElement || {}).tagName || '');
      if ((e.key === '/' && !typing) ||
          (e.key.toLowerCase() === 'k' && (e.metaKey || e.ctrlKey))) {
        e.preventDefault();
        open();
      }
    });
  }

  function unfold(node) {
    for (var n = node && node.parentElement; n; n = n.parentElement) {
      if (n.tagName === 'DETAILS') n.open = true;
    }
  }

  function jumpTo(id) {
    var n = document.getElementById(id);
    if (!n) return;
    unfold(n);
    n.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth' });
    history.replaceState(history.state, '', '#' + id);
  }

  function initAnchors() {
    document.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[href^="#"]');
      if (!a) return;
      var n = document.getElementById(a.getAttribute('href').slice(1));
      if (n) unfold(n);
    });
    if (location.hash.length > 1) {
      var n = document.getElementById(location.hash.slice(1));
      if (n) {
        unfold(n);

        requestAnimationFrame(function () { n.scrollIntoView(); });
      }
    }
  }

  function initSortableTables() {
    $$('table').forEach(function (t) {
      var body = t.tBodies[0];
      if (!body || body.rows.length < 8 || !t.tHead) return;
      $$('th', t.tHead).forEach(function (th, col) {
        th.classList.add('sortable');
        th.setAttribute('title', 'Click to sort');
        th.addEventListener('click', function () {
          var dir = th.dataset.dir === 'asc' ? -1 : 1;
          $$('th', t.tHead).forEach(function (h) { delete h.dataset.dir; });
          th.dataset.dir = dir === 1 ? 'asc' : 'desc';
          var rows = Array.prototype.slice.call(body.rows);
          rows.sort(function (a, b) {
            var x = a.cells[col] ? a.cells[col].textContent.trim() : '';
            var y = b.cells[col] ? b.cells[col].textContent.trim() : '';
            var nx = parseFloat(x.replace(/[%\u2212]/g, function (m) {
              return m === '\u2212' ? '-' : ''; }));
            var ny = parseFloat(y.replace(/[%\u2212]/g, function (m) {
              return m === '\u2212' ? '-' : ''; }));
            if (!isNaN(nx) && !isNaN(ny)) return (nx - ny) * dir;
            return x.localeCompare(y) * dir;
          });
          rows.forEach(function (r) { body.appendChild(r); });
        });
      });
    });
  }

  function initRail() {
    var rail = $('#rail');
    if (!rail || !('IntersectionObserver' in window)) return;
    var links = {};
    $$('a', rail).forEach(function (a) { links[a.getAttribute('href').slice(1)] = a; });

    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        $$('a', rail).forEach(function (a) { a.classList.remove('on'); });
        var a = links[en.target.id];
        if (a) a.classList.add('on');
      });
    }, { rootMargin: '-20% 0px -70% 0px' });

    Object.keys(links).forEach(function (id) {
      var n = document.getElementById(id);
      if (n) obs.observe(n);
    });
  }

  function initGlobe() {
    var node = $('#globe');
    if (!node || !window.d3 || !window.Globe) return;
    Promise.all([
      fetch(cfg.landUrl).then(function (r) { return r.json(); }),
      ready
    ])
      .then(function (vals) {
        var land = vals[0];
        globe = new Globe(node, {
          cities: cfg.cities,
          land: land,

          reliefUrl: cfg.reliefUrl,
          biomeUrl: cfg.biomeUrl,

          landDetailUrl: cfg.landDetailUrl,
          onSelect: function (slug) { go(slug); }
        });
        globe.setSelected(current);
        globe.focus(current, false);

        window.__globe = globe;
        var fb = $('#globe-fallback');
        if (fb) fb.hidden = true;
      })
      .catch(function () {

      });
  }

  function initExplainers() {
    function closeAll(except) {
      $$('details.ginfo[open]').forEach(function (d) {
        if (d !== except) d.open = false;
      });
    }
    document.addEventListener('click', function (e) {
      var d = e.target.closest && e.target.closest('details.ginfo');
      if (d && e.target.closest('.ginfo-pop a')) { d.open = false; return; }
      closeAll(d);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      $$('details.ginfo[open]').forEach(function (d) {
        var back = d.contains(document.activeElement);
        d.open = false;
        if (back) d.querySelector('summary').focus();
      });
    });

    document.addEventListener('toggle', function (e) {
      var d = e.target;
      if (!d.classList || !d.classList.contains('ginfo') || !d.open) return;
      var pop = d.querySelector('.ginfo-pop');
      pop.style.left = '';
      var r = pop.getBoundingClientRect();
      var over = r.right - (document.documentElement.clientWidth - 8);
      if (over > 0) {
        pop.style.left = (parseFloat(getComputedStyle(pop).left) -
                          Math.min(over, r.left - 8)) + 'px';
      }
    }, true);
  }

  function init() {
    initThemeControls();
    initTooltip();
    initExplainers();
    initPalette();
    initRail();
    initGlobe();
    initLazyCharts();
    initLazyFolds();
    initAnchors();
    initSortableTables();

    var start = (cfg.inline && cfg.inline.name) || cityName(current);
    highlightCharts(current, start);

    document.addEventListener('click', function (e) {
      var k = e.target.closest && e.target.closest('.cmp-key');
      if (k) setCompare(null);
    });

    document.addEventListener('click', function (e) {
      var g = e.target.closest && e.target.closest('.citychart .cc[data-city]');
      if (!g || g.classList.contains('cc-cmp')) return;
      if (e.shiftKey || e.altKey) setCompare(g.dataset.city);
      else go(g.dataset.city);
    });

    document.addEventListener('mouseover', function (e) {
      var el = e.target.closest && e.target.closest('[data-city]');
      if (el && !cache[el.dataset.city]) payload(el.dataset.city).catch(function () {});
    });

    document.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[data-city]');
      if (!a || e.metaKey || e.ctrlKey || e.shiftKey) return;
      e.preventDefault();
      go(a.dataset.city);
    });

    window.addEventListener('popstate', function (e) {

      var slug = (e.state && e.state.slug) || slugFromLocation();
      go(slug, false);
    });

    history.replaceState({ slug: current }, '', location.href);
    if (cache[current]) loadForecast(cache[current]);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
