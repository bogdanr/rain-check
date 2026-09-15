/* Report controller: city switching, routing, themes, forecast, palette.
 *
 * Progressive enhancement throughout. Every city already exists as a real
 * server-rendered page at /city/<slug>/, so deep links, search engines, reader
 * modes and a failed script load all work without this file. What it adds is
 * instant switching, the globe, the theme controls and the live forecast.
 *
 * The standing rule: this file may display numbers Python computed, and must
 * never compute a statistic itself. The forecast annotation looks a stated
 * probability up in a table built by `city_report.py`; it does not derive one.
 */
(function () {
  'use strict';

  var cfg = JSON.parse(document.getElementById('site-config').textContent);
  var root = document.documentElement;
  var cache = {};                      // slug -> payload
  // The page this script loaded on, not necessarily the default city: every
  // capital has its own static page, and getting this wrong would leave the
  // globe and the city button pointing at Bucharest on all fifteen of them.
  var current = (cfg.inline && cfg.inline.slug) || cfg.defaultSlug;
  var globe = null;

  if (cfg.inline) cache[cfg.inline.slug] = cfg.inline;

  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };

  /* ------------------------------------------------------------- theming */
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
    // The weather layer only ever touches the forecast card's sky and the globe
    // ocean. It is not permitted to influence any token that carries text
    // contrast, which is why it is a separate attribute rather than a fourth
    // theme.
    if (lastSky) root.setAttribute('data-sky', lastSky);
    else root.removeAttribute('data-sky');
  }

  function initThemeControls() {
    applyTheme(store.get('wc-theme', 'observatory'));
    // Below 700px the segmented control collapses to whichever option is
    // active (see app.css), so a plain "set this theme" handler would make the
    // button a no-op - it would only ever set the theme it already shows.
    // Collapsed, it cycles instead; expanded, it selects directly.
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

  /* ------------------------------------------------------------- tooltip */
  function initTooltip() {
    var tip = $('#tip');
    if (!tip) return;
    // Delegated, because city sections are replaced wholesale on switch and
    // per-element listeners would die with the markup that carried them.
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

  /* -------------------------------------------------------------- cities */
  function payload(slug) {
    if (cache[slug]) return Promise.resolve(cache[slug]);
    var url = cfg.cityUrls[slug];
    if (!url) return Promise.reject(new Error('unknown city ' + slug));
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (p) { cache[slug] = p; return p; });
  }

  function renderCity(p, push) {
    current = p.slug;

    // The card beside the globe is part of the selection, not part of the
    // report, so it is updated here with everything else the click changed.
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
      // The fence below the city panel names the selection, so it has to track
      // it; a stale "you currently have Paris selected" under Bucharest tables
      // is exactly the confusion the fence exists to prevent.
      ds.innerHTML = p.name === 'Bucharest' ? '' :
        'You currently have <b>' + esc(p.name) + '</b> selected; its own ' +
        'results are above.';
    }
    $$('[data-deep-only]').forEach(function (n) { n.hidden = !p.deep; });

    if (globe) globe.focus(p.slug, true);
    highlightCharts(p.slug, p.name);
    if (push) history.pushState({ slug: p.slug }, '', cityUrl(p.slug));
    loadForecast(p);
  }

  /* Pick the selected city out of the cross-city charts.
   *
   * These charts draw all 97 cities, and used to be PNGs with Bucharest fixed
   * in red - so selecting Lisbon left the reader looking at a chart that
   * highlighted somebody else. Python still computes every coordinate; the only
   * thing that happens here is a class toggle and a move to the end of the
   * paint order, because SVG has no z-index and the highlighted line would
   * otherwise stay buried under the mass it is meant to stand out from. */
  function highlightCharts(slug, name) {
    $$('.citychart').forEach(function (svg) {
      var prev = svg.querySelector('.cc.on');
      if (prev) prev.classList.remove('on');
      var g = svg.querySelector('.cc[data-city="' + slug + '"]');
      if (!g) return;
      g.classList.add('on');
      var top = svg.querySelector('.cc-top');
      if (top) top.appendChild(g);
    });
    $$('.chartkey b').forEach(function (b) {
      b.textContent = name || 'your selection';
    });
  }

  function cityUrl(slug) {
    return slug === cfg.defaultSlug ? cfg.base : cfg.base + 'city/' + slug + '/';
  }

  /* Which city the current URL names.
   *
   * The history state object is a convenience, not the record. Following any
   * in-page link - the rail, or the card's link down to the full verdict -
   * is a fragment navigation, and a fragment navigation pushes a history entry
   * whose state is null *and* fires popstate. Reading the slug out of the state
   * alone therefore meant every such click on /city/helsinki/ was handled as
   * "no state, so this must be the default city" and quietly swapped the whole
   * report back to Bucharest while the address bar still said Helsinki.
   *
   * The path is what identifies the city - it is what a deep link, a reload and
   * a no-JS visit all agree on - so the path is what decides here.
   */
  function slugFromLocation() {
    var base = cfg.base || '/';
    var path = location.pathname;
    if (path.indexOf(base) === 0) path = path.slice(base.length);
    var m = /^city\/([^/]+)\/?$/.exec(path);
    return m && cfg.cityUrls[m[1]] ? m[1] : cfg.defaultSlug;
  }

  function go(slug, push) {
    if (slug === current && cache[slug]) return;
    payload(slug).then(function (p) { renderCity(p, push !== false); })
      .catch(function () {
        // Fall back to a real page load. The static pages exist, so a failed
        // fetch degrades to a slower navigation rather than a broken report.
        location.href = cityUrl(slug);
      });
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /* ------------------------------------------------------------ forecast */
  /* WMO 4677 weather codes -> label, scene, sky class. Grouped exactly once,
   * here, so the implicit claim "this is what the sky is doing there" stays
   * auditable instead of scattered through the stylesheet.
   *
   * The third column used to be an emoji. Emoji are a different drawing on
   * every operating system, cannot take a theme, and cannot be stopped for a
   * reader who has asked for less motion - three properties this page cannot
   * accept from its most prominent illustration. They are now keys into the
   * inline SVG scenes below, which are drawn from the stylesheet's own tokens. */
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
          // A clear night is a different picture, not a dimmer sun. An
          // overcast night keeps its cloud and only changes the backdrop.
          if (sc === 'clear') sc = 'night';
          sky = 'night';
        }
        return { label: WMO[i][1], scene: sc, sky: sky };
      }
    }
    return { label: 'Unknown', scene: 'cloud', sky: null };
  }

  /* -- the sky ---------------------------------------------------------------
   * One panorama behind the whole card rather than an icon in a box.
   *
   * It is drawn at 900x400 - roughly the card's own pixel size - and sliced, so
   * it crops like a photograph at a scale near 1:1. Drawing it small and
   * letting `slice` blow it up was the first attempt and it magnified every
   * shape ~3x: a cloud became a grey blob with no edges in frame and the sun
   * ran off the corner. Artwork has to be authored at the size it is seen.
   *
   * The anchor is xMax/YMin because the focal element - sun, moon, bolt - lives
   * top right. A narrow phone card crops to roughly the right third, and that
   * is the third worth keeping.
   *
   * Everything is soft and low-contrast except that one focal element. That is
   * the difference between atmosphere and clip art, and it is also what makes
   * it safe to put text on top.
   *
   * Placement goes on a wrapping <g>, never on the element that animates: a CSS
   * `transform` keyframe replaces the `transform` attribute outright, so a
   * shape carrying both silently loses the position it was authored at. */
  var CLOUD = 'M32 68A15 15 0 0 1 36 42A21 21 0 0 1 69 45A13 13 0 0 1 76 68Z';
  var CLOUD_BOX = [32, 42, 76, 68];
  var MOON = 'M63 22A26 26 0 1 0 63 74A32 32 0 1 1 63 22Z';
  var MOON_BOX = [37, 22, 63, 74];
  var BOLT = 'M56 58L40 82H51L46 96L68 70H55Z';
  var BOLT_BOX = [40, 58, 68, 96];
  var W = 900, H = 400;

  // Deterministic scatter. Math.random() would reshuffle the rain on every city
  // switch, and would make a screenshot comparison meaningless.
  function rnd(i) { return ((i * 9301 + 49297) % 233280) / 233280; }

  /* Ask for a shape by where it should appear, not by its own coordinates. */
  function place(cx, cy, h, box) {
    var s = h / (box[3] - box[1]);
    return 'translate(' + (cx - (box[0] + box[2]) / 2 * s).toFixed(1) + ' ' +
      (cy - (box[1] + box[3]) / 2 * s).toFixed(1) + ') scale(' + s.toFixed(3) + ')';
  }

  /* A cloud is three overlapping lumps under ONE group opacity, not three
   * translucent shapes: at per-shape alpha every overlap paints twice and the
   * seams turn the mass into a diagram of itself. Grouping flattens the union
   * first, which is also how you get a soft silhouette without a blur - and a
   * filter here would mean a compositor pass on every frame of the drift. */
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

  /* Precipitation across the whole card, not under one cloud. */
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

  /* Every star used to twinkle on the same 4s clock, offset only by delay, and
   * a field pulsing in one rhythm reads as a static dot grid - close enough to
   * frozen rain that it was reported as exactly that. Each star now gets its
   * own period as well as its own phase, so the field shimmers instead of
   * breathing, and no two neighbours ever move together. */
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

  /* -- the gap ---------------------------------------------------------------
   * The one thing this card exists to say is that a stated probability and what
   * it has actually meant here are two different numbers. A ring could not say
   * it: 40% and 46% are the same ring. One rail from 0 to 100 with both marks
   * on it says it at a glance, and the distance between them *is* the finding.
   *
   * Both offsets are numbers Python computed, turned into positions. Ordering
   * the ends of the span is geometry; nothing here derives a probability. */
  function gapRail(stated, obs) {
    var lo = Math.min(stated, obs), hi = Math.max(stated, obs);
    // Labels are centred on their marks, but clamped so a 0% or 100% reading
    // cannot hang off the side of the card.
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

  /* -- the next twelve hours -------------------------------------------------
   * Vendor values, displayed. The selection is "the hours from now on" - a
   * slice of an array by timestamp, not an aggregate. No peak is named and
   * nothing is smoothed: those would be claims, and claims belong in Python. */
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
    // Half a ribbon says less than none: late in the local day there may not be
    // twelve hours left in the response.
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

  /* The headline feature: what has this probability actually meant here?
   * Straight lookup into the reliability table Python shipped. */
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

  /* Count a figure up to its value. Purely presentational: the destination is
   * the number that was passed in, and rounding on the way there never changes
   * where it lands. */
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

  /* A skeleton in the resolved card's shape, not a dimmed empty box: the strip
   * is revealed before the third-party response arrives, so whatever stands in
   * for it has to occupy the same height or the page shifts underneath the
   * reader when the data lands. */
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

        // Every figure below is either a vendor value being displayed or a
        // number out of the city's own reliability table. Nothing here is
        // averaged, rescaled or otherwise worked out in the browser.
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
            // A real path, not a defensive one: several cities have thin
            // records, and an unstyled gap is worse than a plain sentence.
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
        // Significance is the report's own verdict vocabulary, so it drives the
        // colour of the observed mark as well as the tag beside it.
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

        // The observed mark starts on top of the stated one and slides out as
        // the span grows behind it, so the gap is something you watch open
        // rather than something you have to measure. Handing the targets to CSS
        // a frame later gives the transition something to interpolate; under
        // reduced motion the transition is already collapsed, so this just sets
        // the final position.
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
        // The analysis is the product. A third-party outage hides the strip; it
        // never shows an error or blocks anything else on the page.
        box.hidden = true;
        box.innerHTML = '';
        box.classList.remove('loading');
        box.setAttribute('aria-busy', 'false');
        applySky(null);
      });
  }

  /* ------------------------------------------------------------- palette */
  function initPalette() {
    var pal = $('#palette');
    if (!pal) return;
    var input = $('input', pal), list = $('ul', pal), idx = 0, items = [];

    function open() {
      pal.hidden = false;
      input.value = '';
      filter('');
      input.focus();
    }
    function close() { pal.hidden = true; }

    function filter(q) {
      q = q.toLowerCase().trim();
      items = cfg.cities.filter(function (c) {
        return !q || c.name.toLowerCase().indexOf(q) >= 0 ||
          c.country.toLowerCase().indexOf(q) >= 0;
      });
      idx = 0;
      list.innerHTML = items.map(function (c, i) {
        return '<li role="option" data-slug="' + c.slug + '" aria-selected="' +
          (i === 0) + '"><span class="swatch" style="background:var(--' +
          (c.bss < 0.2 ? 'bad' : c.bss < 0.35 ? 'warn' : 'good') + ')"></span>' +
          esc(c.name) + '<span class="meta">' + esc(c.country) +
          ' \u00B7 skill ' + c.bss.toFixed(2) + '</span></li>';
      }).join('');
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
        e.preventDefault(); close(); go(items[idx].slug);
      }
    });
    list.addEventListener('click', function (e) {
      var li = e.target.closest('li');
      if (li) { close(); go(li.dataset.slug); }
    });
    pal.addEventListener('click', function (e) { if (e.target === pal) close(); });

    var btn = $('#citybtn');
    if (btn) btn.addEventListener('click', open);
  }

  /* ---------------------------------------------------------------- rail */
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

  /* --------------------------------------------------------------- globe */
  function initGlobe() {
    var node = $('#globe');
    if (!node || !window.d3 || !window.Globe) return;
    fetch(cfg.landUrl)
      .then(function (r) { return r.json(); })
      .then(function (land) {
        globe = new Globe(node, {
          cities: cfg.cities,
          land: land,
          // The shaded terrain, fetched by the globe itself once it has
          // painted. Passing URLs rather than data keeps the decision about
          // when to spend that bandwidth inside the component that knows
          // whether it is already usable without it.
          reliefUrl: cfg.reliefUrl,
          biomeUrl: cfg.biomeUrl,
          // The finer coastline, fetched only if the reader zooms past the
          // point where the 110m one shows its corners.
          landDetailUrl: cfg.landDetailUrl,
          onSelect: function (slug) { go(slug); }
        });
        globe.setSelected(current);
        globe.focus(current, false);
        // Exposed so the verification harness can project known lon/lat pairs
        // and sample the rendered pixels. Land/sea inversion is invisible to
        // unit tests but obvious to a pixel probe, so the hook earns its keep.
        window.__globe = globe;
        var fb = $('#globe-fallback');
        if (fb) fb.hidden = true;
      })
      .catch(function () {
        // Leave the plain city list visible. A decorative feature must never be
        // able to remove the means of navigating the report.
      });
  }

  /* --------------------------------------------------------------- init */
  function init() {
    initThemeControls();
    initTooltip();
    initPalette();
    initRail();
    initGlobe();

    // The first city is server-rendered, so renderCity() has not run for it and
    // the charts would open with nothing picked out.
    var start = (cfg.inline && cfg.inline.name) ||
                (cfg.cities.filter(function (c) { return c.slug === current; })[0] || {}).name;
    highlightCharts(current, start);

    // Hover-prefetch: by the time a click lands the payload is usually already
    // in cache, so switching feels instant without loading fifteen cities up
    // front.
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
      // Null state means an entry this script did not create - a fragment
      // navigation, most often. The URL still knows which city we are on.
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
