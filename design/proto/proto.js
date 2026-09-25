/* Atlas Noir prototype - page logic.
 * Every number shown comes from data.json (written by export.py from the
 * built site and the processed tables). This file only formats and places.  */
(function () {
  'use strict';

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  function pct(v, d) { return (v * 100).toFixed(d || 0) + '%'; }
  function minus(s) { return String(s).replace('-', '\u2212'); }
  function title(s) { return s.toLowerCase().replace(/\b\w/g, function (c) { return c.toUpperCase(); }); }
  function mulberry(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; var t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

  fetch('data.json').then(function (r) { return r.json(); }).then(init);

  function init(D) {
    var h = D.hero, U = D.umbrella, C = D.coverage;
    var tier = tierOf(D.tiers, h.bss);

    /* ── Text fields ─────────────────────────────── */
    var K = {
      name: h.name, country: h.country, n_cities: h.n_cities, lead: h.lead,
      ci: h.bss_lo.toFixed(2) + ' \u2013 ' + h.bss_hi.toFixed(2), tier: tier[1],
      rank: h.rank, rank_range: h.rank_lo + '\u2013' + h.rank_hi,
      n: h.n.toLocaleString('en') + ' days', base_pct: pct(h.base_rate),
      station: title(h.station), station_km: h.station_km,
      event: D.event, span: h.first + ' \u2192 ' + h.last, as_of: D.as_of,
      umb_n: U.n, umb_world: U.world_n, n_countries: C.n_countries,
      floor: (C.floor / 1000) + 'k'
    };
    $$('[data-k]').forEach(function (el) { if (el.dataset.k in K) el.textContent = K[el.dataset.k]; });
    var counts = { bss: h.bss, covered: C.n_covered, missing_bn: C.people_missing / 1e9 };
    $$('[data-count]').forEach(function (el) {
      el._to = counts[el.dataset.count]; el._fmt = +(el.dataset.fmt || 0); el._suf = el.dataset.suffix || '';
      el.textContent = (reduce ? el._to : 0).toFixed(el._fmt) + el._suf;
    });

    scale(D.tiers, h, tier);
    promise(h);
    umbrella(U, h.name);
    tips(D, h, tier);
    var stage = makeStage(D, h, C);
    chapters(stage);
    reveal();
    document.addEventListener('keydown', function (e) {
      if (e.key === '/' && document.activeElement.tagName !== 'INPUT') { e.preventDefault(); $('.citypill').focus(); }
    });
    $('.citypill').title = 'City search palette - Phase 2 (prototype shows Bucharest only)';
  }

  function tierOf(T, v) {
    for (var i = 0; i < T.length; i++) if (T[i][0] === null || v < T[i][0]) return T[i];
    return T[T.length - 1];
  }

  /* ── Skill scale: the site's own tiers, 0 to 0.7 ─ */
  function scale(T, h, cur) {
    var max = 0.7, track = $('#scale-track'), lo = 0;
    function x(v) { return Math.max(0, Math.min(1, v / max)) * 100; }
    T.forEach(function (t) {
      var hi = t[0] === null ? max : t[0];
      if (hi <= 0) return;                       // "cannot beat climatology" lies left of 0
      var b = document.createElement('span');
      b.className = 'scale-band' + (t === cur ? ' cur' : '');
      b.style.left = x(lo) + '%'; b.style.width = 'calc(' + (x(hi) - x(lo)) + '% - 2px)';
      b.title = t[1];
      var em = document.createElement('em'); em.textContent = String(+lo.toFixed(2));
      b.appendChild(em); track.insertBefore(b, track.firstChild); lo = hi;
    });
    track.style.height = '42px';
    setTimeout(function () {
      track.style.setProperty('--ci-lo', x(h.bss_lo) + '%');
      track.style.setProperty('--ci-w', (x(h.bss_hi) - x(h.bss_lo)) + '%');
      track.style.setProperty('--pt', x(h.bss) + '%');
    }, reduce ? 0 : 450);
  }

  /* ── The promise: one square per real day ─────── */
  function promise(h) {
    var seg = $('.bins'), grid = $('.grid-days'), cur = -1;
    h.bins.forEach(function (b, i) {
      var btn = document.createElement('button');
      btn.type = 'button'; btn.setAttribute('role', 'tab');
      btn.textContent = Math.round(b.lo * 100) + '\u2013' + Math.round(b.hi * 100) + '%';
      if (b.sig) { var w = document.createElement('span'); w.className = 'warnmark'; w.textContent = '!';
        w.setAttribute('aria-label', 'off by more than chance'); btn.appendChild(w); }
      btn.addEventListener('click', function () { show(i); });
      seg.appendChild(btn);
    });
    seg.addEventListener('keydown', function (e) {
      var d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (!d) return; e.preventDefault();
      var n = (cur + d + h.bins.length) % h.bins.length; show(n); seg.children[n].focus();
    });
    function show(i) {
      cur = i; var b = h.bins[i], wet = Math.round(b.rained * b.n), rnd = mulberry(i * 7919 + 17);
      $$('button', seg).forEach(function (x, j) { x.setAttribute('aria-selected', j === i); x.tabIndex = j === i ? 0 : -1; });
      var order = []; for (var k = 0; k < b.n; k++) order.push(k);
      for (k = order.length - 1; k > 0; k--) { var r = Math.floor(rnd() * (k + 1)), t = order[k]; order[k] = order[r]; order[r] = t; }
      var isWet = {}; for (k = 0; k < wet; k++) isWet[order[k]] = 1;
      grid.textContent = '';
      var frag = document.createDocumentFragment();
      for (k = 0; k < b.n; k++) {
        var sq = document.createElement('i');
        sq.style.setProperty('--d', reduce ? '0ms' : Math.round(rnd() * 500) + 'ms');
        frag.appendChild(sq);
      }
      grid.appendChild(frag);
      grid.setAttribute('aria-label', b.n + ' days; it rained on ' + wet);
      requestAnimationFrame(function () { requestAnimationFrame(function () {
        $$('i', grid).forEach(function (sq, j) { if (isWet[j]) sq.classList.add('wet'); });
      }); });
      $('#pr-said').textContent = pct(b.said);
      $('#pr-got').textContent = pct(b.rained);
      var v = $('#pr-verdict');
      v.classList.toggle('off', b.sig);
      v.textContent = !b.sig
        ? 'Honest here: the gap is within what chance alone explains.'
        : b.rained > b.said
          ? 'Rain came more often than it said, by more than chance explains.'
          : 'Too sure of itself: rain came less often than it said, by more than chance explains.';
      $('#pr-n').textContent = b.n + ' days \u00b7 it rained on ' + wet;
    }
    var start = 0;
    h.bins.forEach(function (b, i) { if (b.lo <= 0.4 && b.hi > 0.4) start = i; });
    show(start);
  }

  /* ── How cheap is your umbrella? ──────────────── */
  function umbrella(U, name) {
    var inp = $('#alpha'), svg = $('#umb-chart'), NS = 'http://www.w3.org/2000/svg';
    var W = 860, H = 190, L = 44, R = 12, T = 12, B = 30;
    var ymax = Math.ceil(Math.max.apply(null, U.best.concat(U.follow)) * 10) / 10, ymin = -1;
    function X(a) { return L + a * (W - L - R); }
    function Y(v) { return T + (ymax - Math.max(ymin, Math.min(ymax, v))) / (ymax - ymin) * (H - T - B); }
    function el(n, a, txt) { var e = document.createElementNS(NS, n); for (var k in a) e.setAttribute(k, a[k]);
      if (txt != null) e.textContent = txt; svg.appendChild(e); return e; }
    function path(arr) { return arr.map(function (v, i) { return (i ? 'L' : 'M') + X(U.alpha[i]).toFixed(1) + ',' + Y(v).toFixed(1); }).join(''); }
    el('rect', { class: 'neg-zone', x: L, y: Y(0), width: W - L - R, height: Y(ymin) - Y(0) });
    [ymin, -0.5, 0, 0.5].forEach(function (v) { if (v > ymax) return;
      el('line', { class: v === 0 ? 'zero' : 'ax', x1: L, x2: W - R, y1: Y(v), y2: Y(v) });
      el('text', { x: L - 8, y: Y(v) + 4, 'text-anchor': 'end' }, minus(Math.round(v * 100)) + '%'); });
    [0, .2, .4, .6, .8, 1].forEach(function (a) { el('text', { x: X(a), y: H - 8, 'text-anchor': 'middle' }, a.toFixed(1)); });
    el('text', { x: X(0.3), y: Y(ymin) - 8 }, 'below 0%: worse than ignoring the forecast (clipped at \u2212100%)');
    el('path', { class: 'l-world', d: path(U.world_follow) });
    el('path', { class: 'l-best', d: path(U.best) });
    el('path', { class: 'l-follow', d: path(U.follow) });
    var cur = el('line', { class: 'cursor', y1: T, y2: H - B });
    var dot = el('circle', { class: 'dot', r: 6 });
    var lg = el('g', { class: 'lg', transform: 'translate(' + (W - R - 250) + ',' + (T + 6) + ')' });
    [['l-follow', 'follow the app (' + name + ')'], ['l-best', 'same forecast, best trigger'], ['l-world', 'follow the app, world median']]
      .forEach(function (r, i) { var g = document.createElementNS(NS, 'g'); g.setAttribute('transform', 'translate(0,' + i * 16 + ')');
        var ln = document.createElementNS(NS, 'line'); ln.setAttribute('class', r[0]); ln.setAttribute('x2', 22); ln.setAttribute('y1', -4); ln.setAttribute('y2', -4);
        var tx = document.createElementNS(NS, 'text'); tx.setAttribute('x', 30); tx.textContent = r[1];
        g.appendChild(ln); g.appendChild(tx); lg.appendChild(g); });

    var ex = [['carry an umbrella', 0.05], ['move a picnic indoors', 0.25], ['cancel an outdoor event', 0.6]];
    var exBox = $('#umb-ex');
    var exLab = document.createElement('span'); exLab.className = 'mono dim'; exLab.style.fontSize = '11px';
    exLab.style.alignSelf = 'center'; exLab.textContent = 'for example'; exBox.appendChild(exLab);
    ex.forEach(function (e) {
      var b = document.createElement('button'); b.type = 'button'; b.textContent = e[0]; b.dataset.a = e[1];
      b.addEventListener('click', function () { inp.value = idx(e[1]); upd(); });
      exBox.appendChild(b);
    });
    function idx(a) { var best = 0; U.alpha.forEach(function (x, i) { if (Math.abs(x - a) < Math.abs(U.alpha[best] - a)) best = i; }); return best; }
    inp.max = U.alpha.length - 1; inp.value = idx(0.05);

    function upd() {
      var i = +inp.value, a = U.alpha[i], f = U.follow[i], b = U.best[i], w = U.world_follow[i], trig = U.trigger[i];
      inp.style.setProperty('--p', (i / (U.alpha.length - 1) * 100) + '%');
      inp.setAttribute('aria-valuetext', 'ratio ' + a.toFixed(2) + ', following the app gives ' + pct(f));
      $$('button', exBox).forEach(function (x) { x.setAttribute('aria-pressed', Math.abs(+x.dataset.a - a) < 0.006); });
      var v = $('#umb-v'); v.textContent = minus(pct(f)); v.classList.toggle('neg', f < 0);
      var s;
      if (f < 0) {
        s = 'Acting whenever the app shows more than <b>' + pct(a) + '</b> leaves you <b>worse off than ignoring the forecast</b> and just going by how often it usually rains.';
      } else if (b - f > 0.05) {
        s = 'Following the app\u2019s number gets you <b>' + pct(f) + '</b> of the possible value.';
      } else {
        s = 'Here the app\u2019s number works as printed: <b>' + pct(f) + '</b> of what a perfect forecast would give.';
      }
      if (b <= 0) s += ' At this ratio no trigger helps: the usual rain rate is as good as this forecast.';
      else if (b - f > 0.05 && trig != null) s += ' Acting above <b>' + pct(trig) + '</b> instead, the same forecast would give <b>' + pct(b) + '</b>.';
      s += ' <span class="dim">World median: ' + minus(pct(w)) + '.</span>';
      $('#umb-say').innerHTML = s;
      cur.setAttribute('x1', X(a)); cur.setAttribute('x2', X(a));
      dot.setAttribute('cx', X(a)); dot.setAttribute('cy', Y(f));
    }
    inp.addEventListener('input', upd);
    upd();
  }

  /* ── Provenance tooltips ──────────────────────── */
  function tips(D, h, tier) {
    var tip = $('#tip');
    var big = $('[data-count="bss"]'); big.tabIndex = 0; big.dataset.tip = 'bss';
    var T = {
      rank: '<b>Rank ' + h.rank + ' of ' + h.n_cities + '.</b> Allowing for the uncertainty in every city\u2019s score, it could sit anywhere from ' + h.rank_lo + ' to ' + h.rank_hi + '.',
      bss: '<b>Brier skill score ' + h.bss.toFixed(3) + '</b> \u00b7 ' + tier[1] +
        '<dl><dt>range</dt><dd>' + h.bss_lo.toFixed(3) + ' \u2013 ' + h.bss_hi.toFixed(3) + ' (95%)</dd>' +
        '<dt>days</dt><dd>' + h.n + '</dd><dt>vs</dt><dd>the city\u2019s own rain rate (' + pct(h.base_rate, 1) + ')</dd>' +
        '<dt>event</dt><dd>' + D.event + '</dd><dt>source</dt><dd>data/cities/bucharest.json</dd></dl>'
    };
    function show(e) {
      var t = e.target.closest('[data-tip]'); if (!t) return;
      tip.innerHTML = T[t.dataset.tip]; tip.hidden = false;
      var r = t.getBoundingClientRect(), w = tip.offsetWidth;
      tip.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left)) + 'px';
      tip.style.top = (r.bottom + 10) + 'px';
    }
    function hide() { tip.hidden = true; }
    document.addEventListener('pointerover', function (e) { if (e.target.closest('[data-tip]')) show(e); });
    document.addEventListener('pointerout', function (e) { if (e.target.closest('[data-tip]')) hide(); });
    document.addEventListener('focusin', show); document.addEventListener('focusout', hide);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hide(); });
  }

  /* ── Stage and camera script ──────────────────── */
  function makeStage(D, h, C) {
    if (!window.WebGL2RenderingContext) { document.body.classList.add('nogl'); return null; }
    var st = new AtlasStage($('#stage'), $('#marks'), {
      elev: 'assets/relief-elev.webp', biome: 'assets/relief-biome.webp', dragTarget: $('#stage-hit'), sky: $('#sky'),
      onFail: function () { document.body.classList.add('nogl'); }
    });
    if (st.failed) { document.body.classList.add('nogl'); return null; }
    var tone = { bad: 'q0', ok: 'q1', good: 'q2' };
    st.sets = {
      cities: D.cities.filter(function (c) { return c[1] !== h.name; }).map(function (c) {
        return { lon: c[4], lat: c[3], r: 2.6, cls: 'city ' + tone[tierOf(D.tiers, c[5])[2]], title: c[1] + ' \u00b7 skill ' + c[5].toFixed(2) };
      }).concat([{ lon: h.lon, lat: h.lat, r: 6, cls: 'here', pulse: true, label: h.name, title: h.name + ' \u00b7 skill ' + h.bss.toFixed(2) }]),
      countries: C.countries.map(function (c) {
        var cls = c[4] > 0 ? 'ok' : c[5] === 'no gauge nearby' ? 'none' : 'broken';
        return { lon: c[2], lat: c[1], r: Math.min(22, 2.5 + Math.sqrt(c[3] / 1e6) * 1.1), cls: cls, title: c[0] + ' \u00b7 ' + (cls === 'ok' ? 'can be checked' : c[5]) };
      })
    };
    st.shots = function () {
      var m = innerWidth <= 860;
      return {
        // First view: 8% smaller than before so the whole disc, south pole and
        // displaced relief included, sits inside the viewport with a margin.
        city:         m ? { lon: h.lon, lat: h.lat - 8, k: .425, cx: .5, cy: .30, dim: .1 } : { lon: h.lon - 6, lat: h.lat - 10, k: .43, cx: .70, cy: .525, dim: 0 },
        'city-close': m ? { lon: h.lon, lat: h.lat, k: .8, cx: .5, cy: .34, dim: .1 }  : { lon: h.lon + 4, lat: h.lat - 2, k: .82, cx: .33, cy: .56, dim: 0, dur: 1500 },
        dim:          { lon: h.lon + 35, lat: 22, k: m ? .6 : .62, cx: .5, cy: .56, dim: .74, spin: .03 },
        world:        m ? { lon: 20, lat: 12, k: .44, cx: .5, cy: .30, dim: .05, spin: .02 } : { lon: 22, lat: 14, k: .45, cx: .32, cy: .54, dim: 0, spin: .02, dur: 1500 },
        dock:         { lon: h.lon, lat: h.lat, k: m ? .09 : .1, cx: m ? .86 : .93, cy: m ? .9 : .87, dim: .1 }
      };
    };
    st.go = function (name, instant) {
      st.current = name;
      st.setMarkers(name === 'world' ? st.sets.countries : st.sets.cities);
      st.fly(st.shots()[name], instant);
    };
    st.go('city', true);
    addEventListener('resize', function () { st.go(st.current, true); });
    return st;
  }

  function chapters(stage) {
    var links = {}; $$('.chapters a').forEach(function (a) { links[a.dataset.ch] = a; });
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        var id = e.target.id;
        Object.keys(links).forEach(function (k) { if (k === id) links[k].setAttribute('aria-current', 'true'); else links[k].removeAttribute('aria-current'); });
        if (stage && stage.current !== e.target.dataset.stage) stage.go(e.target.dataset.stage);
      });
    }, { rootMargin: '-45% 0px -45% 0px' });
    $$('.chapter').forEach(function (s) { io.observe(s); });
  }

  function reveal() {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add('in'); io.unobserve(e.target);
        $$('[data-count]', e.target).forEach(count);
      });
    }, { threshold: 0.25 });
    $$('.rise').forEach(function (r) { io.observe(r); });
  }

  function count(el) {
    if (reduce) return;
    var t0 = performance.now(), dur = 1300;
    (function f(now) {
      var t = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - t, 4);
      el.textContent = (el._to * e).toFixed(el._fmt) + el._suf;
      if (t < 1) requestAnimationFrame(f);
    })(t0);
  }
})();
