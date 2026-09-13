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

  var skyOn = store.get('wc-sky', '1') === '1';
  var lastSky = null;

  function applySky(cls) {
    lastSky = cls || null;
    // The weather layer only ever touches backdrop and ocean tint. It is not
    // permitted to influence any token that carries text contrast, which is why
    // it is a separate attribute rather than a fourth theme.
    if (skyOn && lastSky) root.setAttribute('data-sky', lastSky);
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
    var t = $('#skytoggle');
    if (t) {
      t.setAttribute('aria-pressed', skyOn ? 'true' : 'false');
      t.addEventListener('click', function () {
        skyOn = !skyOn;
        store.set('wc-sky', skyOn ? '1' : '0');
        t.setAttribute('aria-pressed', skyOn ? 'true' : 'false');
        applySky(lastSky);
      });
    }
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
    if (push) history.pushState({ slug: p.slug }, '', cityUrl(p.slug));
    loadForecast(p);
  }

  function cityUrl(slug) {
    return slug === cfg.defaultSlug ? cfg.base : cfg.base + 'city/' + slug + '/';
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
  /* WMO 4677 weather codes -> label, glyph, sky class. Grouped exactly once,
   * here, so the implicit claim "this is what the sky is doing there" stays
   * auditable instead of scattered through the stylesheet. */
  var WMO = [
    [[0], 'Clear sky', '\u2600\uFE0F', 'clear'],
    [[1, 2], 'Mainly clear', '\u26C5', 'clear'],
    [[3], 'Overcast', '\u2601\uFE0F', 'cloud'],
    [[45, 48], 'Fog', '\uD83C\uDF2B\uFE0F', 'fog'],
    [[51, 53, 55, 56, 57], 'Drizzle', '\uD83C\uDF26\uFE0F', 'rain'],
    [[61, 63, 65, 66, 67, 80, 81, 82], 'Rain', '\uD83C\uDF27\uFE0F', 'rain'],
    [[71, 73, 75, 77, 85, 86], 'Snow', '\u2744\uFE0F', 'snow'],
    [[95, 96, 99], 'Thunderstorm', '\u26C8\uFE0F', 'storm']
  ];

  function decode(code, isDay) {
    for (var i = 0; i < WMO.length; i++) {
      if (WMO[i][0].indexOf(code) >= 0) {
        var sky = WMO[i][3];
        if (!isDay && (sky === 'clear' || sky === 'cloud')) sky = 'night';
        return { label: WMO[i][1], icon: WMO[i][2], sky: sky };
      }
    }
    return { label: 'Unknown', icon: '\u2601\uFE0F', sky: null };
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

  function loadForecast(p) {
    var box = $('#fc');
    if (!box || p.lat == null) return;
    box.hidden = false;
    box.classList.add('loading');

    var url = cfg.forecastApi +
      '?latitude=' + p.lat + '&longitude=' + p.lon +
      '&current=temperature_2m,weather_code,is_day' +
      '&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max' +
      '&forecast_days=1&timezone=auto';

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

        var means = '';
        if (bin != null) {
          var dir = bin.obs > bin.mean ? 'more often' : 'less often';
          means = 'In ' + esc(p.name) + ', forecasts around this level have been ' +
            'followed by rain <b>' + Math.round(bin.obs * 100) + '%</b> of the time' +
            ' (' + bin.n + ' days' + (bin.sig ? ', ' + dir + ' than stated' : '') + ').';
        } else if (pop != null) {
          means = 'No comparable group of days in this city\u2019s record.';
        }

        box.classList.remove('loading');
        box.innerHTML =
          '<div class="icon" aria-hidden="true">' + w.icon + '</div>' +
          '<div><div class="temp">' + (cur.temperature_2m != null ? Math.round(cur.temperature_2m) + '\u00B0' : '\u2014') + '</div>' +
          '<div class="cond">' + esc(w.label) +
          (hi != null ? ' \u00B7 ' + Math.round(hi) + '\u00B0 / ' + Math.round(lo) + '\u00B0' : '') +
          '</div></div>' +
          (pop != null ?
            '<div class="pop"><div class="big">' + pop + '% chance of rain today</div>' +
            '<div class="means">' + means + '</div></div>' : '');

        applySky(w.sky);
      })
      .catch(function () {
        clearTimeout(timer);
        // The analysis is the product. A third-party outage hides the strip; it
        // never shows an error or blocks anything else on the page.
        box.hidden = true;
        box.classList.remove('loading');
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
    document.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault(); pal.hidden ? open() : close();
      }
    });
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
          excluded: cfg.excluded,
          land: land,
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
      var slug = (e.state && e.state.slug) || cfg.defaultSlug;
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
