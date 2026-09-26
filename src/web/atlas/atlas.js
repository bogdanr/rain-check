/* Atlas Noir - page logic.
 * Every number shown comes from data.json and data/cities/<slug>.json (written
 * by src/atlas.py from the processed tables). This file only formats and
 * places. Asset URLs and the page's own city arrive in <script id="cfg">.   */
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
  // base: the site root ("/rain-check/"); data: hashed data.json; city: the
  // city this page was built for; elev/biome/stars: stage assets.
  var CFG = JSON.parse(document.getElementById('cfg').textContent);

  fetch(CFG.data).then(function (r) { return r.json(); }).then(init);

  // Each city has a real page, so a switch updates the address to it and a
  // shared or reloaded link opens the same city.
  function cityPath(slug) { return CFG.base + (slug === D.default ? '' : 'city/' + slug + '/'); }

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
    skyInit();
    chapters();
    palette();
    evidence();
    // ?city= is the prototype's old link form; honour it, the page's own city otherwise.
    var want = new URLSearchParams(location.search).get('city');
    select(bySlug[want] ? want : bySlug[CFG.city] ? CFG.city : D.default, true).then(reveal);
  }

  /* ── Switching city: fetch its file, then re-render every chapter ── */
  function select(slug, first) {
    var seq = ++loadSeq;
    document.body.classList.add('loading');
    return fetch(CFG.base + 'data/cities/' + slug + '.' + D.hashes[slug] + '.json').then(function (r) { return r.json(); }).then(function (c) {
      if (seq !== loadSeq) return;                 // a newer pick won the race
      render(c, first);
      document.body.classList.remove('loading');
      var q = new URLSearchParams(location.search);
      q.delete('city');
      history.replaceState(null, '', cityPath(slug) + (q.toString() ? '?' + q : ''));
    });
  }

  function render(c, first) {
    var h = c.hero, W = D.umbrella_world, tier = tierOf(D.tiers, h.bss);
    S.h = h; S.tier = tier;
    var K = {
      name: h.name, country: h.country, n_cities: h.n_cities, lead: h.lead,
      ci: h.bss_lo == null ? 'not available' : minus(h.bss_lo.toFixed(2)) + ' \u2013 ' + minus(h.bss_hi.toFixed(2)), tier: tier[1],
      grade: GRADE[D.tiers.indexOf(tier)], say: SAY[D.tiers.indexOf(tier)],
      rank: h.rank, rank_range: h.rank_lo + '\u2013' + h.rank_hi,
      n: h.n.toLocaleString('en') + ' days', base_pct: pct(h.base_rate),
      station: title(h.station), station_km: h.station_km == null ? '\u2014' : h.station_km,
      span: h.first + ' \u2192 ' + h.last, umb_n: c.umbrella ? c.umbrella.n.toLocaleString('en') : '\u2014'
    };
    $$('[data-k]').forEach(function (el) { if (el.dataset.k in K) el.textContent = K[el.dataset.k]; });
    document.title = 'How good is the ' + h.name + ' weather forecast? \u00b7 rain check';
    var big = $('[data-count="bss"]');
    big._to = h.bss; big._fmt = 2; big._suf = '';
    if (first || reduce) big.textContent = (first && !reduce ? 0 : h.bss).toFixed(2); else count(big);
    big.classList.toggle('neg', h.bss < 0);
    // The whole card takes the colour of its grade (t0 = fails ... t4 = excellent).
    var ti = D.tiers.indexOf(tier), card = $('.hero-card');
    card.className = card.className.replace(/\bt\d\b/g, '').trim() + ' t' + ti;
    $('#grade').setAttribute('aria-label', 'Grade: ' + GRADE[ti] + ', ' + tier[1]);

    scale(D.tiers, h, tier);
    promise(h);
    umbrella(c.umbrella ? {
      alpha: W.alpha, save: c.umbrella.save, world_save: W.save, n: c.umbrella.n, rainy: c.umbrella.rainy,
      acted: c.umbrella.acted, missed: c.umbrella.missed
    } : null, h.name);
    if (stage) stage.setCity(h, first);
    cityDetail();
    if (evidence.redraw) evidence.redraw();
    live(h);
  }

  // One plain word per site tier, worst to best: the site's labels stay as
  // the precise wording beside it.
  var GRADE = ['Fails', 'Poor', 'Fair', 'Good', 'Excellent'];
  // What each grade means for someone reading the forecast. "The usual" is
  // the city's own long-run rain rate, the yardstick the skill score uses.
  var SAY = [
    'Worse than just quoting the usual chance of rain. Don\u2019t rely on it.',
    'Only slightly better than quoting the usual chance of rain.',
    'Clearly better than the usual chance of rain, but often off.',
    'Reliable enough to plan around on most days.',
    'Reliable. One of the strongest forecasts in this audit.'
  ];

  function tierOf(T, v) {
    for (var i = 0; i < T.length; i++) if (T[i][0] === null || v < T[i][0]) return T[i];
    return T[T.length - 1];
  }

  /* ── Skill scale: the site's own tiers, from below 0 up to 0.7 ─ */
  function scale(T, h, cur) {
    // The "fails" band gets its own stretch left of 0, so a negative score
    // lands visibly in the red instead of being pinned to the edge.
    var min = -0.2, max = 0.7, track = $('#scale-track'), lo = min;
    function x(v) { return Math.max(0, Math.min(1, (v - min) / (max - min))) * 100; }
    $$('.scale-band', track).forEach(function (b) { b.remove(); });
    T.forEach(function (t, i) {
      var hi = t[0] === null ? max : t[0];
      var b = document.createElement('span');
      b.className = 'scale-band t' + i + (t === cur ? ' cur' : '');
      b.style.left = x(lo) + '%'; b.style.width = 'calc(' + (x(hi) - x(lo)) + '% - 2px)';
      b.title = GRADE[i] + ' \u00b7 ' + t[1];
      var em = document.createElement('em'); em.textContent = i === 0 ? 'below 0' : String(+lo.toFixed(2));
      var g = document.createElement('strong'); g.textContent = GRADE[i];
      b.appendChild(em); b.appendChild(g); track.insertBefore(b, track.firstChild); lo = hi;
    });
    track.style.height = '52px';
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
  // Everything is counted on the city's real days, in one currency: one
  // protection. Getting caught costs 1/a of those (the slider shows 1/a).
  // A way of deciding costs days protected + soakings / a. The score is the
  // share of bother following the forecast saves against the best habit
  // without it (export.py derives it from the value curve and checks it
  // against these same day counts).
  function umbrella(U, name) {
    var inp = $('#alpha'), svg = $('#umb-chart'), NS = 'http://www.w3.org/2000/svg', exBox = $('#umb-ex');
    var none = !U;
    $('#umb-none').hidden = !none;
    $$('.umb, .umb-chart-head, #umb-chart, #umbrella .note').forEach(function (e) { e.hidden = none; });
    svg.textContent = ''; exBox.textContent = '';
    if (none) { $('#umb-none-name').textContent = name; return; }
    // Only the range people actually live in: getting caught at most 20x worse
    // than protecting (C/L >= 0.05). Beyond that nobody reasons in "times".
    var s0 = 0; while (s0 < U.alpha.length - 1 && U.alpha[s0] < 0.05 - 1e-9) s0++;
    U = Object.keys(U).reduce(function (o, k) { o[k] = Array.isArray(U[k]) ? U[k].slice(s0) : U[k]; return o; }, {});
    var keep = inp._a, first = !inp._init; inp._init = true;
    // The slider only stops where getting wet is a whole number of times
    // worse (20x, 17x, ... 2x, 1x): one grid point per whole ratio, the one
    // whose 1/a lies closest to it, so the label never reads 4.7x.
    var stops = [], best = {};
    U.alpha.forEach(function (x, i) { var k = Math.round(1 / x), d = Math.abs(1 / x - k);
      if (!(k in best) || d < best[k][1]) best[k] = [i, d]; });
    Object.keys(best).forEach(function (k) { stops.push(best[k][0]); });
    stops.sort(function (p, q) { return p - q; });
    var W = 860, H = 168, L = 44, R = 12, T = 26, B = 26;   // T leaves a row for the legend
    var all = U.save.concat(U.world_save).filter(function (v) { return v != null; });
    var ymax = Math.max(0.25, Math.ceil(Math.max.apply(null, all) * 4) / 4);
    var ymin = Math.max(-1, Math.min(-0.25, Math.floor(Math.min.apply(null, all) * 4) / 4));
    // Log axis: equal room for 20x-10x-5x and for 2x-1.5x-1x, which a linear
    // axis would squeeze into opposite ends.
    var LA0 = Math.log(U.alpha[0]), LA1 = Math.log(U.alpha[U.alpha.length - 1]);
    function X(a) { return L + (Math.log(a) - LA0) / (LA1 - LA0) * (W - L - R); }
    function Y(v) { return T + (ymax - Math.max(ymin, Math.min(ymax, v))) / (ymax - ymin) * (H - T - B); }
    function el(n, a, txt) { var e = document.createElementNS(NS, n); for (var k in a) e.setAttribute(k, a[k]);
      if (txt != null) e.textContent = txt; svg.appendChild(e); return e; }
    function path(arr) { return arr.map(function (v, i) { return (i ? 'L' : 'M') + X(U.alpha[i]).toFixed(1) + ',' + Y(v).toFixed(1); }).join(''); }
    el('rect', { class: 'neg-zone', x: L, y: Y(0), width: W - L - R, height: Y(ymin) - Y(0) });
    var ticks = []; for (var tv = ymin; tv <= ymax + 1e-9; tv += 0.25) ticks.push(+tv.toFixed(2));
    ticks.forEach(function (v) {
      el('line', { class: v === 0 ? 'zero' : 'ax', x1: L, x2: W - R, y1: Y(v), y2: Y(v) });
      el('text', { x: L - 8, y: Y(v) + 4, 'text-anchor': 'end' }, minus(Math.round(v * 100)) + '%'); });
    // x axis in the slider's own words: how many times worse getting caught is
    [[0.05, '20\u00d7'], [0.1, '10\u00d7'], [0.2, '5\u00d7'], [0.25, '4\u00d7'], [1 / 3, '3\u00d7'], [0.5, '2\u00d7'], [2 / 3, '1.5\u00d7'], [0.99, '1\u00d7']].forEach(function (t) {
      el('text', { x: X(t[0]), y: H - 6, 'text-anchor': t[0] > .9 ? 'end' : t[0] < .051 ? 'start' : 'middle' }, t[1]); });
    el('text', { x: L, y: H + 10 }, '\u2190 getting wet is much worse');
    el('text', { x: W - R, y: H + 10, 'text-anchor': 'end' }, 'protecting is almost as bad \u2192');
    el('text', { x: L + 8, y: Y(ymin) - 6 }, 'below 0%: worse than not checking');
    el('path', { class: 'l-world', d: path(U.world_save) });
    el('path', { class: 'l-follow', d: path(U.save) });
    var cur = el('line', { class: 'cursor', y1: T, y2: H - B });
    var dot = el('circle', { class: 'dot', r: 6 });
    var lg = el('g', { class: 'lg', transform: 'translate(' + L + ',12)' });
    [['l-follow', 'following the forecast (' + name + ')'], ['l-world', 'typical city (world median)']]
      .forEach(function (r, i) { var g = document.createElementNS(NS, 'g'); g.setAttribute('transform', 'translate(' + [0, 290][i] + ',0)');
        var ln = document.createElementNS(NS, 'line'); ln.setAttribute('class', r[0]); ln.setAttribute('x2', 22); ln.setAttribute('y1', -4); ln.setAttribute('y2', -4);
        var tx = document.createElementNS(NS, 'text'); tx.setAttribute('x', 30); tx.textContent = r[1];
        g.appendChild(ln); g.appendChild(tx); lg.appendChild(g); });

    // Everyday examples, each named by its trade-off so the number means something.
    var ex = [['Take a jacket', '2\u00d7', 0.5], ['\u2602\ufe0e Umbrella', '3\u00d7', 0.33], ['Picnic indoors', '4\u00d7', 0.25]];
    ex.forEach(function (e) {
      var b = document.createElement('button'); b.type = 'button'; b.dataset.a = e[2];
      b.innerHTML = esc(e[0]) + ' <span>' + esc(e[1]) + '</span>';
      b.addEventListener('click', function () { inp.value = pos(e[2]); upd(); });
      exBox.appendChild(b);
    });
    // Slider position of the stop nearest a cost ratio a.
    function pos(a) { var b = 0; stops.forEach(function (s, j) { if (Math.abs(U.alpha[s] - a) < Math.abs(U.alpha[stops[b]] - a)) b = j; }); return b; }
    inp.max = stops.length - 1; inp.value = pos(first || keep == null ? 0.25 : keep);   // a new city keeps your setting

    function times(x) { return Math.round(1 / x) + '\u00d7'; }
    function n0(v) { return Math.round(v).toLocaleString('en'); }
    // Plain verdict: only the sign is judged (0 = the best no-forecast habit);
    // the percentage carries the size, so no invented bands.
    function verdict(s) {
      return s < 0 ? ['t0', 'Following the forecast leaves you worse off']
                   : ['t4', 'Following the forecast helps'];
    }
    function cost(acted, missed, a) { return acted + missed / a; }
    function upd() {
      var i = stops[+inp.value], a = U.alpha[i], f = U.save[i];
      inp._a = a;
      inp.style.setProperty('--p', (+inp.value / (stops.length - 1) * 100) + '%');
      $('#umb-ratio').textContent = times(a);
      $$('button', exBox).forEach(function (x) { x.setAttribute('aria-pressed', Math.abs(+x.dataset.a - a) < 0.006); });
      // The best fixed habit without a forecast: always protect if that is cheaper, else never.
      var always = a * U.n <= U.rainy;
      var v = verdict(f), r = times(a);
      // The textbook rule the slider sets: protect when the chance is at least a.
      $('#umb-rule').innerHTML = 'Your rule: <b>protect when the forecast says ' + pct(a) + ' or more</b>' +
        (1 / a >= 2 ? ' <span class="dim">(' + (Math.abs(1 / a - Math.round(1 / a)) < 0.05 ? 'a' : 'about a') + ' 1-in-' +
          Math.round(1 / a) + ' chance is worth covering)</span>' : '');
      var cF = cost(U.acted[i], U.missed[i], a), cH = always ? U.n : U.rainy / a;
      $('#umb-say').className = 'umb-verdict ' + v[0];
      $('#umb-say').innerHTML = '<b>' + v[1] + '</b><span>' + (f < 0
        ? 'It costs you <em>' + pct(-f) + '</em> more bother than not checking at all.'
        : 'It saves you <em>' + pct(f) + '</em> of the bother of not checking at all.') + '</span>';
      function row(lab, what, c, cls) {
        return '<span class="dl' + (cls ? ' ' + cls : '') + '">' + lab + '</span><span>' + what +
          '</span><span class="umb-cost' + (cls ? ' ' + cls : '') + '">' + n0(c) + '</span>';
      }
      var d = '<span class="umb-days-head">' + U.n.toLocaleString('en') + ' days, ' + n0(U.rainy) + ' of them rainy</span>' +
        '<span class="umb-days-head umb-cost" title="protections + soakings \u00d7 ' + esc(r) + '">total bother</span>' +
        row('Follow it', '<b>' + n0(U.acted[i]) + '</b> protected \u00b7 <b>' + n0(U.missed[i]) + '</b> soaked', cF, f < 0 ? 'worse' : 'you') +
        row('Don\u2019t check', always ? 'protect every day' : 'never protect \u00b7 <b>' + n0(U.rainy) + '</b> soaked', cH);
      d += '<span class="umb-days-note">Bother: each protection counts 1, each soaking ' + r + '. Without a forecast the ' +
        'cheaper habit wins: ' + (always ? 'always protect.' : 'never protect.') + '</span>';
      $('#umb-days').innerHTML = d;
      inp.setAttribute('aria-valuetext', 'getting caught is ' + times(a) + ' as bad; ' + v[1]);
      cur.setAttribute('x1', X(a)); cur.setAttribute('x2', X(a));
      dot.setAttribute('cx', X(a)); dot.setAttribute('cy', Y(f));
    }
    inp.oninput = upd;
    upd();
  }

  /* ── Evidence: claims, league table, methods, glossary ── */
  var ST = { held: 'held', reversed: 'reversed', flipped: 'flipped', weakened: 'weakened',
             unresolved: 'unresolved', 'new': 'new finding', failed: 'did not replicate', limit: 'limit' };
  function stClass(s) { return s === 'flipped' ? 'reversed' : s === 'failed' ? 'reversed' : s; }
  function evidence() {
    var E = D.evidence;
    // Claims: one card each; the challenges fold open under a summary strip.
    var box = $('#claims'), tot = {};
    // Two groups, so a single-city study never reads as a result for the selected city.
    var GROUPS = [['all', 'Across all cities', 'The same whichever city you pick.'],
                  ['case', 'Case study', 'Tests that need extra data (other rain sources, other gauges), run for one city only.']];
    var gbox = {};
    GROUPS.forEach(function (g) {
      if (!E.claims.some(function (c) { return (c.group || 'all') === g[0]; })) return;
      var h = document.createElement('h4'); h.className = 'cl-group';
      h.innerHTML = esc(g[1]) + ' <span class="dim">' + esc(g[2]) + '</span>';
      var wrap = document.createElement('div'); wrap.className = 'claims-g';
      box.appendChild(h); box.appendChild(wrap); gbox[g[0]] = wrap;
    });
    var first = true;
    E.claims.slice().sort(function (a, b) { return (a.group === 'case') - (b.group === 'case'); }).forEach(function (c) {
      var n = {}; c.challenges.forEach(function (x) { n[x.status] = (n[x.status] || 0) + 1; tot[x.status] = (tot[x.status] || 0) + 1; });
      var d = document.createElement('details'); d.className = 'claim'; if (first) { d.open = true; first = false; }
      var dots = c.challenges.map(function (x) { return '<i class="st ' + stClass(x.status) + '" title="' + esc(x.what + ': ' + ST[x.status]) + '"></i>'; }).join('');
      var tally = Object.keys(n).map(function (k) { return n[k] + ' ' + ST[k]; }).join(' \u00b7 ');
      // Intervals of all 'sig' challenges share one axis per claim, with 0 marked.
      var sigs = c.challenges.filter(function (x) { return x.sig; }), lo = 0, hi = 0;
      sigs.forEach(function (x) { lo = Math.min(lo, x.sig.lo); hi = Math.max(hi, x.sig.hi); });
      var pad = (hi - lo) * 0.08 || 0.01; lo -= pad; hi += pad;
      function X(v) { return ((v - lo) / (hi - lo) * 100).toFixed(2) + '%'; }
      var rows = c.challenges.map(function (x) {
        var right = x.sig
          ? '<span class="ci-bar" style="--z:' + X(0) + ';--a:' + X(x.sig.lo) + ';--b:' + X(x.sig.hi) + ';--e:' + X(x.sig.est) + '"><i></i><b></b></span>' +
            '<span class="ci-num mono tnum">' + minus(x.sig.est.toFixed(4)) + ' <span class="dim">[' + minus(x.sig.lo.toFixed(4)) + ', ' + minus(x.sig.hi.toFixed(4)) + ']' +
            ' p ' + (x.sig.p <= 0.001 ? '\u2264 0.001' : x.sig.p.toFixed(3)) + '</span></span>'
          : '';
        return '<li class="' + stClass(x.status) + '"><span class="ch-st mono"><i class="st ' + stClass(x.status) + '"></i>' + ST[x.status] + '</span>' +
          '<span class="ch-what">' + esc(x.what) + (x.detail ? '<small>' + esc(x.detail) + '</small>' : '') + '</span>' +
          '<span class="ch-sig">' + right + '</span></li>';
      }).join('');
      d.innerHTML = '<summary><span class="cl-scope mono">' + esc(c.scope) + '</span>' +
        '<span class="cl-text">' + esc(c.claim) + '</span>' +
        '<span class="cl-dots" aria-label="' + esc(tally) + '">' + dots + '</span>' +
        '<span class="cl-tally mono">' + esc(tally) + '</span><span class="cl-chev" aria-hidden="true"></span></summary>' +
        (c.case_city ? '<p class="cl-case" data-case="' + esc(c.case_city) + '" hidden></p>' : '') +
        (c.stat ? '<p class="cl-stat mono">' + esc(c.stat) + ' \u00b7 95% interval, bar axis includes 0</p>' : '') +
        '<ul class="ch">' + rows + '</ul>' +
        '<p class="cl-src mono">source <code>' + esc(c.source) + '</code></p>';
      gbox[c.group || 'all'].appendChild(d);
    });
    var nch = E.claims.reduce(function (s, c) { return s + c.challenges.length; }, 0);
    $('#ev-claims-sum').textContent = E.claims.length + ' claims \u00b7 ' + nch + ' challenges \u00b7 ' +
      (tot.held || 0) + ' held, ' + ((tot.reversed || 0) + (tot.flipped || 0) + (tot.failed || 0)) + ' reversed or failed, ' +
      (tot.weakened || 0) + ' weakened. The failures are shown, not hidden.';

    // League table.
    var C = E.league_cols, ci = {}; C.forEach(function (k, i) { ci[k] = i; });
    var COLS = [
      ['rank', '#', 'num'], ['name', 'City', ''], ['bss', 'Skill', 'num'], ['ci', '95% range', 'ci'],
      ['rank_lo', 'Could rank', 'num'], ['n', 'Days', 'num'], ['ess', 'Indep. days', 'num'],
      ['base_rate', 'Rain rate', 'num'], ['reliability', 'Reliability', 'num'], ['resolution', 'Resolution', 'num'], ['gauge_km', 'Gauge km', 'num']
    ];
    var TIPS = { ess: 'effective independent days, after allowing for weather persisting', reliability: 'calibration error, lower is better',
      resolution: 'how much the forecast separates wet from dry days, higher is better', rank_lo: 'rank range across joint resamples' };
    var rows = E.league.map(function (r, i) { var o = { rank: i + 1 }; C.forEach(function (k) { o[k] = r[ci[k]]; }); return o; });
    var sortK = 'rank', dir = 1, q = '';
    var thead = $('#lg thead'), tbody = $('#lg tbody');
    thead.innerHTML = '<tr>' + COLS.map(function (c) {
      return '<th class="' + c[2] + '" scope="col"' + (TIPS[c[0]] ? ' title="' + esc(TIPS[c[0]]) + '"' : '') + '>' +
        (c[0] === 'ci' ? c[1] : '<button type="button" data-sort="' + c[0] + '">' + c[1] + '<i></i></button>') + '</th>';
    }).join('') + '</tr>';
    $$('button', thead).forEach(function (b) { b.addEventListener('click', function () {
      var k = b.dataset.sort; if (sortK === k) dir = -dir; else { sortK = k; dir = (k === 'name' || k === 'rank' || k === 'rank_lo' || k === 'reliability' || k === 'gauge_km') ? 1 : -1; }
      draw(); }); });
    var MIN = -0.2, MAX = 0.8;
    function x(v) { return (Math.max(0, Math.min(1, (v - MIN) / (MAX - MIN))) * 100).toFixed(1) + '%'; }
    function fold(s) { return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase(); }
    function draw() {
      var s = fold(q);
      var list = rows.filter(function (r) { return !s || fold(r.name).indexOf(s) >= 0 || r.cc.toLowerCase() === s; });
      list.sort(function (a, b) { var A = a[sortK], B = b[sortK];
        return (typeof A === 'string' ? A.localeCompare(B) : (A == null) - (B == null) || A - B) * dir; });
      $$('button', thead).forEach(function (b) { var th = b.parentNode;
        if (b.dataset.sort === sortK) th.setAttribute('aria-sort', dir > 0 ? 'ascending' : 'descending'); else th.removeAttribute('aria-sort'); });
      var here = S.h && S.h.slug;
      tbody.innerHTML = list.map(function (r) {
        var t = D.tiers.indexOf(tierOf(D.tiers, r.bss));
        return '<tr class="t' + t + (r.slug === here ? ' here' : '') + '" data-slug="' + r.slug + '" tabindex="0">' +
          '<td class="num mono">' + r.rank + '</td>' +
          '<td><span class="lg-cc mono">' + esc(r.cc) + '</span><a href="' + esc(cityPath(r.slug)) + '">' + esc(r.name) + '</a></td>' +
          '<td class="num mono lg-bss">' + minus(r.bss.toFixed(2)) + '</td>' +
          '<td class="ci"><span class="lg-ci" style="--z:' + x(0) + ';--a:' + x(r.bss_lo) + ';--b:' + x(r.bss_hi) + ';--e:' + x(r.bss) + '"><i></i><b></b></span></td>' +
          '<td class="num mono">' + r.rank_lo + '\u2013' + r.rank_hi + '</td>' +
          '<td class="num mono">' + r.n + '</td><td class="num mono">' + r.ess + '</td>' +
          '<td class="num mono">' + pct(r.base_rate) + '</td>' +
          '<td class="num mono">' + r.reliability.toFixed(4) + '</td><td class="num mono">' + r.resolution.toFixed(4) + '</td>' +
          '<td class="num mono">' + (r.gauge_km == null ? '\u2014' : r.gauge_km.toFixed(1)) + '</td></tr>';
      }).join('');
      $('#lg-n').textContent = list.length + ' of ' + rows.length + ' cities \u00b7 sorted by ' +
        (COLS.filter(function (c) { return c[0] === sortK; })[0] || [0, sortK])[1].toLowerCase() + ' \u00b7 source cities_metrics \u00b7 event: ' + D.event;
    }
    // Case-study note follows the selected city.
    function caseNote() {
      $$('.cl-case', box).forEach(function (p) {
        var other = S.h && S.h.name !== p.dataset.case;
        p.hidden = !other;
        if (other) p.innerHTML = 'This test was run for <b>' + esc(p.dataset.case) + '</b> only, not for ' + esc(S.h.name) +
          '. ' + esc(S.h.name) + '\u2019s own score and range are in the verdict card and the league table below.';
      });
    }
    evidence.redraw = function () { draw(); caseNote(); };
    caseNote();
    function open(tr) { if (tr && tr.dataset.slug && tr.dataset.slug !== S.h.slug) { select(tr.dataset.slug); scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' }); } }
    tbody.addEventListener('click', function (e) {
      // A modified click on a name opens that city's page in a new tab as usual.
      var a = e.target.closest('a');
      if (a && (e.metaKey || e.ctrlKey || e.shiftKey || e.button)) return;
      if (a) e.preventDefault();
      open(e.target.closest('tr'));
    });
    tbody.addEventListener('keydown', function (e) { if (e.key === 'Enter') open(e.target.closest('tr')); });
    $('#lg-q').addEventListener('input', function (e) { q = e.target.value.trim(); draw(); });
    $('#lg-csv').addEventListener('click', function () {
      var csv = C.join(',') + '\n' + E.league.map(function (r) { return r.map(function (v) {
        return v == null ? '' : typeof v === 'string' && /[",]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }).join(','); }).join('\n');
      var a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
      a.download = 'rain-check-league-' + D.as_of + '.csv'; a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    });
    draw();

    // Methods and glossary.
    $('#methods').innerHTML = E.methods.map(function (m) { return '<div><dt>' + esc(m[0]) + '</dt><dd>' + esc(m[1]) + '</dd></div>'; }).join('');
    var gl = $('#gloss');
    gl.innerHTML = E.glossary.map(function (g) {
      return '<details class="g" id="g-' + esc(g[0]) + '"><summary><b>' + esc(g[1]) + '</b>' + (g[2] && g[2] !== g[1] ? ' <span class="dim">' + esc(g[2]) + '</span>' : '') +
        '</summary><p>' + esc(g[3]) + '</p><p class="g-care"><span class="mono">careful</span> ' + esc(g[4]) + '</p></details>';
    }).join('');
    $('#gl-q').addEventListener('input', function (e) {
      var s = fold(e.target.value.trim());
      $$('.g', gl).forEach(function (d, i) { var g = E.glossary[i];
        var hit = !s || fold(g[1] + ' ' + g[2] + ' ' + g[3]).indexOf(s) >= 0;
        d.hidden = !hit; d.open = !!s && hit; });
    });
    $$('.ev-tabs a').forEach(function (a) { a.addEventListener('click', function (e) {
      e.preventDefault(); var t = $(a.getAttribute('href')); t.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' }); }); });
  }

  /* ── Provenance tooltips (read the current city from S) ── */
  var tip;
  function tips() {
    tip = $('#tip');
    var big = $('[data-count="bss"]'); big.tabIndex = 0; big.dataset.tip = 'bss';
    function body(k) {
      var h = S.h;
      if (k === 'rank') return '<b>Rank ' + h.rank + ' of ' + h.n_cities + '.</b> Allowing for the uncertainty in every city\u2019s score, it could sit anywhere from ' + h.rank_lo + ' to ' + h.rank_hi + '.';
      return '<b>Brier skill score ' + minus(h.bss.toFixed(3)) + '</b> \u00b7 ' + GRADE[D.tiers.indexOf(S.tier)] + ', ' + esc(S.tier[1]) +
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
    function hide() { tip.hidden = true; tip.classList.remove('tip-text'); }
    /* Plain-text tooltips (the sky legend's sources): the page's own tip,
     * so they can be styled, which the browser's title tooltip cannot. */
    function showText(t) {
      tip.textContent = t.dataset.tt; tip.classList.add('tip-text'); tip.hidden = false;
      var r = t.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
      tip.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left)) + 'px';
      tip.style.top = (r.top - h - 8 >= 8 ? r.top - h - 8 : r.bottom + 8) + 'px';
    }
    document.addEventListener('pointerover', function (e) {
      var tt = e.target.closest && e.target.closest('[data-tt]');
      if (tt) showText(tt); else if (e.target.closest('[data-tip]')) show(e);
    });
    document.addEventListener('pointerout', function (e) { if (e.target.closest('[data-tip],[data-tt]')) hide(); });
    document.addEventListener('focusin', show); document.addEventListener('focusout', hide);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hide(); });
  }

  /* A small label that follows the pointer over globe markers. */
  function globeTip(m, x, y) {
    if (!m) { if (tip.dataset.globe) { tip.hidden = true; delete tip.dataset.globe; } return; }
    var c = bySlug[m.slug], t = tierOf(D.tiers, c[5]);
    tip.dataset.globe = 1;
    tip.innerHTML = '<b>' + esc(c[1]) + '</b> <span class="dim mono">' + esc(c[2]) + '</span><br>' +
      '<span class="mono">skill ' + minus(c[5].toFixed(2)) + '</span> \u00b7 <b class="tc t' + D.tiers.indexOf(t) + '">' + GRADE[D.tiers.indexOf(t)] + '</b> ' + esc(t[1]) +
      '<br><span class="dim">click to open this city</span>';
    tip.hidden = false;
    tip.style.left = Math.min(innerWidth - tip.offsetWidth - 8, x + 16) + 'px';
    tip.style.top = Math.min(innerHeight - tip.offsetHeight - 8, y + 16) + 'px';
  }

  /* ── City palette: search, arrows, enter; opened by the pill or "/" ── */
  function palette() {
    var dlg = $('#palette'), q = $('#pal-q'), list = $('#pal-list'), items = [], act = 0;
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
        li.id = 'pal-' + i; li.setAttribute('role', 'option'); li.className = 't' + D.tiers.indexOf(t) + (S.h && c[0] === S.h.slug ? ' here' : '');
        li.innerHTML = '<span class="pal-cc mono">' + esc(c[2]) + '</span><span class="pal-name">' + esc(c[1]) + '</span>' +
          '<span class="pal-tier">' + GRADE[D.tiers.indexOf(t)] + ' \u00b7 ' + esc(t[1]) + '</span><span class="pal-bss mono">' + minus(c[5].toFixed(2)) + '</span>';
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
      elev: CFG.elev, biome: CFG.biome, dragTarget: $('#stage-hit'), stars: CFG.stars,
      moon: CFG.moon, lights: CFG.lights, onTier: function () { skyLegend(); },
      onFail: function () { document.body.classList.add('nogl'); skyLegend(); },
      onPick: function (m) { if (m.slug !== S.h.slug) select(m.slug); },
      onHover: globeTip
    });
    if (st.failed) { document.body.classList.add('nogl'); return null; }
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
        return { slug: c[0], lon: c[4], lat: c[3], r: 2.8, cls: 'city t' + D.tiers.indexOf(tierOf(D.tiers, c[5])) };
      }).concat([{ lon: h.lon, lat: h.lat, r: 6, cls: 'here', pulse: true, label: h.name }]);
      st.setCityDir(AtlasSky.vec(h.lon, h.lat));
      st.go(first ? 'city' : st.current || 'city', !!first);
    };
    addEventListener('resize', function () { if (S.h) st.go(st.current, true); });
    return st;
  }

  /* ── The live sky: real Sun, Moon, stars and satellite weather ──
   *
   * The clock is the reader's, or ?now=<ISO time> (fixed) for review and
   * tests. The switch is remembered; ?sky=off starts it off. */
  var NOWQ = new URLSearchParams(location.search).get('now');
  function now() { return NOWQ ? Date.parse(NOWQ) : Date.now(); }
  var SKY = { on: true, wx: null, err: null, loading: false, at: 0 };
  var DEG = Math.PI / 180;

  /* A performance mark, once per name: rc:clouds, rc:rain, rc:bolts, ... */
  function pmark(n) {
    try { if (!performance.getEntriesByName('rc:' + n).length) performance.mark('rc:' + n); } catch (e) { /* no User Timing */ }
  }

  /* A light session: data saver, a slow link, or a small memory. It gets
   * the newest lightning frame alone and no city-detail frame; the clouds
   * and rain are the same. ?lite=1|0 forces it either way. */
  var LITE = (function () {
    var q = new URLSearchParams(location.search).get('lite');
    if (q) return q === '1';
    var c = navigator.connection || {};
    return !!(c.saveData || /(^|-)2g$|^3g$/.test(c.effectiveType || '') || (navigator.deviceMemory && navigator.deviceMemory <= 2));
  })();

  function skyInit() {
    // The sky's downloads and pixel passes run in a worker (the same script).
    AtlasSky.init({ worker: CFG.skyJs });
    document.documentElement.dataset.skyMode = AtlasSky.mode;
    var q = new URLSearchParams(location.search).get('sky'), saved = null;
    try { saved = localStorage.getItem('rc-sky'); } catch (e) { /* private mode */ }
    SKY.on = q === 'off' ? false : q ? true : saved !== 'off';
    $('#sky-toggle').addEventListener('click', function () {
      SKY.on = !SKY.on;
      try { localStorage.setItem('rc-sky', SKY.on ? 'on' : 'off'); } catch (e) { /* private mode */ }
      skyApply();
    });
    if (stage && window.AtlasBolts) SKY.bolt = new AtlasBolts($('#bolts'), stage);
    if (stage && window.AtlasSats && CFG.sats) SKY.sat = new AtlasSats($('#sats'), stage, { hit: $('#stage-hit'), tip: $('#tip'), now: now });
    if (stage) skyLayers();
    skyApply();
    // The Sun and Moon move a pixel every few minutes; the satellites publish
    // every 30 min (rain), 3 h (cloud) and 5 min (lightning). A hidden tab
    // does none of it.
    if (!NOWQ) setInterval(function () { if (!document.hidden) skyTick(); }, 60000);
    if (!NOWQ) setInterval(function () { if (!document.hidden && SKY.on) weather(true); }, 10 * 60000);
    if (!NOWQ) setInterval(function () { if (!document.hidden && SKY.on && SKY.bolts) lightning(true); }, 5 * 60000);
  }

  /* Weather layers: clouds, rain and lightning, each switchable and
   * remembered. How the clouds are drawn (volume or flat) is the device's
   * call, not the reader's. Lightning is remembered under its own key so a
   * choice saved before it existed does not switch it off. The satellites
   * have no switch of their own: they follow the live sky. */
  function skyLayers() {
    var saved = null, savedB = null;
    try { saved = localStorage.getItem('rc-sky-layers'); savedB = localStorage.getItem('rc-sky-bolts'); } catch (e) { /* private mode */ }
    SKY.clouds = !saved || saved.indexOf('clouds') >= 0;
    SKY.rain = !saved || saved.indexOf('rain') >= 0;
    SKY.bolts = !!SKY.bolt && savedB !== 'off';
    if (!SKY.bolt) $('#sky-bolts').hidden = true;
    function apply() {
      $('#sky-clouds').setAttribute('aria-pressed', SKY.clouds);
      $('#sky-rain').setAttribute('aria-pressed', SKY.rain);
      $('#sky-bolts').setAttribute('aria-pressed', SKY.bolts);
      stage.setLayers(SKY.clouds, SKY.rain);
      if (SKY.clouds) skyNoise();
      boltClouds();
      boltsApply();
      satsApply();
      skyLegend();
      if (S.h) overCity(S.h);
    }
    [['#sky-clouds', 'clouds'], ['#sky-rain', 'rain'], ['#sky-bolts', 'bolts']].forEach(function (b) {
      $(b[0]).addEventListener('click', function () {
        SKY[b[1]] = !SKY[b[1]];
        try {
          localStorage.setItem('rc-sky-layers', [SKY.clouds ? 'clouds' : '', SKY.rain ? 'rain' : ''].join(','));
          localStorage.setItem('rc-sky-bolts', SKY.bolts ? 'on' : 'off');
        } catch (e) { /* private mode */ }
        apply();
      });
    });
    apply();
  }

  /* The lightning layer follows the live sky and its own switch; the frames
   * are fetched only once somebody wants to see them, and only after the
   * clouds are on screen (or their fetch has settled, or BOLT_HOLD ms have
   * passed): the flashes light the clouds, so they have nothing to light
   * before, and their 1400 x 1400 frames should not compete with the cloud
   * frames. */
  var BOLT_HOLD = 6000;
  function boltsApply() {
    if (!SKY.bolt) return;
    SKY.bolt.show(SKY.bolts, SKY.on);
    if (!(SKY.on && SKY.bolts)) return;
    if ((SKY.wx && SKY.wx.ir) || SKY.boltGo) { lightning(false); return; }
    if (!SKY.boltTimer) SKY.boltTimer = setTimeout(function () { SKY.boltGo = true; boltsApply(); }, BOLT_HOLD);
  }

  function lightning(refresh) {
    if (!SKY.bolt || SKY.bLoading || (SKY.b && !refresh)) return;
    SKY.bLoading = true;
    AtlasSky.bolts(CFG.wx, { lite: LITE }).then(function (b) {
      SKY.bLoading = false; SKY.b = b; SKY.bErr = null;
      SKY.bolt.setData(b);
      SKY.bolt.setStale(now() - b.t > BOLT_STALE);
      pmark('bolts');
      skyLegend();
      if (S.h) overCity(S.h);
    }, function (e) {
      SKY.bLoading = false; SKY.bErr = String(e && e.message || e);
      skyLegend();
    });
  }
  // Frames come every 5 minutes; an hour without one means the feed stalled.
  var BOLT_STALE = 3600000;

  /* Satellites: drawn from the roster the site was built with, then from
   * CelesTrak's current elements. CelesTrak updates them every two hours and
   * asks clients not to fetch more often, so the reply is kept that long in
   * localStorage. With ?now= (review, tests) the built copy is used as is,
   * so the positions are the same on every run. */
  var SAT_TTL = 2 * 3600000;
  function satsApply() {
    if (!SKY.sat) return;
    SKY.sat.show(true, SKY.on);
    if (SKY.on) satellites();
  }

  function satellites() {
    if (SKY.sLoading || SKY.sData) return;
    SKY.sLoading = true;
    fetch(CFG.sats).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(function (d) {
      SKY.sData = d; SKY.sLoading = false;
      SKY.sat.setData(d.sats, 'baked');
      skyLegend();
      if (!NOWQ) satsLive(d);
    }, function (e) {
      SKY.sLoading = false; SKY.sErr = String(e && e.message || e);
      skyLegend();
    });
  }

  function satsLive(d) {
    var want = {}, cache = null;
    d.sats.forEach(function (s) { want[s.id] = true; });
    function use(omm) {
      var n = 0;
      var sats = d.sats.map(function (s) {
        var o = omm[s.id];
        if (!o || !(Date.parse(String(o.EPOCH).slice(0, 23) + 'Z') > Date.parse(String(s.omm.EPOCH).slice(0, 23) + 'Z'))) return s;
        n++;
        return Object.assign({}, s, { omm: o });
      });
      if (n) { SKY.sat.setData(sats, 'live'); skyLegend(); }
    }
    try { cache = JSON.parse(localStorage.getItem('rc-sats-omm') || 'null'); } catch (e) { /* private mode */ }
    if (cache && cache.omm && Date.now() - cache.at < SAT_TTL) return use(cache.omm);
    Promise.all((CFG.satsLive || []).map(function (u) {
      return fetch(u).then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; });
    })).then(function (lists) {
      var omm = {};
      lists.forEach(function (l) {
        (Array.isArray(l) ? l : []).forEach(function (o) { if (want[o.NORAD_CAT_ID] && o.EPOCH && o.MEAN_MOTION) omm[o.NORAD_CAT_ID] = o; });
      });
      if (!Object.keys(omm).length) return;
      try { localStorage.setItem('rc-sats-omm', JSON.stringify({ at: Date.now(), omm: omm })); } catch (e) { /* private mode */ }
      use(omm);
    });
  }
  // The volume's 3-D noise, fetched once and only when a volume tier is in use.
  function skyNoise() {
    if (!stage || stage.tier === 'flat' || SKY.noise) return;
    SKY.noise = true;
    AtlasSky.noise3d(function (d, N) { stage.setNoise(d, N); });
  }

  function skyApply() {
    $('#sky-toggle').setAttribute('aria-pressed', SKY.on);
    document.body.classList.toggle('sky-off', !SKY.on);
    if (stage) stage.setLive(SKY.on);
    if (SKY.on) { skyTick(); weather(false); }
    boltsApply();
    satsApply();
    skyLegend();
    if (S.h) overCity(S.h);
  }

  function skyTick() {
    if (!stage || !SKY.on) return;
    var t = now(), b = AtlasSky.bodies(t);
    stage.setSky({
      sun: AtlasSky.vec(b.sun.lon, b.sun.lat), moon: AtlasSky.vec(b.moon.lon, b.moon.lat),
      moonK: b.moonLit, rot: (b.gmst - AtlasSky.gmst(CFG.starsEpoch)) * DEG
    });
    SKY.bodies = b;
    if (SKY.bolt) SKY.bolt.setSun(AtlasSky.vec(b.sun.lon, b.sun.lat));
    skyLegend();
  }

  /* Each layer goes on screen the moment it is read: the world clouds, then
   * the Meteosat clouds over them, the rain on its own. A layer the page
   * already shows (same key) is skipped, so a refresh that finds nothing
   * newer changes nothing. A first visit in this browser shows the last
   * layers it saw (skyCache) while the fresh ones load; each carries its
   * own time, and the legend ages it by that. */
  function weather(refresh) {
    if (SKY.loading || (SKY.wx && SKY.wxLive && !refresh)) return;
    SKY.loading = true;
    if (!SKY.wx) skyCache.get().then(function (c) { if (c && !SKY.wxLive) showWx(c, true); });
    AtlasSky.weather(CFG.wx, now(), { lite: LITE }, function (part) { showWx(part, false); }).then(function (wx) {
      SKY.loading = false;
      if (!SKY.boltGo) { SKY.boltGo = true; boltsApply(); }
      SKY.err = wx.errors.length ? wx.errors.join('; ') : null;
      if (!wx.ir && !wx.rain) { skyLegend(); return; }
      skyCache.put(SKY.wx);
      skyLegend();
    }, function (e) {
      SKY.loading = false; SKY.err = String(e && e.message || e); skyLegend();
      if (!SKY.boltGo) { SKY.boltGo = true; boltsApply(); }
    });
  }

  /* Put one or both layers on screen. A new cloud field cross-fades from
   * the one shown (stage.setWeather: a, then b). */
  function showWx(p, cached) {
    var o = SKY.wx || { ir: null, rain: null }, n = { ir: null, rain: null }, changed = false;
    if (p.ir && (!o.ir || o.ir.key !== p.ir.key || o.cached)) {
      var a = o.ir && o.ir.w === p.ir.w && o.ir.h === p.ir.h ? o.ir.b : p.ir.b;
      n.ir = Object.assign({}, p.ir, { a: a });
      changed = true;
    }
    if (p.rain && (!o.rain || o.rain.key !== p.rain.key || o.cached)) { n.rain = p.rain; changed = true; }
    if (!changed) return;
    if (!cached) SKY.wxLive = true;
    SKY.wx = { ir: n.ir || o.ir, rain: n.rain || o.rain, cached: cached && !SKY.wxLive };
    if (stage) stage.setWeather({ ir: n.ir, rain: n.rain });
    if (n.ir) { pmark(cached ? 'clouds-cached' : 'clouds'); document.documentElement.dataset.clouds = n.ir.key + (cached ? ' cached' : ''); }
    if (n.rain) { pmark(cached ? 'rain-cached' : 'rain'); document.documentElement.dataset.rain = n.rain.key + (cached ? ' cached' : ''); }
    boltClouds();
    if (n.ir) { boltsApply(); if (!cached) cityDetail(); }
    skyLegend();
    if (S.h) overCity(S.h);
  }

  /* The last layers seen, in IndexedDB, so the next city page (or visit)
   * has clouds at once. Kept 3 hours past the frame's own time - the world
   * mosaic's cadence - and never used with ?now= (review and tests pin the
   * clock; ?cache=1 turns it on there, for the cache check). */
  var skyCache = (function () {
    var TTL = 3 * 3600000, on = typeof indexedDB !== 'undefined' &&
      (!NOWQ || new URLSearchParams(location.search).get('cache') === '1');
    function db() {
      return new Promise(function (ok, no) {
        var r = indexedDB.open('rc-sky', 1);
        r.onupgradeneeded = function () { r.result.createObjectStore('wx'); };
        r.onsuccess = function () { ok(r.result); };
        r.onerror = function () { no(r.error); };
      });
    }
    function tx(mode, f) {
      return db().then(function (d) {
        return new Promise(function (ok, no) {
          var t = d.transaction('wx', mode), s = t.objectStore('wx'), r = f(s);
          t.oncomplete = function () { d.close(); ok(r && r.result); };
          t.onerror = t.onabort = function () { d.close(); no(t.error); };
        });
      });
    }
    function strip(l, ch) { if (!l) return null; var o = { w: l.w, h: l.h, t: l.t, tf: l.tf, key: l.key }; o[ch] = l[ch]; return o; }
    return {
      get: function () {
        if (!on) return Promise.resolve(null);
        return tx('readonly', function (s) { return s.get('last'); }).then(function (c) {
          if (!c || c.v !== 1) return null;
          var t = now(), ir = c.ir && t - c.ir.t < TTL + 3 * 3600000 ? c.ir : null, rain = c.rain && t - c.rain.t < TTL ? c.rain : null;
          return ir || rain ? { ir: ir, rain: rain } : null;
        }).catch(function () { return null; });
      },
      put: function (wx) {
        if (!on || !wx || wx.cached) return;
        tx('readwrite', function (s) { return s.put({ v: 1, ir: strip(wx.ir, 'b'), rain: strip(wx.rain, 'd') }, 'last'); })
          .then(function () { document.documentElement.dataset.skySaved = (wx.ir && wx.ir.key) || ''; })
          .catch(function () { /* quota, private mode */ });
      }
    };
  })();

  /* The city-detail frame: about 2 km a pixel around the selected city,
   * where Meteosat sees it, asked for once the Meteosat clouds are on
   * screen (it is read with their calibration). Not in a light session. */
  function cityDetail() {
    if (!stage || !S.h || LITE || !SKY.on || !CFG.wx.mtgIrDetail) return;
    var ir = SKY.wx && SKY.wx.ir;
    if (!ir || !ir.tf || SKY.wx.cached) return;
    var key = S.h.slug + '|' + ir.tf;
    if (SKY.detKey === key) return;
    SKY.detKey = key;
    stage.setDetail(null);
    AtlasSky.detail(CFG.wx, { lon: S.h.lon, lat: S.h.lat }, 1024).then(function (d) {
      if (SKY.detKey !== key) return;          // another city, or a newer frame, won
      stage.setDetail(d);
      pmark('detail');
    }, function (e) {
      if (SKY.detKey === key) SKY.detErr = String(e && e.message || e);
    });
  }

  // Lightning lights the cloud it is in: the layer needs to know where cloud is.
  function boltClouds() {
    if (SKY.bolt) SKY.bolt.setClouds(SKY.wx && SKY.clouds !== false ? function (lon, lat) { return AtlasSky.at(SKY.wx, lon, lat).cloud; } : null);
  }

  function ago(t) {
    var m = Math.max(0, Math.round((now() - t) / 60000));
    return m < 60 ? m + ' min ago' : Math.round(m / 60) + ' h ago';
  }
  function utc(t) { return new Date(t).toISOString().slice(11, 16) + ' UTC'; }

  /* The legend says what is drawn, from where, and how old it is. */
  function skyLegend() {
    var box = $('#sky-src'); if (!box) return;
    var wx = SKY.wx, b = SKY.bodies, parts = [];
    if (!SKY.on) { box.innerHTML = 'Off. The globe shows the report\u2019s fixed light.'; return; }
    if (!stage) { box.innerHTML = 'Needs WebGL 2, which this browser does not offer.'; return; }
    // One short line per source; the detail lives in the tooltip.
    function line(label, title, src, t, off, extra) {
      return '<span data-tt="' + title + '"><b>' + label + '</b> ' + src + ' \u00b7 ' + utc(t) + ' \u00b7 ' + ago(t) +
        (off ? ' \u00b7 off' : extra || '') + '</span>';
    }
    /* Over Europe, Africa and the Atlantic, clouds, rain and lightning all
     * come from Meteosat, minutes apart: one line for all three, aged by the
     * oldest of them, and one short line for the world sources elsewhere.
     * The full account of each source lives in the tooltips. */
    var bo = SKY.b, flat = stage.tier === 'flat';
    var mIr = wx && wx.ir && wx.ir.tf, mRain = wx && wx.rain && wx.rain.tf, mBolt = SKY.bolt && bo;
    if (mIr) {
      var names = [], offs = [], ts = [];
      function add(name, t, off) { names.push(name); ts.push(t); if (off) offs.push(name); }
      add('clouds', wx.ir.tf, SKY.clouds === false);
      if (mRain) add('rain', wx.rain.tf, SKY.rain === false);
      if (mBolt) add('lightning', bo.t, !SKY.bolts);
      var oldest = Math.min.apply(null, ts);
      var tip = 'Over Europe, Africa and the Atlantic, from the same satellite, Meteosat (MTG) at 0\u00b0: ' +
        'clouds from its 10.5 \u00b5m infrared image, every 10 minutes (cover and height read from how cold the cloud tops look; heights exaggerated about 20\u00d7), ' +
        'at about 2 km a pixel around the selected city and about 19 km elsewhere. ' +
        (mRain ? 'Rain from H SAF h40b, that infrared calibrated by microwave satellite passes, for the same moment as the clouds; drawn into the clouds, darker where heavier, and only where there is cloud. ' : '') +
        (mBolt ? 'Lightning from the Lightning Imager, last ' + bo.frames.length * 5 + ' minutes, flashing inside the clouds where it counted flashes, and only where there is cloud. ' : '') +
        'The age shown is the oldest of them' + (flat ? '. This device gets the flat cloud layer.' : '.');
      parts.push('<span data-tt="' + tip + '"><b>Meteosat</b> ' + names.join(', ') + ' \u00b7 ' + ago(oldest) +
        (offs.length ? ' \u00b7 ' + offs.join(', ') + ' off' : '') +
        (mBolt && now() - bo.t > BOLT_STALE ? ' \u00b7 <b>stale</b>' : '') + (flat ? ' \u00b7 flat' : '') + '</span>');
      var el = ['clouds ' + ago(wx.ir.t)];
      if (wx.rain) el.push('rain ' + ago(wx.rain.t));
      parts.push('<span data-tt="Outside Meteosat\u2019s view (the Americas, Asia, the Pacific): clouds from the EUMETSAT IR 10.8 \u00b5m world mosaic (every 3 hours, ' +
        utc(wx.ir.t) + '), rain from NASA GPM IMERG (half-hourly, early run' + (wx.rain ? ', ' + utc(wx.rain.t) : '') + '). No lightning data there."><b>Elsewhere</b> ' + el.join(' \u00b7 ') + '</span>');
    }
    // Without the Meteosat frames: one line per world source.
    if (!mIr && wx && wx.ir)
      parts.push(line('Clouds', 'EUMETSAT IR 10.8 \u00b5m world cloud mosaic, every 3 hours. Cover and height are read from how cold the cloud tops look; heights are exaggerated about 20\u00d7 so they show at globe scale.' +
        (flat ? ' This device gets the flat cloud layer.' : ''),
        'EUMETSAT', wx.ir.t, SKY.clouds === false, flat ? ' \u00b7 flat' : ''));
    if (!mIr && wx && wx.rain)
      parts.push(line('Rain', 'NASA GPM IMERG half-hourly estimate (early run). Rain is only drawn where there is cloud: it darkens and greys the cloud; heavier rain is darker.',
        'IMERG', wx.rain.t, SKY.rain === false));
    if (!mIr && mBolt) parts.push(line('Lightning', 'EUMETSAT Meteosat Lightning Imager, accumulated flash area, last ' + bo.frames.length * 5 + ' minutes: the storms flash inside their clouds where the satellite counted flashes, more often where it counted more. It sees 70\u00b0 either way of 0\u00b0 (faint dashed edge); outside it, no data.',
      'Meteosat LI', bo.t, !SKY.bolts, now() - bo.t > BOLT_STALE ? ' \u00b7 <b>stale</b>' : ''));
    else if (SKY.bolt && SKY.bolts && SKY.bErr && !SKY.bLoading) parts.push('Lightning unavailable right now.');
    var sat = SKY.sat;
    if (sat && sat.list.length) {
      var nS = sat.list.length, fr = sat.fresh(), geo = sat.list.filter(function (x) { return x.kind === 'geo'; }).length;
      parts.push(line('Satellites', nS + ' of the weather satellites this sky comes from, where they are now: ' + geo + ' geostationary (on the outer ring) and ' + (nS - geo) +
        ' in low polar orbits. Each is drawn where it really is: checked against SGP4, the standard orbit model, the positions agree to within about 15 km, under a pixel at globe scale. Polar orbits are to scale, 400 to 840 km up; the geostationary ring is really 35,786 km up, 6.6 Earth radii from the centre, and is drawn about ' +
        Math.round(6.61 / sat.GEO_R) + '\u00d7 closer. Orbits from CelesTrak mean elements' + (sat.src === 'baked' ? ', as the site was built' : '') + '; the time shown is the oldest in use. Hover one for its orbit and what it carries.',
        'CelesTrak', sat.oldest(), false,
        (fr < nS ? ' \u00b7 ' + (nS - fr) + ' orbit only, data too old' : ' \u00b7 true positions') +
        '<i class="sat-key geo"></i><span class="sky-key">ring, not to scale</span><i class="sat-key leo"></i><span class="sky-key">polar</span>'));
    } else if (sat && SKY.sErr && !SKY.sLoading) parts.push('Satellites unavailable right now.');
    if (!wx && SKY.loading) parts.push('Loading the latest satellite images\u2026');
    if (!wx && !SKY.loading && SKY.err) parts.push('Satellite images unavailable right now.');
    // Cloud frames arrive every 3 h and rain every 30 min; well past that,
    // the service has stalled and the picture is history, so say so.
    var old = [wx && wx.ir && now() - wx.ir.t > 9 * 3600000 ? 'clouds' : '',
               wx && wx.rain && now() - wx.rain.t > 9 * 3600000 ? 'rain' : ''].filter(Boolean);
    if (old.length) parts.push('<b>Stale:</b> the ' + old.join(' and ') + ' shown ' + (old.length > 1 ? 'are' : 'is') +
      ' over 9 h old; the satellite service has not published since.');
    if (b) parts.push('<b>Sun &amp; Moon</b> ' + utc(now()) + (NOWQ ? ' (fixed)' : '') + ' \u00b7 Moon ' + Math.round(b.moonLit * 100) + '% lit');
    box.innerHTML = parts.join('<br>');
  }

  /* ── Right now in the selected city: the live forecast, read against
   * the city's own record ─────────────────────────────────────────────
   *
   * The audit scored Open-Meteo's best_match forecast as the day's highest
   * hourly chance of rain, per local calendar day, from the freshest run
   * (capitals.py fetch_pop). Today's precipitation_probability_max is the
   * same number, so it is the one looked up in the city's bins. */
  var WMO = [
    [[0], 'Clear sky', 'clear'], [[1, 2], 'Mainly clear', 'clear'], [[3], 'Overcast', 'cloud'],
    [[45, 48], 'Fog', 'fog'], [[51, 53, 55, 56, 57], 'Drizzle', 'rain'],
    [[61, 63, 65, 66, 67, 80, 81, 82], 'Rain', 'rain'], [[71, 73, 75, 77, 85, 86], 'Snow', 'snow'],
    [[95, 96, 99], 'Thunderstorm', 'storm']
  ];
  function wmo(code) {
    for (var i = 0; i < WMO.length; i++) if (WMO[i][0].indexOf(code) >= 0) return { label: WMO[i][1], kind: WMO[i][2] };
    return { label: 'Unknown', kind: 'cloud' };
  }
  function binOf(h, p) {
    if (p == null || !h.bins || !h.bins.length) return null;
    for (var i = 0; i < h.bins.length; i++) {
      var b = h.bins[i];
      if (p >= b.lo - 1e-9 && (p < b.hi - 1e-9 || i === h.bins.length - 1)) return b;
    }
    return null;
  }

  var liveSeq = 0, fcCache = {};
  function live(h) {
    var box = $('#now'), seq = ++liveSeq;
    box.hidden = false;
    box.classList.add('pending'); box.classList.remove('fail');
    box.setAttribute('aria-busy', 'true');
    overCity(h);
    var url = CFG.forecast + '?latitude=' + h.lat + '&longitude=' + h.lon +
      '&current=temperature_2m,weather_code,is_day,precipitation' +
      '&hourly=precipitation_probability' +
      '&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max' +
      '&forecast_days=2&timezone=auto';
    var hit = fcCache[h.slug];
    var p = hit && now() - hit.at < 10 * 60000 ? Promise.resolve(hit.j) : fetchT(url, 8000).then(function (r) {
      if (!r.ok) throw new Error(r.status); return r.json();
    }).then(function (j) { fcCache[h.slug] = { at: now(), j: j }; return j; });
    p.then(function (j) {
      if (seq !== liveSeq) return;                 // a newer city won the race
      fillNow(h, j);
    }).catch(function () {
      if (seq !== liveSeq) return;
      // The analysis is the product: an outage only says so, quietly.
      box.classList.remove('pending'); box.classList.add('fail');
      box.setAttribute('aria-busy', 'false');
      $('#now-said').innerHTML = 'The live forecast could not be loaded right now. Everything else on this page is the audit, and stands.';
    });
  }

  function fetchT(url, ms) {
    var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = setTimeout(function () { ctrl && ctrl.abort(); }, ms);
    return fetch(url, ctrl ? { signal: ctrl.signal } : undefined).then(function (r) { clearTimeout(timer); return r; },
      function (e) { clearTimeout(timer); throw e; });
  }

  function fillNow(h, j) {
    var box = $('#now'), cur = j.current || {}, day = j.daily || {}, w = wmo(cur.weather_code);
    var pop = day.precipitation_probability_max ? day.precipitation_probability_max[0] : null;
    var popT = day.precipitation_probability_max ? day.precipitation_probability_max[1] : null;
    var hi = day.temperature_2m_max ? day.temperature_2m_max[0] : null, lo = day.temperature_2m_min ? day.temperature_2m_min[0] : null;
    box.classList.remove('pending'); box.setAttribute('aria-busy', 'false');
    box.dataset.kind = w.kind + (cur.is_day === 0 ? ' night' : '');
    $('#now-temp').textContent = cur.temperature_2m == null ? '\u2014' : minus(Math.round(cur.temperature_2m)) + '\u00b0';
    $('#now-cond').textContent = w.label;
    var at = /T(\d\d:\d\d)/.exec(cur.time || '');
    $('#now-range').textContent = (hi != null ? 'H ' + minus(Math.round(hi)) + '\u00b0 \u00b7 L ' + minus(Math.round(lo)) + '\u00b0' : '') +
      (at ? ' \u00b7 ' + at[1] + ' local' : '');
    // Every figure is a vendor value or a number from the city's own table.
    var b = binOf(h, pop == null ? null : pop / 100), said = '';
    if (pop == null) said = 'No chance of rain was published for today.';
    else {
      said = 'Today\u2019s forecast: <b class="now-pop">' + pop + '%</b> chance of rain.';
      if (b) {
        var wet = Math.round(b.rained * b.n);
        said += ' <span class="now-meant' + (b.sig ? ' off' : '') + '">On the <b>' + b.n.toLocaleString('en') + '</b> days it said ' +
          Math.round(b.lo * 100) + '\u2013' + Math.round(b.hi * 100) + '% in ' + esc(h.name) + ', it rained on <b>' + wet.toLocaleString('en') +
          '</b> (<b>' + pct(b.rained) + '</b>)' + (b.sig ? (b.rained > b.said ? ': more often than it says.' : ': less often than it says.') : ', in line with what it says.') + '</span>';
      } else said += ' <span class="dim">No comparable days in ' + esc(h.name) + '\u2019s record.</span>';
    }
    $('#now-said').innerHTML = said;
    $('#now-tom').textContent = popT != null ? 'Tomorrow: ' + popT + '% chance of rain.' : '';
    ribbon(j);
  }

  /* The next 12 hours of hourly chance, as small bars. */
  function ribbon(j) {
    var box = $('#now-hours'), H = j.hourly || {}, t = H.time || [], p = H.precipitation_probability || [];
    var nowL = (j.current && j.current.time || '').slice(0, 13), i0 = 0;
    for (var i = 0; i < t.length; i++) if (t[i].slice(0, 13) >= nowL) { i0 = i; break; }
    var html = '';
    for (i = i0; i < Math.min(t.length, i0 + 12); i++) {
      var v = p[i] == null ? 0 : p[i];
      html += '<span title="' + t[i].slice(11, 16) + ' \u00b7 ' + v + '%"><i style="height:' + Math.max(2, v) + '%"></i>' +
        ((i - i0) % 3 === 0 ? '<em>' + t[i].slice(11, 13) + '</em>' : '') + '</span>';
    }
    box.innerHTML = html;
    box.setAttribute('aria-label', 'Chance of rain, next 12 hours: ' + p.slice(i0, i0 + 12).join('%, ') + '%');
  }

  /* What the satellites show over the city right now, in plain words. */
  function overCity(h) {
    var el = $('#now-sat'), wx = SKY.wx, bo = SKY.b;
    if (!SKY.on || (!wx && !bo)) { el.textContent = ''; return; }
    var s = [];
    if (wx) {
      var o = AtlasSky.at(wx, h.lon, h.lat);
      if (o.cloud != null) s.push(o.cloud > 0.6 ? 'thick cloud' : o.cloud > 0.25 ? 'some cloud' : 'mostly clear skies');
      if (o.rain) s.push((o.snow ? 'snow' : 'rain') + ' about ' + (o.rain < 1 ? o.rain.toFixed(1) : Math.round(o.rain)) + ' mm/h');
    }
    // Lightning within 50 km in the frames held (the last 15 minutes).
    if (bo && SKY.bolts && now() - bo.t <= BOLT_STALE) {
      var ln = AtlasSky.boltsNear(bo, h.lon, h.lat, 50);
      if (ln.cells) s.push('lightning ' + (ln.km < 5 ? 'overhead' : Math.round(ln.km) + ' km away') + ' in the last ' + bo.frames.length * 5 + ' min');
    }
    el.textContent = s.length ? 'Satellites over ' + h.name + ' now: ' + s.join(', ') + '.' : '';
  }

  function chapters() {
    var links = {}; $$('.chapters a').forEach(function (a) { links[a.dataset.ch] = a; });
    // Chapter links scroll without writing #chapter into the address, so a
    // reload never reopens part-way down the page.
    $$('.chapters a, .brand').forEach(function (a) {
      a.addEventListener('click', function (e) {
        var t = document.getElementById(a.getAttribute('href').slice(1)); if (!t) return;
        e.preventDefault();
        // A chapter's card sits mid-way down a section taller than the screen,
        // so scrolling to the section's top leaves it low. Centre the card in
        // the space under the fixed bar instead (top-aligned if it is too tall).
        // Layout offsets, not getBoundingClientRect, so the card's not-yet-run
        // entry transform doesn't skew the target.
        var card = t.id === 'hero' ? null : t.querySelector(':scope > .card');
        if (!card) { t.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth' }); return; }
        var top = 0; for (var n = card; n; n = n.offsetParent) top += n.offsetTop;
        var bar = $('.bar'), barB = bar ? bar.getBoundingClientRect().bottom + 12 : 0;
        var room = innerHeight - barB, y = top - barB - Math.max(0, (room - card.offsetHeight) / 2);
        scrollTo({ top: Math.max(0, y), behavior: reduce ? 'auto' : 'smooth' });
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
