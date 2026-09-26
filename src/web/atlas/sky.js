/* Atlas Noir - the live sky: where the Sun and Moon are, and what the weather
 * is doing, right now.
 *
 * Nothing here is invented. The Sun and Moon come from low-precision
 * ephemerides (Astronomical Almanac; about 0.01 deg for the Sun, 0.3 deg for
 * the Moon, which is far below a pixel on this globe). The clouds are the
 * latest EUMETSAT 10.8 um infrared mosaic, the rain is NASA's IMERG
 * half-hourly estimate, both read straight from their public map services
 * (both send Access-Control-Allow-Origin: *, so no server of ours is involved).
 *
 * What is interpretation, and labelled as such on the page:
 *  - Cloud cover is how much colder (brighter) a pixel is than the clear
 *    ground around it. The IR image is a styled grey, not calibrated
 *    temperatures, so this is a contrast, not a retrieval.
 *  - Cloud height follows the same contrast: colder tops stand taller. The
 *    height is exaggerated about 20x (stage.js draws the layer 64-290 km
 *    up), or it would be invisible at globe scale.
 *
 * The 3D noise volume only shapes clouds where the satellite sees cloud; it
 * never adds cloud to a clear sky.
 */
(function (global) {
  'use strict';

  var DEG = Math.PI / 180;

  /* ── Ephemerides ─────────────────────────────────────── */
  function jd(t) { return t / 86400000 + 2440587.5; }
  function wrap(d) { return ((d + 180) % 360 + 360) % 360 - 180; }

  /* Greenwich mean sidereal time, degrees. */
  function gmst(t) { return (280.46061837 + 360.98564736629 * (jd(t) - 2451545.0)) % 360; }

  /* Equatorial unit vector of the Sun (x to the March equinox, z to the pole). */
  function sunEq(t) {
    var n = jd(t) - 2451545.0;
    var L = 280.460 + 0.9856474 * n, g = (357.528 + 0.9856003 * n) * DEG;
    var lam = (L + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * DEG;
    var eps = (23.439 - 0.0000004 * n) * DEG;
    return [Math.cos(lam), Math.cos(eps) * Math.sin(lam), Math.sin(eps) * Math.sin(lam)];
  }

  /* Moon, geocentric, ecliptic terms from the Astronomical Almanac's low
   * precision formulae. */
  function moonEq(t) {
    var n = jd(t) - 2451545.0, T = n / 36525;
    function s(a) { return Math.sin(a * DEG); }
    var lam = 218.32 + 481267.881 * T + 6.29 * s(135.0 + 477198.87 * T) - 1.27 * s(259.3 - 413335.36 * T) +
      0.66 * s(235.7 + 890534.22 * T) + 0.21 * s(269.9 + 954397.74 * T) - 0.19 * s(357.5 + 35999.05 * T) -
      0.11 * s(186.5 + 966404.03 * T);
    var bet = 5.13 * s(93.3 + 483202.02 * T) + 0.28 * s(228.2 + 960400.89 * T) -
      0.28 * s(318.3 + 6003.15 * T) - 0.17 * s(217.6 - 407332.21 * T);
    var eps = (23.439 - 0.0000004 * n) * DEG;
    lam *= DEG; bet *= DEG;
    var x = Math.cos(bet) * Math.cos(lam), y = Math.cos(bet) * Math.sin(lam), z = Math.sin(bet);
    return [x, y * Math.cos(eps) - z * Math.sin(eps), y * Math.sin(eps) + z * Math.cos(eps)];
  }

  /* The ground point a body stands over: equatorial vector -> lon/lat. */
  function subPoint(v, t) {
    var ra = Math.atan2(v[1], v[0]) / DEG, dec = Math.asin(Math.max(-1, Math.min(1, v[2]))) / DEG;
    return { lon: wrap(ra - gmst(t)), lat: dec };
  }

  /* Everything the stage needs for one instant. */
  function bodies(t) {
    var s = sunEq(t), m = moonEq(t);
    var cosE = s[0] * m[0] + s[1] * m[1] + s[2] * m[2];
    // Waxing when the Moon is east of the Sun (ecliptic longitude ahead).
    var cross = s[0] * m[1] - s[1] * m[0];
    return {
      t: t, sun: subPoint(s, t), moon: subPoint(m, t),
      moonLit: (1 - cosE) / 2, waxing: cross > 0,
      gmst: gmst(t)
    };
  }

  /* World unit vector for a lon/lat, in the stage's convention:
   * x = east at lon 90, y = north pole, z = lon 0 on the equator. */
  function vec(lon, lat) {
    var la = lat * DEG, lo = lon * DEG;
    return [Math.cos(la) * Math.sin(lo), Math.sin(la), Math.cos(la) * Math.cos(lo)];
  }

  /* ── Weather sources ─────────────────────────────────── */
  var IR_W = 2048, IR_H = 1024, RAIN_W = 1024, RAIN_H = 512;

  function iso(t) { return new Date(t).toISOString().replace(/\.\d{3}Z$/, 'Z'); }

  function timed(url, ms) {
    var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timer = setTimeout(function () { ctrl && ctrl.abort(); }, ms || 15000);
    return fetch(url, ctrl ? { signal: ctrl.signal, mode: 'cors' } : { mode: 'cors' })
      .then(function (r) { clearTimeout(timer); if (!r.ok) throw new Error(r.status + ' ' + url); return r; },
            function (e) { clearTimeout(timer); throw e; });
  }

  function pixels(url, w, h) {
    return timed(url).then(function (r) { return r.blob(); })
      .then(function (b) { return createImageBitmap(b); })
      .then(function (img) {
        var c = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(w, h)
          : Object.assign(document.createElement('canvas'), { width: w, height: h });
        var g = c.getContext('2d', { willReadFrequently: true });
        g.drawImage(img, 0, 0, w, h);
        return g.getImageData(0, 0, w, h).data;
      });
  }

  /* A layer's newest time, from its own capabilities document. */
  function capsTime(url, what) {
    return timed(url).then(function (r) { return r.text(); }).then(function (x) {
      var m = /<Dimension[^>]*name="time"[^>]*default="([^"]+)"/.exec(x) ||
              /default="(\d{4}-\d\d-\d\dT[\d:.]+Z)"/.exec(x);
      if (!m) throw new Error(what + ' capabilities: no time');
      return Date.parse(m[1]);
    });
  }

  function grey(px) {
    var n = px.length >> 2, g = new Uint8Array(n);
    for (var i = 0; i < n; i++) g[i] = px[i * 4];
    return g;
  }

  function irUrl(cfg, t) {
    return cfg.irMap + '&width=' + IR_W + '&height=' + IR_H + '&time=' + iso(t);
  }

  /* Cloud cover and top height from one styled IR frame.
   *
   * The grey is brighter where the scene is colder. Clear ground is not one
   * grey - the tropics are near 64, a winter ocean near 110, Antarctica near
   * 180 - so cloud is judged against the clear ground around it: the 12th
   * percentile of each 64 x 16 block, the smallest of its neighbours (so a
   * block that is all cloud borrows a clear neighbour), capped by what the
   * clearest part of that latitude looks like. Cover is the contrast above
   * that; height is the same contrast measured against the coldest grey. */
  function irField(g) {
    var W = IR_W, H = IR_H, BW = 64, BH = 16, GX = W / BW, GY = H / BH;
    var x, y;
    // Clearest grey per latitude row band (5th percentile), for the cap.
    var rowCap = new Float32Array(GY), hist = new Uint32Array(256);
    for (var by = 0; by < GY; by++) {
      hist.fill(0); var n = 0;
      for (y = by * BH; y < (by + 1) * BH; y += 2) for (x = 0; x < W; x += 4) { hist[g[y * W + x]]++; n++; }
      var k = 0, acc = 0; while (k < 255 && acc + hist[k] < n * 0.05) acc += hist[k++];
      rowCap[by] = k * 1.1 + 10;
    }
    var blk = new Float32Array(GX * GY);
    for (by = 0; by < GY; by++) for (var bx = 0; bx < GX; bx++) {
      hist.fill(0); n = 0;
      for (y = by * BH; y < (by + 1) * BH; y += 2) for (x = bx * BW; x < (bx + 1) * BW; x += 2) { hist[g[y * W + x]]++; n++; }
      k = 0; acc = 0; while (k < 255 && acc + hist[k] < n * 0.12) acc += hist[k++];
      blk[by * GX + bx] = k;
    }
    var bg = new Float32Array(GX * GY);
    for (by = 0; by < GY; by++) for (bx = 0; bx < GX; bx++) {
      var m = 255;
      for (var dy = -1; dy <= 1; dy++) for (var dx = -2; dx <= 2; dx++) {
        var yy = Math.max(0, Math.min(GY - 1, by + dy)), xx = (bx + dx + GX) % GX;
        m = Math.min(m, blk[yy * GX + xx]);
      }
      bg[by * GX + bx] = Math.min(m, rowCap[by]);
    }
    var out = new Uint8Array(W * H * 2);
    for (y = 0; y < H; y++) {
      var fy = Math.max(0, Math.min(GY - 1.001, (y + 0.5) / BH - 0.5)), y0 = Math.floor(fy), ty = fy - y0;
      var lat = 90 - (y + 0.5) / H * 180, al = Math.abs(lat);
      // No data poleward of about 80 deg (the mosaic is white there).
      var polar = al < 70 ? 1 : al > 80 ? 0 : (80 - al) / 10;
      for (x = 0; x < W; x++) {
        var fx = (x + 0.5) / BW - 0.5, x0 = Math.floor(fx), tx = fx - x0;
        var xa = (x0 + GX) % GX, xb = (x0 + 1) % GX;
        var b = (bg[y0 * GX + xa] * (1 - tx) + bg[y0 * GX + xb] * tx) * (1 - ty) +
                (bg[(y0 + 1) * GX + xa] * (1 - tx) + bg[(y0 + 1) * GX + xb] * tx) * ty;
        var v = g[y * W + x], c = v - b;
        var cov = Math.max(0, Math.min(1, (c - 10) / 62));
        cov = cov * cov * (3 - 2 * cov) * polar;
        var top = Math.max(0, Math.min(1, (v - b - 4) / Math.max(40, 246 - b)));
        out[(y * W + x) * 2] = cov * 255;
        out[(y * W + x) * 2 + 1] = top * 255;
      }
    }
    return out;
  }

  /* IMERG colours -> rate. GIBS paints the half-hourly rate with a
   * 110-step palette per phase (rain, snow), step i meaning 0.1 * 10^(i/40)
   * mm/h (read from GPM_Precipitation_Rate.xml). Stored as (i + 1) / 110 so
   * zero means dry; the second channel says snow. */
  var PAL_RAIN = '00764e00784b007b48007e4500814200833e00863a008936008c32008e2e00912900942400971f00991a009c14009f0f00a20900a50303a70009aa0010ad0017b0001eb20026b5002db80035bb003dbd0045c0004ec30056c6005fc80068cb0072ce007bd10085d4008fd60099d900a3dc00aedf00b8e100c3e400cfe700daea00e6ec00efed00f2e600f5e000f7d900fad200fdca00ffc201ffb904ffb006ffa809ffa00cff980fff9011ff8814ff8117ff7a1aff731cff6c1fff6522ff5f25ff5927ff532aff4d2dff4730ff4233ff342cff2626ff2020ff1a1aff1313ff0d0dff0707ff0101fa0000f30000ed0000e70000e10000da0000d40000ce0000c80000c20000bb0000b50000af0000a90000a200009c00009600009000008a00008300007d00007700007100006a00006400005e00005800005200004b00004500003f0000390000330000';
  var PAL_SNOW = 'b1faeeaefaefaaf9efa7f8f0a4f8f1a1f7f29ef6f39bf5f498f4f495f1f392eef28febf18ce7f089e4ef86e0ee84dded81d9ec7ed5ea7cd1e979cde876c9e674c5e571c1e46fbde26db8e16ab4df68b0dd66abdc63a7da61a2d85f9ed75d99d55b95d35990d1578ccf5587cd5383cb517ec94f7ac74d75c54f73c44d70c54c6dc64b6ac74a66c94963ca485fcb465ccc4558cd4454ce4350d0424cd14048d23f44d33e40d43f3dd5413cd7433bd84539d94838da4a37db4d36dc5035de5334df5632e05931e15c30e25f2fe4632ee5662de66a2be76a25e86a1fe86b19e96d14e96f12e57010e1720fdd740dd9750cd5770bd1780acc790ac77a0ac17a09bc7b09b77b09b27b09ac7a08a77a08a279089d78089877079276078d74078872068370067d6e06786c067369056e6605696305635e045d59045654044f4f044849034244033c3f03363a0330';
  var palette = null;
  function pal() {
    if (palette) return palette;
    palette = new Map();
    [[PAL_RAIN, 0], [PAL_SNOW, 1]].forEach(function (p) {
      for (var i = 0; i * 6 < p[0].length; i++) {
        var rgb = parseInt(p[0].substr(i * 6, 6), 16);
        if (!palette.has(rgb)) palette.set(rgb, [i, p[1]]);
      }
    });
    return palette;
  }

  function rainField(px) {
    var P = pal(), n = RAIN_W * RAIN_H, out = new Uint8Array(n * 2), keys = Array.from(P.keys());
    var memo = new Map();
    for (var i = 0; i < n; i++) {
      if (px[i * 4 + 3] < 128) continue;
      var rgb = (px[i * 4] << 16) | (px[i * 4 + 1] << 8) | px[i * 4 + 2];
      var e = P.get(rgb) || memo.get(rgb);
      if (!e) {
        // Resampling can blend two palette steps: take the nearest colour.
        var best = 1e9;
        for (var k = 0; k < keys.length; k++) {
          var q = keys[k], dr = (q >> 16) - px[i * 4], dg = ((q >> 8) & 255) - px[i * 4 + 1], db = (q & 255) - px[i * 4 + 2];
          var d = dr * dr + dg * dg + db * db;
          if (d < best) { best = d; e = P.get(q); }
        }
        memo.set(rgb, e);
      }
      out[i * 2] = Math.round((e[0] + 1) / 110 * 255);
      out[i * 2 + 1] = e[1] ? 255 : 0;
    }
    return out;
  }

  /* IMERG's newest half hour, from GIBS's DescribeDomains for the layer. */
  function rainLatest(cfg, now) {
    var d0 = iso(now - 2 * 86400000).slice(0, 10), d1 = iso(now + 86400000).slice(0, 10);
    return timed(cfg.rainDomain.replace('{range}', d0 + '--' + d1)).then(function (r) { return r.text(); }).then(function (x) {
      var m = /<Domain>([^<]+)<\/Domain>/.exec(x);
      if (!m) throw new Error('IMERG domain: none');
      var last = m[1].split(',').pop().split('/'), end = last.length > 1 ? last[1] : last[0];
      if (!/T/.test(end)) end += 'T00:00:00Z';
      return Date.parse(end);
    });
  }

  function rainUrl(cfg, t) {
    return cfg.rainMap + '&WIDTH=' + RAIN_W + '&HEIGHT=' + RAIN_H + '&TIME=' + iso(t);
  }

  /* ── Fresh Meteosat (MTG 0 degree) over its own disk ─────
   *
   * The world mosaic is 1-3 h old; the lightning is minutes old. Inside the
   * Meteosat disk the same satellite publishes a 10.5 um IR frame and the
   * H SAF h40b rain estimate (IR calibrated by microwave overpasses) every
   * 10 minutes, on the same +-70 degree box as the lightning. Both are laid
   * over the world fields with a soft edge by view angle: full weight within
   * 60 degrees of the sub-satellite point (0, 0), none past 72, where the
   * disk is too oblique to be worth more than the mosaic. */
  var MTG_W = 800, MTG_RW = 400;

  function mtgWeight(lon, lat) {
    var c = Math.cos(lat * DEG) * Math.cos(lon * DEG);
    var d = Math.acos(Math.max(-1, Math.min(1, c))) / DEG;
    var w = Math.max(0, Math.min(1, (72 - d) / 12));
    return w * w * (3 - 2 * w);
  }

  function mtgUrl(base, w, t) { return base + '&width=' + w + '&height=' + w + '&time=' + iso(t); }

  /* A frame that is all transparent is a frame not yet filled in: step back
   * one 10-minute slot, a few times. */
  function mtgFrame(base, w, t, tries) {
    return pixels(mtgUrl(base, w, t), w, w).then(function (p) {
      for (var i = 3; i < p.length; i += 4) if (p[i] >= 128) return { t: t, px: p, w: w };
      if (tries <= 1) throw new Error('Meteosat: empty frames');
      return mtgFrame(base, w, t - 600000, tries - 1);
    });
  }

  /* Paste the fresh IR grey into the mosaic's grey. The two services style
   * their greys differently, so the fresh one is first mapped onto the
   * mosaic's scale by matching their distributions where both see the same
   * ground (quantile matching; the weather moved in between, but not the
   * climate of a 140-degree box). Cover is then read from the merged grey by
   * the one irField, so the seam needs no second calibration. */
  function mergeIr(g, f, box) {
    var W = IR_W, H = IR_H, FW = f.w, px = f.px, x, y, i;
    var dLon = (box[2] - box[0]) / FW, dLat = (box[3] - box[1]) / FW;
    var src = new Int32Array(W * H).fill(-1), wt = new Float32Array(W * H);
    var hg = new Float64Array(256), hf = new Float64Array(256);
    for (y = 0; y < H; y++) {
      var lat = 90 - (y + 0.5) / H * 180, fy = Math.floor((box[3] - lat) / dLat);
      if (fy < 0 || fy >= FW) continue;
      for (x = 0; x < W; x++) {
        var lon = (x + 0.5) / W * 360 - 180, fx = Math.floor((lon - box[0]) / dLon);
        if (fx < 0 || fx >= FW) continue;
        var o = fy * FW + fx, w = mtgWeight(lon, lat);
        if (w <= 0 || px[o * 4 + 3] < 128) continue;
        i = y * W + x; src[i] = px[o * 4]; wt[i] = w;
        if (w > 0.5) { hg[g[i]]++; hf[px[o * 4]]++; }
      }
    }
    var lut = new Uint8Array(256), ng = 0, nf = 0, k;
    for (k = 0; k < 256; k++) { ng += hg[k]; nf += hf[k]; }
    if (nf < 1000) return g;
    var cg = 0, cf = 0, j = 0;
    for (k = 0; k < 256; k++) {
      cf += hf[k];
      var q = (cf - hf[k] / 2) / nf;
      while (j < 255 && (cg + hg[j]) / ng < q) cg += hg[j++];
      lut[k] = j;
    }
    var out = new Uint8Array(g);
    for (i = 0; i < W * H; i++) if (src[i] >= 0) out[i] = Math.round(g[i] * (1 - wt[i]) + lut[src[i]] * wt[i]);
    return out;
  }

  /* h40b's colour classes (its legend, type "intervals": each colour is the
   * range up to its quantity), as one representative rate per class. */
  var H40B = [[0xccffcc, 0.7], [0x99e699, 3], [0x66cc66, 5.5], [0x33b333, 8.5], [0x3399cc, 12.5], [0x3366ff, 17.5],
              [0x0000ff, 22.5], [0x6600cc, 27.5], [0x9933cc, 35], [0xcc0099, 45], [0x800080, 60]];
  function h40bRate(r, g, b) {
    var best = 1e9, v = 0;
    for (var k = 0; k < H40B.length; k++) {
      var q = H40B[k][0], dr = (q >> 16) - r, dg = ((q >> 8) & 255) - g, db = (q & 255) - b, d = dr * dr + dg * dg + db * db;
      if (d < best) { best = d; v = H40B[k][1]; }
    }
    return v;
  }
  // Rate -> the rain field's code, (i + 1) / 110 with rate 0.1 * 10^(i/40).
  function rainCode(mm) {
    if (mm <= 0) return 0;
    var i = Math.max(0, Math.min(109, Math.round(40 * Math.log10(mm / 0.1))));
    return Math.round((i + 1) / 110 * 255);
  }

  /* Lay h40b over IMERG inside the disk. Transparent there means dry. */
  function mergeRain(d, f, box) {
    var W = RAIN_W, H = RAIN_H, FW = f.w, px = f.px, memo = new Map();
    var dLon = (box[2] - box[0]) / FW, dLat = (box[3] - box[1]) / FW;
    var out = new Uint8Array(d);
    for (var y = 0; y < H; y++) {
      var lat = 90 - (y + 0.5) / H * 180, fy = Math.floor((box[3] - lat) / dLat);
      if (fy < 0 || fy >= FW) continue;
      for (var x = 0; x < W; x++) {
        var lon = (x + 0.5) / W * 360 - 180, fx = Math.floor((lon - box[0]) / dLon);
        if (fx < 0 || fx >= FW) continue;
        var w = mtgWeight(lon, lat);
        if (w <= 0) continue;
        var o = (fy * FW + fx) * 4, c = 0;
        if (px[o + 3] >= 128) {
          var key = (px[o] << 16) | (px[o + 1] << 8) | px[o + 2];
          c = memo.get(key);
          if (c === undefined) memo.set(key, c = rainCode(h40bRate(px[o], px[o + 1], px[o + 2])));
        }
        var i = (y * W + x) * 2;
        out[i] = Math.round(d[i] * (1 - w) + c * w);
        if (w > 0.5) out[i + 1] = 0;   // h40b does not separate snow
      }
    }
    return out;
  }

  /* Rain falls from cloud: fade it out where the cloud field says clear
   * (cover under ~25%). Inside the disk the two agree by construction; out
   * of it, this hides where IMERG's older rain no longer has its cloud. */
  function maskRain(d, cov) {
    var W = RAIN_W, H = RAIN_H, sx = IR_W / W, sy = IR_H / H;
    for (var y = 0; y < H; y++) for (var x = 0; x < W; x++) {
      var m = 0;
      for (var j = 0; j < sy; j++) for (var k = 0; k < sx; k++) m = Math.max(m, cov[((y * sy + j) * IR_W + x * sx + k) * 2]);
      var t = Math.max(0, Math.min(1, (m / 255 - 0.15) / 0.2));
      d[(y * W + x) * 2] = Math.round(d[(y * W + x) * 2] * t * t * (3 - 2 * t));
    }
  }

  /* Every layer independently: rain failing never costs the clouds, and
   * Meteosat failing only means the world fields everywhere. */
  function weather(cfg, now) {
    var box = cfg.mtgBox;
    var fresh = capsTime(cfg.mtgIrCaps, 'Meteosat IR').then(function (t) { return mtgFrame(cfg.mtgIrMap, MTG_W, t, 3); });
    // The rain for the cloud frame's own time, so the two line up exactly.
    var freshRain = fresh.then(function (f) { return mtgFrame(cfg.mtgRainMap, MTG_RW, f.t, 3); });
    function soft(p) { return p.catch(function (e) { return { err: String(e) }; }); }
    var ir = Promise.all([capsTime(cfg.irCaps, 'IR'), soft(fresh)]).then(function (r) {
      var t1 = r[0], f = r[1].err ? null : r[1], t0 = t1 - 3 * 3600000;
      return Promise.all([pixels(irUrl(cfg, t1), IR_W, IR_H),
                          f ? null : pixels(irUrl(cfg, t0), IR_W, IR_H).catch(function () { return null; })])
        .then(function (p) {
          var b = irField(f ? mergeIr(grey(p[0]), f, box) : grey(p[0]));
          // With a 10-minute frame in, the 3-hour morph between mosaics
          // would only animate history: show the merged frame still.
          return { w: IR_W, h: IR_H, t: t1, tf: f ? f.t : null, t0: p[1] ? t0 : t1, b: b, a: p[1] ? irField(grey(p[1])) : b,
                   ferr: r[1].err || null };
        });
    });
    /* GIBS sometimes answers a time it lists with a fully transparent image
     * (the tile set is still being filled in, and which request sizes are
     * ready varies). IMERG always has rain somewhere on Earth, so an empty
     * frame is a missing frame: step back half an hour, a few times. */
    function rainFrame(t, tries) {
      return pixels(rainUrl(cfg, t), RAIN_W, RAIN_H).then(function (p) {
        var any = false;
        for (var i = 3; i < p.length; i += 4) if (p[i] >= 128) { any = true; break; }
        if (any) return { w: RAIN_W, h: RAIN_H, t: t, d: rainField(p) };
        if (tries <= 1) throw new Error('IMERG: empty frames');
        return rainFrame(t - 1800000, tries - 1);
      });
    }
    var rain = rainLatest(cfg, now).then(function (t) { return rainFrame(t, 4); });
    return Promise.all([soft(ir), soft(rain), soft(freshRain)]).then(function (r) {
      var I = r[0].err ? null : r[0], R = r[1].err ? null : r[1], F = r[2].err ? null : r[2];
      if (R && F) { R.d = mergeRain(R.d, F, box); R.tf = F.t; }
      if (R && I) maskRain(R.d, I.b);
      var errors = [r[0].err, r[1].err, I && I.ferr].filter(Boolean);
      return { ir: I, rain: R, errors: errors };
    });
  }

  /* ── Lightning: Meteosat's Lightning Imager ─────────────
   *
   * EUMETSAT's LI Accumulated Flash Area (layer li_afa) is published every
   * 5 minutes over the MTG 0-degree disk, cut to a +-70 degree box. Each
   * pixel (2 km FCI grid, served here at 0.1 degree) is painted with how
   * many flashes lit it in those 5 minutes, on a YlOrRd ramp read off the
   * layer's own legend: pale yellow 1, orange 10 (53% along), dark red 20+.
   * A frame is turned into a short list of lit cells, not a texture: a
   * stormy disk lights well under 1% of it, and each cell is animated. */
  var BOLT_W = 1400, BOLT_H = 1400, BOLT_CELL = 3;   // 3 px = 0.3 degree cells
  var BOLT_RAMP = 'ffffe9ffffc9fffbc1fff9bdfff8bcfff7b9fff6b6fff6b5fff4b2fff3affff2aefff1adfff0aaffefa8ffefa6ffeda2ffeda1ffec9fffea9cffea9bffe897ffe793ffe692ffe48ffee38bfee187fedf84fedf83fedd81fedc7dfedb7cfeda79fed877fed775fed674fed471fed26ffed16dfecd69fecc68feca66fec763fec662fec35efec05cfebf5bfebd57feb953feb54ffeb24cfeb14cfeae4afeab48feaa48fea847fea646fea546fda144fda044fd9e43fd9d43fd9b42fd9941fd9740fd953ffd943ffd913dfd8f3dfd8e3cfd893afd8339fd7d37fd7736fd7635fd7234fc6c33fc6b33fc6631fc6330fc612ffc602ffc5c2efc5a2dfc572cfc562bfc522afc512afc4f2afb4c29f94728f84628f74427f64227f53e26f43d25f23824f03423ed3021eb2b20ea2a20e8261fe6221de6211de41d1ce21b1ce1191ce0181cde171ddd161ddb141dda141dd8121ed7121ed5101fd40f1fd10d20d00d20cf0c21cd0b21ca0922c90822c60623c30324c00224bc0025bb0025b70026b20026b00026ac0026a90026a70026a60026a100269f00269c00269b00269700269600269400269100268c00268b002686001d';
  var boltRamp = null, boltMemo = new Map(), boltCache = new Map();
  function flashes(r, g, b) {
    var key = (r << 16) | (g << 8) | b, v = boltMemo.get(key);
    if (v !== undefined) return v;
    if (!boltRamp) {
      boltRamp = [];
      for (var i = 0; i * 6 < BOLT_RAMP.length; i++) boltRamp.push(parseInt(BOLT_RAMP.substr(i * 6, 6), 16));
    }
    var best = 1e9, bi = 0;
    for (var k = 0; k < boltRamp.length; k++) {
      var q = boltRamp[k], dr = (q >> 16) - r, dg = ((q >> 8) & 255) - g, db = (q & 255) - b, d = dr * dr + dg * dg + db * db;
      if (d < best) { best = d; bi = k; }
    }
    var p = bi / (boltRamp.length - 1);
    v = p <= 0.528 ? 1 + 9 * p / 0.528 : 10 + 10 * (p - 0.528) / 0.472;
    boltMemo.set(key, v);
    return v;
  }

  function boltField(px, box) {
    var W = BOLT_W, H = BOLT_H, C = BOLT_CELL, GX = Math.ceil(W / C), cells = new Map();
    var dLon = (box[2] - box[0]) / W, dLat = (box[3] - box[1]) / H;
    for (var y = 0; y < H; y++) for (var x = 0; x < W; x++) {
      var o = (y * W + x) * 4;
      if (px[o + 3] < 128) continue;
      var k = Math.floor(y / C) * GX + Math.floor(x / C), c = cells.get(k), n = flashes(px[o], px[o + 1], px[o + 2]);
      if (!c) cells.set(k, c = { k: k, sx: 0, sy: 0, a: 0, n: 0 });
      c.sx += x; c.sy += y; c.a++; if (n > c.n) c.n = n;
    }
    var out = [];
    cells.forEach(function (c) {
      out.push({ k: c.k, lon: box[0] + (c.sx / c.a + 0.5) * dLon, lat: box[3] - (c.sy / c.a + 0.5) * dLat, n: c.n, a: c.a });
    });
    // The busiest first, so a cap (and the renderer's) keeps the storms' cores.
    out.sort(function (a, b) { return b.n - a.n; });
    return out.slice(0, 6000);
  }

  function boltLatest(cfg) {
    return timed(cfg.boltCaps).then(function (r) { return r.text(); }).then(function (x) {
      var m = /<Dimension[^>]*name="time"[^>]*default="([^"]+)"/.exec(x);
      if (!m) throw new Error('LI capabilities: no time');
      return Date.parse(m[1]);
    });
  }

  function boltFrame(cfg, t) {
    if (boltCache.has(t)) return Promise.resolve(boltCache.get(t));
    return pixels(cfg.boltMap + '&width=' + BOLT_W + '&height=' + BOLT_H + '&time=' + iso(t), BOLT_W, BOLT_H)
      .then(function (p) {
        var f = { t: t, cells: boltField(p, cfg.boltBox) };
        boltCache.set(t, f);
        // Keep only the frames still on screen: the newest three.
        Array.from(boltCache.keys()).sort(function (a, b) { return b - a; }).slice(3)
          .forEach(function (k) { boltCache.delete(k); });
        return f;
      });
  }

  /* The newest 15 minutes: three 5-minute frames, newest first. An older
   * frame that fails is only a shorter history; the newest must load. A
   * refresh fetches just the frames it has not already seen. */
  function bolts(cfg) {
    return boltLatest(cfg).then(function (t) {
      return Promise.all([boltFrame(cfg, t),
        boltFrame(cfg, t - 300000).catch(function () { return null; }),
        boltFrame(cfg, t - 600000).catch(function () { return null; })]);
    }).then(function (fs) {
      return { t: fs[0].t, box: cfg.boltBox, frames: fs.filter(Boolean) };
    });
  }

  /* Lightning near a place: lit cells within km, over every frame held. */
  function boltsNear(b, lon, lat, km) {
    var box = b.box, o = { inView: lon >= box[0] && lon <= box[2] && lat >= box[1] && lat <= box[3], cells: 0, n: 0, km: null };
    if (!o.inView) return o;
    var v = vec(lon, lat), cosR = Math.cos(km / 6371);
    b.frames.forEach(function (f) {
      f.cells.forEach(function (c) {
        var w = vec(c.lon, c.lat), d = v[0] * w[0] + v[1] * w[1] + v[2] * w[2];
        if (d < cosR) return;
        var dk = Math.acos(Math.min(1, d)) * 6371;
        o.cells++; o.n = Math.max(o.n, c.n);
        if (o.km === null || dk < o.km) o.km = dk;
      });
    });
    return o;
  }

  /* Read a field at a lon/lat (for the "over <city>" line). */
  function sample(f, lon, lat, ch, stride) {
    var x = Math.floor((wrap(lon) + 180) / 360 * f.w) % f.w, y = Math.max(0, Math.min(f.h - 1, Math.floor((90 - lat) / 180 * f.h)));
    return f[ch][(y * f.w + x) * stride];
  }
  function at(wx, lon, lat) {
    var o = {};
    if (wx.ir) {
      var s = 0, n = 0;
      for (var dx = -2; dx <= 2; dx++) for (var dy = -2; dy <= 2; dy++) { s += sample(wx.ir, lon + dx * 0.18, lat + dy * 0.18, 'b', 2); n++; }
      o.cloud = s / n / 255;
    }
    if (wx.rain) {
      var i = sample(wx.rain, lon, lat, 'd', 2);
      o.rain = i ? 0.1 * Math.pow(10, (Math.round(i / 255 * 110) - 1) / 40) : 0;
      o.snow = !!(i && wx.rain.d[((Math.max(0, Math.min(wx.rain.h - 1, Math.floor((90 - lat) / 180 * wx.rain.h)))) * wx.rain.w +
        Math.floor((wrap(lon) + 180) / 360 * wx.rain.w) % wx.rain.w) * 2 + 1]);
    }
    return o;
  }

  /* ── The cloud-shaping volume ────────────────────────── */
  /* 64^3, two channels, tileable in all three axes:
   *  R  Perlin-Worley: billows with soft gaps (the cloud body)
   *  G  Worley at three higher frequencies (the edges it erodes)
   * Built in slices across frames so the page never stalls. */
  function noise3d(done) {
    var N = 64, out = new Uint8Array(N * N * N * 2);
    function hash(i) { i = Math.imul(i ^ (i >>> 16), 0x45d9f3b); i = Math.imul(i ^ (i >>> 16), 0x45d9f3b); return ((i ^ (i >>> 16)) >>> 0) / 4294967296; }
    function points(c, seed) {
      var p = new Float32Array(c * c * c * 3);
      for (var i = 0; i < c * c * c; i++) for (var k = 0; k < 3; k++) p[i * 3 + k] = hash(i * 3 + k + seed * 7919);
      return p;
    }
    var oct = [[4, 1], [8, 2], [16, 3], [8, 4], [16, 5], [32, 6]].map(function (o) { return { c: o[0], p: points(o[0], o[1]) }; });
    function worley(o, x, y, z) {
      var c = o.c, X = x * c, Y = y * c, Z = z * c, ix = Math.floor(X), iy = Math.floor(Y), iz = Math.floor(Z), best = 9;
      for (var dz = -1; dz <= 1; dz++) for (var dy = -1; dy <= 1; dy++) for (var dx = -1; dx <= 1; dx++) {
        var cx = ix + dx, cy = iy + dy, cz = iz + dz;
        var j = ((((cz % c) + c) % c) * c + (((cy % c) + c) % c)) * c + (((cx % c) + c) % c);
        var px = cx + o.p[j * 3] - X, py = cy + o.p[j * 3 + 1] - Y, pz = cz + o.p[j * 3 + 2] - Z;
        var d = px * px + py * py + pz * pz; if (d < best) best = d;
      }
      return 1 - Math.min(1, Math.sqrt(best));
    }
    // Tileable gradient noise (period P lattice cells over the unit cube).
    function grad(ix, iy, iz, P, s) {
      var h = hash(((((iz % P) + P) % P) * P + (((iy % P) + P) % P)) * P + (((ix % P) + P) % P) + s * 131) * 12 | 0;
      var G = [[1,1,0],[-1,1,0],[1,-1,0],[-1,-1,0],[1,0,1],[-1,0,1],[1,0,-1],[-1,0,-1],[0,1,1],[0,-1,1],[0,1,-1],[0,-1,-1]];
      return G[h];
    }
    function perlin(x, y, z, P, s) {
      var X = x * P, Y = y * P, Z = z * P, ix = Math.floor(X), iy = Math.floor(Y), iz = Math.floor(Z);
      var fx = X - ix, fy = Y - iy, fz = Z - iz;
      function f(t) { return t * t * t * (t * (t * 6 - 15) + 10); }
      var u = f(fx), v = f(fy), w = f(fz), r = 0;
      for (var k = 0; k < 8; k++) {
        var a = k & 1, b = (k >> 1) & 1, c = (k >> 2) & 1, g = grad(ix + a, iy + b, iz + c, P, s);
        var d = g[0] * (fx - a) + g[1] * (fy - b) + g[2] * (fz - c);
        r += d * (a ? u : 1 - u) * (b ? v : 1 - v) * (c ? w : 1 - w);
      }
      return r;
    }
    var z = 0;
    (function slice() {
      var until = performance.now() + 12;
      while (z < N && performance.now() < until) {
        for (var y = 0; y < N; y++) for (var x = 0; x < N; x++) {
          var X = (x + 0.5) / N, Y = (y + 0.5) / N, Z = (z + 0.5) / N;
          var wf = worley(oct[0], X, Y, Z) * 0.625 + worley(oct[1], X, Y, Z) * 0.25 + worley(oct[2], X, Y, Z) * 0.125;
          var pf = perlin(X, Y, Z, 4, 1) + perlin(X, Y, Z, 8, 2) * 0.5 + perlin(X, Y, Z, 16, 3) * 0.25;
          pf = Math.max(0, Math.min(1, pf * 0.7 + 0.5));
          // Perlin-Worley: Perlin remapped by the Worley field.
          var pw = Math.max(0, Math.min(1, (pf - (wf - 1)) / (1 - (wf - 1)) - 0.35) / 0.65);
          var det = worley(oct[3], X, Y, Z) * 0.625 + worley(oct[4], X, Y, Z) * 0.25 + worley(oct[5], X, Y, Z) * 0.125;
          var o = ((z * N + y) * N + x) * 2;
          out[o] = Math.max(0, Math.min(1, pw)) * 255;
          out[o + 1] = det * 255;
        }
        z++;
      }
      if (z < N) setTimeout(slice, 0); else done(out, N);
    })();
  }

  global.AtlasSky = { bodies: bodies, vec: vec, gmst: gmst, weather: weather, at: at, bolts: bolts, boltsNear: boltsNear, noise3d: noise3d, iso: iso };
})(window);
