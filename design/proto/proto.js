/* Atlas Noir prototype - page logic.
 * Every number shown comes from data.json and cities/<slug>.json (written by
 * export.py from the built site and the processed tables). This file only
 * formats and places.                                                        */
(function () {
  'use strict';

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // The page always opens on the verdict. Browsers otherwise restore the old
  // scroll position (or jump to a stale #chapter) on reload, which lands the
  // reader part-way down with "room to scroll up" that makes no sense.
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
  var hadHash = !!location.hash;
  if (hadHash) history.replaceState(null, '', location.pathname + location.search);
  scrollTo(0, 0);
  // The browser applies a #fragment jump after scripts run, so undo it once
  // the page has loaded (instant, not the smooth CSS scroll).
  if (hadHash) addEventListener('load', function () {
    document.documentElement.style.scrollBehavior = 'auto';
    scrollTo(0, 0);
    requestAnimationFrame(function () { scrollTo(0, 0); document.documentElement.style.scrollBehavior = ''; });
  });

  function pct(v, d) { return (v * 100).toFixed(d || 0) + '%'; }
  function minus(s) { return String(s).replace('-', '\u2212'); }
  function title(s) { return s ? s.toLowerCase().replace(/\b\w/g, function (c) { return c.toUpperCase(); }) : '\u2014'; }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function mulberry(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; var t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

  var D, S = { h: null, tier: null }, stage, bySlug = {}, loadSeq = 0;

  fetch('data.json').then(function (r) { return r.json(); }).then(init);

  function init(data) {
    D = data;
    D.cities.forEach(function (c) { bySlug[c[0]] = c; });
    var C = D.coverage;
    $$('[data-k]').forEach(function (el) {
      var K = { event: D.event, as_of: D.as_of, n_countries: C.n_countries, floor: (C.floor / 1000) + 'k',
                umb_world: D.umbrella_world.n };
      if (el.dataset.k in K) el.textContent = K[el.dataset.k];
    });
    var counts = { covered: C.n_covered, missing_bn: C.people_missing / 1e9 };
    $$('[data-count]').forEach(function (el) {
      el._to = counts[el.dataset.count] || 0; el._fmt = +(el.dataset.fmt || 0); el._suf = el.dataset.suffix || '';
      el.textContent = (reduce ? el._to : 0).toFixed(el._fmt) + el._suf;
    });
    tips();
    stage = makeStage();
    chapters();
    palette();
    var want = new URLSearchParams(location.search).get('city');
    select(bySlug[want] ? want : D.default, true).then(reveal);
  }

  /* ── Switching city: fetch its file, then re-render every chapter ── */
  function select(slug, first) {
    var seq = ++loadSeq;
    document.body.classList.add('loading');
    return fetch('cities/' + slug + '.json').then(function (r) { return r.json(); }).then(function (c) {
      if (seq !== loadSeq) return;                 // a newer pick won the race
      render(c, first);
      document.body.classList.remove('loading');
      var q = new URLSearchParams(location.search);
      if (slug === D.default) q.delete('city'); else q.set('city', slug);
      history.replaceState(null, '', location.pathname + (q.toString() ? '?' + q : ''));
    });
  }

  function render(c, first) {
    var h = c.hero, W = D.umbrella_world, tier = tierOf(D.tiers, h.bss);
    S.h = h; S.tier = tier;
    var K = {
      name: h.name, country: h.country, n_cities: h.n_cities, lead: h.lead,
      ci: h.bss_lo == null ? 'not available' : h.bss_lo.toFixed(2) + ' \u2013 ' + h.bss_hi.toFixed(2), tier: tier[1],
      rank: h.rank, rank_range: h.rank_lo + '\u2013' + h.rank_hi,
      n: h.n.toLocaleString('en') + ' days', base_pct: pct(h.base_rate),
      station: title(h.station), station_km: h.station_km == null ? '\u2014' : h.station_km,
      span: h.first + ' \u2192 ' + h.last, umb_n: c.umbrella ? c.umbrella.n.toLocaleString('en') : '\u2014'
    };
    $$('[data-k]').forEach(function (el) { if (el.dataset.k in K) el.textContent = K[el.dataset.k]; });
    document.title = 'Atlas Noir prototype \u00b7 How good is the ' + h.name + ' weather forecast?';
    var big = $('[data-count="bss"]');
    big._to = h.bss; big._fmt = 2; big._suf = '';
    if (first || reduce) big.textContent = (first && !reduce ? 0 : h.bss).toFixed(2); else count(big);
    big.classList.toggle('neg', h.bss < 0);

    scale(D.tiers, h, tier);
    promise(h);
    umbrella(c.umbrella ? {
      alpha: W.alpha, follow: c.umbrella.follow, best: c.umbrella.best, trigger: c.umbrella.trigger,
      world_follow: W.follow
    } : null, h.name);
    if (stage) stage.setCity(h, first);
  }

  function tierOf(T, v) {
    for (var i = 0; i < T.length; i++) if (T[i][0] === null || v < T[i][0]) return T[i];
    return T[T.length - 1];
  }

  /* ── Skill scale: the site's own tiers, 0 to 0.7 ─ */
  function scale(T, h, cur) {
    var max = 0.7, track = $('#scale-track'), lo = 0;
    function x(v) { return Math.max(0, Math.min(1, v / max)) * 100; }
    $$('.scale-band', track).forEach(function (b) { b.remove(); });
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
    var hasCi = h.bss_lo != null;
    setTimeout(function () {
      track.style.setProperty('--ci-lo', x(hasCi ? h.bss_lo : h.bss) + '%');
      track.style.setProperty('--ci-w', (hasCi ? x(h.bss_hi) - x(h.bss_lo) : 0) + '%');
      track.style.setProperty('--pt', x(h.bss) + '%');
    }, reduce ? 0 : 450);
  }

  /* ── The promise: one square per real day ─────── */
  function promise(h) {
    var seg = $('.bins'), grid = $('.grid-days'), cur = -1;
    seg.textContent = '';
    h.bins.forEach(function (b, i) {
      var btn = document.createElement('button');
      btn.type = 'button'; btn.setAttribute('role', 'tab');
      btn.textContent = Math.round(b.lo * 100) + '\u2013' + Math.round(b.hi * 100) + '%';
      if (b.sig) { var w = document.createElement('span'); w.className = 'warnmark'; w.textContent = '!';
        w.setAttribute('aria-label', 'off by more than chance'); btn.appendChild(w); }
      btn.addEventListener('click', function () { show(i); });
      seg.appendChild(btn);
    });
    seg.onkeydown = function (e) {
      var d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
      if (!d) return; e.preventDefault();
      var n = (cur + d + h.bins.length) % h.bins.length; show(n); seg.children[n].focus();
    };
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
    var inp = $('#alpha'), svg = $('#umb-chart'), NS = 'http://www.w3.org/2000/svg', exBox = $('#umb-ex');
    var none = !U;
    $('#umb-none').hidden = !none;
    $$('.umb, #umb-chart, #umbrella .note').forEach(function (e) { e.hidden = none; });
    svg.textContent = ''; exBox.textContent = '';
    if (none) { $('#umb-none-name').textContent = name; return; }
    var keep = +inp.value, first = !inp._init; inp._init = true;
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
    var exLab = document.createElement('span'); exLab.className = 'mono dim'; exLab.style.fontSize = '11px';
    exLab.style.alignSelf = 'center'; exLab.textContent = 'for example'; exBox.appendChild(exLab);
    ex.forEach(function (e) {
      var b = document.createElement('button'); b.type = 'button'; b.textContent = e[0]; b.dataset.a = e[1];
      b.addEventListener('click', function () { inp.value = idx(e[1]); upd(); });
      exBox.appendChild(b);
    });
    function idx(a) { var best = 0; U.alpha.forEach(function (x, i) { if (Math.abs(x - a) < Math.abs(U.alpha[best] - a)) best = i; }); return best; }
    inp.max = U.alpha.length - 1; inp.value = first ? idx(0.05) : keep;   // a new city keeps your ratio

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
    inp.oninput = upd;
    upd();
  }

  /* ── Provenance tooltips (read the current city from S) ── */
  var tip;
  function tips() {
    tip = $('#tip');
    var big = $('[data-count="bss"]'); big.tabIndex = 0; big.dataset.tip = 'bss';
    function body(k) {
      var h = S.h;
      if (k === 'rank') return '<b>Rank ' + h.rank + ' of ' + h.n_cities + '.</b> Allowing for the uncertainty in every city\u2019s score, it could sit anywhere from ' + h.rank_lo + ' to ' + h.rank_hi + '.';
      return '<b>Brier skill score ' + h.bss.toFixed(3) + '</b> \u00b7 ' + esc(S.tier[1]) +
        '<dl><dt>range</dt><dd>' + (h.bss_lo == null ? 'not available' : h.bss_lo.toFixed(3) + ' \u2013 ' + h.bss_hi.toFixed(3) + ' (95%)') + '</dd>' +
        '<dt>days</dt><dd>' + h.n + '</dd><dt>vs</dt><dd>the city\u2019s own rain rate (' + pct(h.base_rate, 1) + ')</dd>' +
        '<dt>event</dt><dd>' + esc(D.event) + '</dd><dt>source</dt><dd>data/cities/' + esc(h.slug) + '.json</dd></dl>';
    }
    function show(e) {
      var t = e.target.closest && e.target.closest('[data-tip]'); if (!t || !S.h) return;
      tip.innerHTML = body(t.dataset.tip); tip.hidden = false;
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

  /* A small label that follows the pointer over globe markers. */
  function globeTip(m, x, y) {
    if (!m) { if (tip.dataset.globe) { tip.hidden = true; delete tip.dataset.globe; } return; }
    var c = bySlug[m.slug], t = tierOf(D.tiers, c[5]);
    tip.dataset.globe = 1;
    tip.innerHTML = '<b>' + esc(c[1]) + '</b> <span class="dim mono">' + esc(c[2]) + '</span><br>' +
      '<span class="mono">skill ' + minus(c[5].toFixed(2)) + '</span> \u00b7 ' + esc(t[1]) +
      '<br><span class="dim">click to open this city</span>';
    tip.hidden = false;
    tip.style.left = Math.min(innerWidth - tip.offsetWidth - 8, x + 16) + 'px';
    tip.style.top = Math.min(innerHeight - tip.offsetHeight - 8, y + 16) + 'px';
  }

  /* ── City palette: search, arrows, enter; opened by the pill or "/" ── */
  function palette() {
    var dlg = $('#palette'), q = $('#pal-q'), list = $('#pal-list'), items = [], act = 0;
    var tone = { bad: 'q0', ok: 'q1', good: 'q2' };
    var all = D.cities.slice().sort(function (a, b) { return a[1].localeCompare(b[1]); });
    function fold(s) { return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase(); }
    function draw() {
      var s = fold(q.value.trim());
      items = !s ? all : all.filter(function (c) { return fold(c[1]).indexOf(s) >= 0 || c[2].toLowerCase() === s; })
        .sort(function (a, b) { return (fold(b[1]).indexOf(s) === 0) - (fold(a[1]).indexOf(s) === 0); });
      act = 0; list.textContent = '';
      if (!items.length) { var li = document.createElement('li'); li.className = 'pal-empty'; li.textContent = 'No city matches \u201c' + q.value + '\u201d'; list.appendChild(li); return; }
      items.forEach(function (c, i) {
        var li = document.createElement('li'), t = tierOf(D.tiers, c[5]);
        li.id = 'pal-' + i; li.setAttribute('role', 'option'); li.className = tone[t[2]] + (S.h && c[0] === S.h.slug ? ' here' : '');
        li.innerHTML = '<span class="pal-cc mono">' + esc(c[2]) + '</span><span class="pal-name">' + esc(c[1]) + '</span>' +
          '<span class="pal-tier">' + esc(t[1]) + '</span><span class="pal-bss mono">' + minus(c[5].toFixed(2)) + '</span>';
        li.addEventListener('click', function () { pick(i); });
        li.addEventListener('pointermove', function () { if (act !== i) mark(i); });
        list.appendChild(li);
      });
      mark(0);
    }
    function mark(i) {
      if (!items.length) return;
      act = (i + items.length) % items.length;
      $$('[role=option]', list).forEach(function (li, j) { li.setAttribute('aria-selected', j === act); });
      var li = list.children[act]; q.setAttribute('aria-activedescendant', li.id);
      li.scrollIntoView({ block: 'nearest' });
    }
    function pick(i) { var c = items[i]; if (!c) return; dlg.close(); select(c[0]); }
    q.addEventListener('input', draw);
    q.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); mark(act + 1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); mark(act - 1); }
      else if (e.key === 'Enter') { e.preventDefault(); pick(act); }
    });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close(); });   // backdrop
    function open() { if (dlg.open) return; q.value = ''; draw(); dlg.showModal(); q.focus(); }
    $('.citypill').addEventListener('click', open);
    document.addEventListener('keydown', function (e) {
      var tag = document.activeElement && document.activeElement.tagName;
      if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA') { e.preventDefault(); open(); }
    });
  }

  /* ── Stage and camera script ──────────────────── */
  function makeStage() {
    if (!window.WebGL2RenderingContext) { document.body.classList.add('nogl'); return null; }
    var C = D.coverage;
    var st = new AtlasStage($('#stage'), $('#marks'), {
      elev: 'assets/relief-elev.webp', biome: 'assets/relief-biome.webp', dragTarget: $('#stage-hit'), stars: 'assets/stars.bin',
      onFail: function () { document.body.classList.add('nogl'); },
      onPick: function (m) { if (m.slug !== S.h.slug) select(m.slug); },
      onHover: globeTip
    });
    if (st.failed) { document.body.classList.add('nogl'); return null; }
    var tone = { bad: 'q0', ok: 'q1', good: 'q2' };
    var countries = C.countries.map(function (c) {
      var cls = c[4] > 0 ? 'ok' : c[5] === 'no gauge nearby' ? 'none' : 'broken';
      return { lon: c[2], lat: c[1], r: Math.min(22, 2.5 + Math.sqrt(c[3] / 1e6) * 1.1), cls: cls, title: c[0] + ' \u00b7 ' + (cls === 'ok' ? 'can be checked' : c[5]) };
    });
    st.shots = function () {
      var h = S.h, m = innerWidth <= 860;
      return {
        // First view: small enough that the whole disc, south pole and
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
      st.setMarkers(name === 'world' ? countries : st.cityMarks);
      st.fly(st.shots()[name], instant);
    };
    // A new city: rebuild the city markers around it and fly to it (the
    // shot for the current chapter is recomputed from the new coordinates).
    st.setCity = function (h, first) {
      st.cityMarks = D.cities.filter(function (c) { return c[0] !== h.slug; }).map(function (c) {
        return { slug: c[0], lon: c[4], lat: c[3], r: 2.8, cls: 'city ' + tone[tierOf(D.tiers, c[5])[2]] };
      }).concat([{ lon: h.lon, lat: h.lat, r: 6, cls: 'here', pulse: true, label: h.name }]);
      st.go(first ? 'city' : st.current || 'city', !!first);
    };
    addEventListener('resize', function () { if (S.h) st.go(st.current, true); });
    return st;
  }

  function chapters() {
    var links = {}; $$('.chapters a').forEach(function (a) { links[a.dataset.ch] = a; });
    // Chapter links scroll without writing #chapter into the address, so a
    // reload never reopens part-way down the page.
    $$('.chapters a, .brand').forEach(function (a) {
      a.addEventListener('click', function (e) {
        var t = document.getElementById(a.getAttribute('href').slice(1)); if (!t) return;
        e.preventDefault(); t.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth' });
      });
    });
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        var id = e.target.id;
        Object.keys(links).forEach(function (k) { if (k === id) links[k].setAttribute('aria-current', 'true'); else links[k].removeAttribute('aria-current'); });
        if (stage && S.h && stage.current !== e.target.dataset.stage) stage.go(e.target.dataset.stage);
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
    if (reduce) { el.textContent = el._to.toFixed(el._fmt) + el._suf; return; }
    var t0 = performance.now(), dur = 1300, from = parseFloat(el.textContent.replace('\u2212', '-')) || 0;
    (function f(now) {
      var t = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - t, 4);
      el.textContent = minus((from + (el._to - from) * e).toFixed(el._fmt)) + el._suf;
      if (t < 1) requestAnimationFrame(f);
    })(t0);
  }
})();
