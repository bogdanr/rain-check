/* The look panel (?tune=1): sliders over the cloud, lightning and wave look, for
 * tuning by eye on a real screen and GPU. Never loaded for readers - atlas.js
 * adds this script only when the address asks for it.
 *
 * Every change is live (stage.setLook / bolts.setLook) and kept in
 * localStorage, so a reload keeps it. "Copy values" puts the changed values
 * on the clipboard as JSON, to paste back as the defaults (LOOK in stage.js
 * and bolts.js); "Reset" returns to those defaults. */
(function (global) {
  'use strict';
  var KEY = 'rc-tune';

  // [key, label]; each slider runs from 0 to twice its default, so the
  // default sits in the middle and can be pushed either way.
  var CLOUD = [
    ['Clouds'],
    ['density', 'Density'],
    ['rim', 'Rim opacity'],
    ['rimWidth', 'Rim width'],
    ['veil', 'Thin cover opacity'],
    ['lump', 'Lumpy tops'],
    ['billow', 'Puffiness'],
    ['Light'],
    ['sun', 'Sunlight'],
    ['shadow', 'Self-shadow'],
    ['glow', 'Inner glow'],
    ['powder', 'Dark fringes'],
    ['ambient', 'Sky light'],
    ['Types'],
    ['types', 'Cloud types'],
    ['cirrus', 'Cirrus'],
    ['flat', 'Flat layer opacity']
  ];
  var BOLT = [
    ['Lightning'],
    ['bright', 'Brightness'],
    ['size', 'Glow size'],
    ['rate', 'Flash rate /s'],
    ['day', 'Daylight visibility'],
    ['minCover', 'Min. cloud cover'],
    ['contrast', 'Storm contrast'],
    ['spread', 'Flash strength spread']
  ];
  var WAVE = [
    ['Waves'],
    ['long', 'Long sea, px'],
    ['chop', 'Chop, px'],
    ['speed', 'Crest speed'],
    ['steep', 'Steepness'],
    ['sky', 'Sky light in faces'],
    ['sparkle', 'Sun sparkle'],
    ['foam', 'Crest foam']
  ];
  var ARROWS = 'rc-wind-arrows';

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; }
  }
  function save(v) {
    try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) { /* private mode */ }
  }

  global.AtlasTune = function (stage, bolts, util) {
    var saved = load(), state = { clouds: saved.clouds || {}, bolts: saved.bolts || {}, waves: saved.waves || {} };
    var dC = stage.lookDefaults(), dB = bolts ? bolts.lookDefaults() : {};
    stage.setLook(state.clouds);
    if (bolts) bolts.setLook(state.bolts);
    // The waves live in stage.wave / stage.setWave; this gives the slider
    // builder the same look / setLook shape as the clouds and lightning.
    var waves = stage.setWave ? {
      get look() { return stage.wave; },
      setLook: function (o) { stage.setWave(o); }
    } : null;
    var dW = waves ? stage.waveDefaults() : {};
    // Only the keys that still have a slider: older saved values for
    // removed sliders would otherwise apply with no way to see or undo them.
    var wkeys = WAVE.filter(function (d) { return d.length > 1; }).map(function (d) { return d[0]; });
    for (var wk in state.waves) if (wkeys.indexOf(wk) < 0) delete state.waves[wk];
    if (waves) stage.setWave(state.waves);

    var css = el('style', {}, [
      '#tune{position:fixed;left:10px;bottom:10px;z-index:9999;width:260px;max-height:calc(100vh - 20px);overflow:auto;',
      'background:rgba(12,16,26,.88);color:#dde4f0;font:11px/1.35 ui-monospace,monospace;border:1px solid #334;',
      'border-radius:6px;padding:8px 10px;backdrop-filter:blur(4px)}',
      '#tune h4{margin:8px 0 3px;font-size:11px;color:#8fb3ff;text-transform:uppercase;letter-spacing:.05em}',
      '#tune label{display:grid;grid-template-columns:1fr 92px 34px;gap:4px;align-items:center;margin:1px 0}',
      '#tune input[type=range]{width:92px;margin:0}',
      '#tune .v{text-align:right;color:#fff}',
      '#tune .row{display:flex;gap:6px;align-items:center;margin:3px 0;flex-wrap:wrap}',
      '#tune button,#tune select{font:inherit;background:#223;color:#dde4f0;border:1px solid #445;border-radius:3px;padding:2px 6px;cursor:pointer}',
      '#tune .hd{display:flex;justify-content:space-between;align-items:center;font-weight:bold}',
      '#tune.min>:not(.hd){display:none}'
    ].join(''));
    document.head.appendChild(css);
    var box = el('div', { id: 'tune', role: 'region', 'aria-label': 'Look tuning panel' });
    var hd = el('div', { 'class': 'hd' }), fold = el('button', { type: 'button', title: 'Fold' }, '\u2013');
    hd.appendChild(el('span', {}, 'Look tuning'));
    hd.appendChild(fold);
    box.appendChild(hd);
    fold.onclick = function () { box.classList.toggle('min'); fold.textContent = box.classList.contains('min') ? '+' : '\u2013'; };

    // Readout: frame interval and CPU cost, quality tier.
    var fps = el('div', { 'class': 'row' });
    box.appendChild(fps);
    setInterval(function () {
      fps.textContent = 'frame ' + (stage.frameMs || 0).toFixed(1) + ' ms \u00b7 cpu ' + (stage.cpuMs || 0).toFixed(1) +
        ' ms \u00b7 ' + stage.tier + (bolts ? ' \u00b7 storms ' + (bolts.c.dataset.storms || 0) : '');
    }, 500);

    // Tier, soft, types.
    var row = el('div', { 'class': 'row' }), tier = el('select', { 'aria-label': 'Quality tier' });
    ['flat', 'mid', 'high', 'ultra'].forEach(function (t) {
      var o = el('option', { value: t }, t); if (t === stage.tier) o.selected = true; tier.appendChild(o);
    });
    tier.onchange = function () { stage.pinned = true; stage.setTier(tier.value); };
    row.appendChild(tier);
    [['soft', 'soft'], ['types', 'types']].forEach(function (s) {
      var l = el('label', { style: 'display:flex;gap:3px' }), cb = el('input', { type: 'checkbox' });
      cb.checked = !!stage[s[0]];
      cb.onchange = function () { stage.setSwitch(s[0], cb.checked); };
      l.appendChild(cb); l.appendChild(document.createTextNode(s[1]));
      row.appendChild(l);
    });
    box.appendChild(row);

    // The Sun: live, or pinned to an hour (UTC) to judge shading in low light.
    box.appendChild(el('h4', {}, 'Sun'));
    var sl = el('label'), sun = el('input', { type: 'range', min: 0, max: 24, step: 0.25, value: 12 }),
      sv = el('span', { 'class': 'v' }, 'live'), live = el('input', { type: 'checkbox', title: 'Live Sun' });
    live.checked = true;
    sl.appendChild(el('span', {}, 'Hour UTC'));
    sl.appendChild(sun); sl.appendChild(sv);
    var lr = el('label', { style: 'display:flex;gap:3px' });
    lr.appendChild(live); lr.appendChild(document.createTextNode('live Sun'));
    function setSun() {
      if (live.checked) { sv.textContent = 'live'; stage.setSunOverride(null); if (bolts) bolts.setSun(stage.sky && stage.sky.sun); return; }
      var h = +sun.value, s0 = stage.sky && stage.sky.sun, lat = s0 ? Math.asin(s0[1]) * 180 / Math.PI : 0;
      var v = util.vec(-(h - 12) * 15, lat);
      sv.textContent = h.toFixed(2).replace(/\.?0+$/, '');
      stage.setSunOverride(v);
      if (bolts) bolts.setSun(v);
    }
    sun.oninput = function () { live.checked = false; setSun(); };
    live.onchange = setSun;
    box.appendChild(sl); box.appendChild(lr);

    function sliders(list, target, dflt, bucket) {
      var inputs = {};
      list.forEach(function (d) {
        if (d.length === 1) { box.appendChild(el('h4', {}, d[0])); return; }
        var cur = target.look[d[0]], mx = 2 * dflt[d[0]] || 1;
        var fmt = function (x) { return (+x).toFixed(mx < 0.5 ? 3 : 2); };
        var l = el('label', { title: 'default ' + dflt[d[0]] }),
          r = el('input', { type: 'range', min: 0, max: mx, step: mx / 100, value: cur }),
          v = el('span', { 'class': 'v' }, fmt(cur));
        l.appendChild(el('span', {}, d[1])); l.appendChild(r); l.appendChild(v);
        r.oninput = function () {
          var o = {}; o[d[0]] = +r.value; v.textContent = fmt(r.value);
          target.setLook(o);
          if (+r.value === dflt[d[0]]) delete state[bucket][d[0]]; else state[bucket][d[0]] = +r.value;
          save(state);
        };
        inputs[d[0]] = [r, v, fmt];
        box.appendChild(l);
      });
      return inputs;
    }
    var ic = sliders(CLOUD, stage, dC, 'clouds'), ib = bolts ? sliders(BOLT, bolts, dB, 'bolts') : {};
    var iw = waves ? sliders(WAVE, waves, dW, 'waves') : {};

    // Debug arrows: the grid's own wind, one arrow per point. Kept in
    // localStorage so it also holds when Wind is switched on later.
    if (waves) {
      var ar = el('label', { style: 'display:flex;gap:3px', title: 'One arrow per grid point, to check the data' }),
        acb = el('input', { type: 'checkbox', id: 'tune-arrows' });
      try { acb.checked = localStorage.getItem(ARROWS) === 'on'; } catch (e) { /* private mode */ }
      acb.onchange = function () {
        try { if (acb.checked) localStorage.setItem(ARROWS, 'on'); else localStorage.removeItem(ARROWS); } catch (e) { /* private mode */ }
        var w = util.wind && util.wind();
        if (w) w.setDebug(acb.checked);
      };
      ar.appendChild(acb); ar.appendChild(document.createTextNode('wind arrows (debug)'));
      box.appendChild(ar);
      var w0 = util.wind && util.wind();
      if (w0 && acb.checked) w0.setDebug(true);
    }

    var btns = el('div', { 'class': 'row', style: 'margin-top:8px' });
    var copy = el('button', { type: 'button' }, 'Copy values'), reset = el('button', { type: 'button' }, 'Reset');
    copy.onclick = function () {
      var txt = JSON.stringify({ clouds: stage.look, bolts: bolts ? bolts.look : {}, waves: waves ? stage.wave : {}, changed: state, tier: stage.tier });
      (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(
        function () { copy.textContent = 'Copied'; setTimeout(function () { copy.textContent = 'Copy values'; }, 1200); },
        function () { global.prompt('Look values', txt); });
    };
    reset.onclick = function () {
      state = { clouds: {}, bolts: {}, waves: {} }; save(state);
      stage.setLook(null); if (bolts) bolts.setLook(null); if (waves) stage.setWave(null);
      [[ic, dC], [ib, dB], [iw, dW]].forEach(function (p) {
        for (var k in p[0]) { p[0][k][0].value = p[1][k]; p[0][k][1].textContent = p[0][k][2](p[1][k]); }
      });
    };
    btns.appendChild(copy); btns.appendChild(reset);
    box.appendChild(btns);
    document.body.appendChild(box);
    stage.c.dataset.tune = '1';
  };
})(window);
