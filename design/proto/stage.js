/* Atlas Noir prototype - the stage.
 *
 * A WebGL2 port of the relief shader in src/web/globe.js, drawn full-viewport
 * behind the page. Same textures, same byte encoding (sea level 128, square
 * root above, power 1/0.65 below), same material idea (textures hold metres,
 * vegetation, snow and coast distance; colours come from CSS tokens), same
 * north-west terrain light. What changes is where it runs: on the GPU, so the
 * camera can fly between states at frame rate instead of repainting on the CPU.
 *
 * Relief at the horizon (as on main, src/web/globe.js:146-207): land is given
 * real height near the limb with the same law - 20x at the summits, a square
 * root below, a 100x ceiling on low ground - so mountains break the outline.
 * On the GPU this is an honest ray march against the displaced sphere, which
 * gives the silhouette and the parallax near the edge from one test.
 *
 * Prototype scope: no macro-field blur (a mip level stands in for it), no
 * clustering. Markers are an SVG layer projected with
 * the same orthographic maths, so they stay focusable and labelled.
 */
(function (global) {
  'use strict';

  var DEG = Math.PI / 180;

  var VS = '#version 300 es\n' +
    'in vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }';

  var FS = [
    '#version 300 es',
    'precision highp float;',
    'uniform sampler2D uElev, uBio;',
    'uniform vec2 uRes, uCtr, uTex;',          // viewport px, globe centre px (GL y-up), elev size
    'uniform float uR, uLon0, uLat0, uDim, uHasTex;',
    'uniform vec3 cAbyss, cShelf, cForest, cDesert, cRock, cIce, cShore, cAtmo, cGround, cLand;',
    'uniform vec4 uLight;',                     // ambient, contrast, seaAmbient, sun
    'uniform vec3 uDisp;',                      // per metre, per root-metre, tallest (radius fractions)
    'out vec4 o;',
    'const float PI = 3.14159265;',
    'float metres(float c){',
    '  c *= 255.0;',
    '  if (c > 128.0){ float t = (c - 128.0) / 127.0; return t * t * 8500.0; }',
    '  float d = (128.0 - c) / 127.0; return -pow(d, 1.0 / 0.65) * 9000.0;',
    '}',
    'float hAt(vec2 uv, float lod){ return metres(textureLod(uElev, uv, lod).r); }',
    'float hash(vec2 q){ return fract(sin(dot(q, vec2(12.9898, 78.233))) * 43758.5453); }',
    // Only land is displaced; the sea stays on the sphere.
    'float disp(float h){ h = max(h, 0.0); return min(h * uDisp.x, sqrt(h) * uDisp.y); }',
    'vec2 uvOf(vec3 n, out float lat){',
    '  float sl = sin(uLat0), cl = cos(uLat0);',
    '  lat = asin(clamp(n.y * cl + n.z * sl, -1.0, 1.0));',
    '  float lon = uLon0 + atan(n.x, n.z * cl - n.y * sl);',
    '  return vec2(fract(lon / (2.0 * PI) + 0.5), 0.5 - lat / PI);',
    '}',
    // Radial gap between a point on the view ray and the terrain under it.
    'float gapAt(vec3 p, out vec3 n){',
    '  float r = length(p); n = p / r; float la;',
    '  return r - 1.0 - disp(hAt(uvOf(n, la), 0.0));',
    '}',
    'void main(){',
    '  vec2 q = (gl_FragCoord.xy - uCtr) / uR;',
    '  float rho2 = dot(q, q), rho = sqrt(rho2);',
    // Space is transparent: the star sky is its own layer underneath. Only the
    // thin atmosphere is drawn here, as premultiplied light with alpha 0, so
    // it adds to the stars instead of covering them.
    '  float a = max(rho - 1.0, 0.0);',
    '  float halo = exp(-a * 70.0) * 0.28 + exp(-a * 14.0) * 0.045;',
    '  vec3 bg = cAtmo * halo * (1.0 - uDim * 0.5);',
    '  float top = uHasTex > 0.5 ? uDisp.z : 0.0, RM = 1.0 + top;',
    '  if (rho2 >= RM * RM){ o = vec4(bg, 0.0); return; }',
    // March the view ray (orthographic, along -z) through the relief shell.
    '  float zT = sqrt(RM * RM - rho2);',
    '  float zB = rho2 < 1.0 ? sqrt(1.0 - rho2) : -zT;',
    '  vec3 n = vec3(q, max(zB, 0.0)), nb;',
    '  float cov = 0.0, best = 1e9;',
    '  if (top <= 0.0){ cov = step(rho2, 1.0); }',
    '  else {',
    '    int N = rho < 0.8 ? 8 : 28;',
    '    float zp = zT;',
    '    for (int i = 1; i <= 28; i++){',
    '      if (i > N) break;',
    '      float z = mix(zT, zB, float(i) / float(N));',
    '      float g = gapAt(vec3(q, z), nb);',
    '      if (g <= 1e-5){',
    '        float lo = zp, hi = z;',
    '        for (int j = 0; j < 5; j++){',
    '          float m = 0.5 * (lo + hi);',
    '          if (gapAt(vec3(q, m), nb) <= 1e-5) hi = m; else lo = m;',
    '        }',
    '        gapAt(vec3(q, hi), n); cov = 1.0; break;',
    '      }',
    '      if (g < best){ best = g; n = nb; }',
    '      zp = z;',
    '    }',
    // A near miss is partial coverage: antialiasing for the silhouette.
    '    if (cov < 1.0) cov = 1.0 - smoothstep(0.0, 1.2 / uR, best);',
    '  }',
    '  if (cov <= 0.0){ o = vec4(bg, 0.0); return; }',
    '  float lat; vec2 uv = uvOf(n, lat);',
    '  vec3 col;',
    '  float sph = max(dot(n, normalize(vec3(-0.42, 0.46, 0.78))), 0.0);',
    '  float sunBase = 1.0 - uLight.w * 0.72;',
    '  if (uHasTex < 0.5){',
    '    col = mix(cAbyss, cShelf, 0.35) * (0.55 + 0.45 * sph);',
    '  } else {',
    '    vec2 px = 1.0 / uTex;',
    '    float h = hAt(uv, 0.0);',
    '    bool land = h > 0.0;',
    '    float cosLat = max(cos(lat), 0.05);',
    '    float mx = 40075017.0 * cosLat / uTex.x, my = 20003931.0 / uTex.y;',
    '    // Fine field at full resolution plus a macro field from a coarse mip:',
    '    // the massif gives the mass, the fine field gives the edges.',
    '    float ex = land ? 14.0 : 0.08;',
    '    float gx = (hAt(uv + vec2(px.x, 0.0), 0.0) - hAt(uv - vec2(px.x, 0.0), 0.0)) / (2.0 * mx);',
    '    float gy = (hAt(uv - vec2(0.0, px.y), 0.0) - hAt(uv + vec2(0.0, px.y), 0.0)) / (2.0 * my);',
    '    float Gx = (hAt(uv + vec2(px.x * 6.0, 0.0), 2.6) - hAt(uv - vec2(px.x * 6.0, 0.0), 2.6)) / (12.0 * mx);',
    '    float Gy = (hAt(uv - vec2(0.0, px.y * 6.0), 2.6) - hAt(uv + vec2(0.0, px.y * 6.0), 2.6)) / (12.0 * my);',
    '    vec3 L = normalize(vec3(-0.60, 0.58, 0.55));',
    '    vec3 nF = normalize(vec3(-gx * ex, -gy * ex, 1.0));',
    '    vec3 nM = normalize(vec3(-Gx * (land ? 90.0 : 0.5), -Gy * (land ? 90.0 : 0.5), 1.0));',
    '    float hill = mix(dot(nF, L), dot(nM, L), 0.62) / L.z;',
    '    vec3 bio = texture(uBio, uv).rgb;',
    '    float steep = 1.0 - 1.0 / length(vec3(gx * ex, gy * ex, 1.0));',
    '    if (land){',
    '      float alt = smoothstep(1600.0, 4200.0, h);',
    '      float bare = min(alt * 0.55 + steep * 0.60, 0.90);',
    '      col = mix(cDesert, cForest, bio.r);',
    '      col = mix(col, cRock, bare);',
    '      float line = 5600.0 - 5300.0 * pow(abs(sin(lat)), 2.2);',
    '      float cap = clamp((h - line) / 2200.0, 0.0, 1.0) * (0.72 - 0.34 * steep);',
    '      float sn = max(bio.g * (0.55 + 0.45 * bio.g), cap);',
    '      col = mix(col, cIce, sn);',
    '      col *= (uLight.x + uLight.y * (hill - 1.0) + uLight.y * 0.55) * (sunBase + uLight.w * sph);',
    '    } else {',
    '      float dep = pow(clamp(-h / 9000.0, 0.0, 1.0), 0.468);',
    '      col = mix(cShelf, cAbyss, dep);',
    '      col = mix(col, cIce, step(0.02, bio.g) * bio.g);',
    '      col *= (uLight.z + 0.25 * (hill - 1.0)) * (sunBase + uLight.w * sph * 0.7);',
    '      vec3 hv = normalize(normalize(vec3(-0.42, 0.46, 0.78)) + vec3(0.0, 0.0, 1.0));',
    '      col += pow(max(dot(n, hv), 0.0), 32.0) * 0.35;',
    '      col += cShore * pow(bio.b, 14.0) * 0.45;',
    '    }',
    '  }',
    '  col += cAtmo * pow(1.0 - clamp(n.z, 0.0, 1.0), 5.0) * 0.22;',  // thin limb haze
    '  col = mix(col, cGround, uDim);',                       // stage dims behind reading
    '  o = vec4(col * cov + bg * (1.0 - cov), cov);',
    '}'
  ].join('\n');

  function hex(css) {
    var s = (css || '').trim();
    if (s[0] === '#') {
      if (s.length === 4) s = '#' + s[1] + s[1] + s[2] + s[2] + s[3] + s[3];
      return [parseInt(s.slice(1, 3), 16) / 255, parseInt(s.slice(3, 5), 16) / 255,
              parseInt(s.slice(5, 7), 16) / 255];
    }
    var m = /rgba?\(([^)]+)\)/.exec(s);
    if (m) { var p = m[1].split(/[ ,\/]+/); return [p[0] / 255, p[1] / 255, p[2] / 255]; }
    return [0, 0, 0];
  }

  function ease(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }

  function mulberry(a) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; var t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

  /* The sky: painted once per size into a 2D canvas, never per frame.
   * Stars follow a steep magnitude law (most faint, a few bright) and real
   * colour temperatures (O/B blue-white to K/M orange). A faint Milky Way
   * band with a dust lane crosses the field. Seeded, so every load and every
   * screenshot shows the same sky. Pure decoration: it never carries data
   * and sits under glass cards, so it cannot touch text contrast. */
  var STAR_COL = [[155, 176, 255], [170, 191, 255], [202, 215, 255], [236, 240, 255],
                  [248, 247, 255], [255, 244, 234], [255, 222, 180], [255, 204, 140]];
  var STAR_W = [4, 6, 10, 14, 16, 10, 6, 3];
  function starColour(rnd) {
    var s = 0, i, r = rnd() * 69;
    for (i = 0; i < STAR_W.length; i++) { s += STAR_W[i]; if (r < s) return STAR_COL[i]; }
    return STAR_COL[4];
  }

  function paintSky(cv) {
    var dpr = Math.min(devicePixelRatio || 1, 2), W = cv.clientWidth, H = cv.clientHeight;
    if (!W || !H) return;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    var g = cv.getContext('2d'), rnd = mulberry(20260925), i;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    var D = Math.max(W, H);
    // The band: a gentle arc from lower left to upper right.
    function band(t) { return [W * (-0.1 + 1.2 * t), H * (0.92 - 0.86 * t) + Math.sin(t * 3.1) * H * 0.07]; }
    function blob(x, y, r, rgb, al) {
      var gr = g.createRadialGradient(x, y, 0, x, y, r);
      gr.addColorStop(0, 'rgba(' + rgb + ',' + al + ')'); gr.addColorStop(1, 'rgba(' + rgb + ',0)');
      g.fillStyle = gr; g.fillRect(x - r, y - r, 2 * r, 2 * r);
    }
    g.globalCompositeOperation = 'lighter';
    // Milky Way glow: many soft overlapping clouds along the spine, a bright
    // core near the middle, blue-white with violet and a warm core tint.
    for (i = 0; i < 70; i++) {
      var t = rnd(), p = band(t), k = rnd(), core = Math.exp(-Math.pow((t - 0.55) / 0.22, 2));
      blob(p[0] + (rnd() - .5) * D * .08, p[1] + (rnd() - .5) * D * .08, (0.05 + rnd() * 0.13) * D,
           k < 0.55 ? '90,140,255' : k < 0.82 ? '160,110,255' : '255,180,130',
           (0.022 + rnd() * 0.03 + core * 0.035).toFixed(3));
    }
    // Two faint nebulae well away from the band, for colour.
    blob(W * 0.12, H * 0.2, D * 0.2, '40,190,255', 0.05); blob(W * 0.16, H * 0.24, D * 0.1, '120,90,255', 0.05);
    blob(W * 0.9, H * 0.82, D * 0.18, '255,80,160', 0.035); blob(W * 0.86, H * 0.78, D * 0.08, '255,140,120', 0.04);
    // Dust lane: carve a darker, broken streak just off the band's spine.
    g.globalCompositeOperation = 'destination-out';
    for (i = 0; i < 60; i++) {
      var tt = rnd(), q = band(tt);
      blob(q[0] + D * 0.01, q[1] + D * 0.016, (0.012 + rnd() * 0.035) * D, '0,0,0', (0.3 + rnd() * 0.35).toFixed(2));
    }
    g.globalCompositeOperation = 'lighter';
    function star(x, y, m) {
      // m in 0..1 is brightness. Even the faintest star is a visible dot
      // (0.55 px radius, 35% alpha); the brightest carry a halo and spikes.
      var c = starColour(rnd), r = 0.55 + m * m * 1.9, al = Math.min(1, 0.35 + m * 0.75);
      if (m > 0.6) blob(x, y, r * 6, c.join(','), (al * 0.28).toFixed(3));
      g.fillStyle = 'rgba(' + c.join(',') + ',' + al.toFixed(3) + ')';
      g.beginPath(); g.arc(x, y, r, 0, 6.2832); g.fill();
      if (m > 0.9) {                            // the brightest few get diffraction spikes
        var L = r * 11;
        [[1, 0], [0, 1]].forEach(function (d) {
          var gr = g.createLinearGradient(x - d[0] * L, y - d[1] * L, x + d[0] * L, y + d[1] * L);
          gr.addColorStop(0, 'rgba(' + c + ',0)'); gr.addColorStop(.5, 'rgba(' + c + ',' + (al * .7).toFixed(2) + ')');
          gr.addColorStop(1, 'rgba(' + c + ',0)');
          g.strokeStyle = gr; g.lineWidth = 0.9; g.beginPath();
          g.moveTo(x - d[0] * L, y - d[1] * L); g.lineTo(x + d[0] * L, y + d[1] * L); g.stroke();
        });
      }
    }
    var nField = Math.round(W * H / 1500), nBand = Math.round(W * H / 450);
    for (i = 0; i < nField; i++) star(rnd() * W, rnd() * H, Math.pow(rnd(), 2.6));
    for (i = 0; i < nBand; i++) {                // band stars: dense, gaussian across the spine
      var b = band(rnd()), gs = (rnd() + rnd() + rnd() - 1.5) * D * 0.055;
      star(b[0] + gs * 0.45, b[1] + gs, Math.pow(rnd(), 3.5) * 0.85);
    }
    g.globalCompositeOperation = 'source-over';
  }

  /* A handful of twinkling stars and a rare meteor, as DOM nodes animated by
   * CSS on the compositor, so the expensive globe never redraws for them. */
  function liveSky(host) {
    var rnd = mulberry(7), i, reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    for (i = 0; i < 60; i++) {
      var s = document.createElement('i'), c = starColour(rnd);
      s.className = 'tw';
      s.style.cssText = 'left:' + (rnd() * 100).toFixed(2) + '%;top:' + (rnd() * 100).toFixed(2) + '%;' +
        '--c:rgb(' + c + ');--s:' + (1.8 + rnd() * 2.2).toFixed(1) + 'px;' +
        'animation-duration:' + (2.8 + rnd() * 4.5).toFixed(2) + 's;animation-delay:-' + (rnd() * 7).toFixed(2) + 's';
      host.appendChild(s);
    }
    if (reduce) return;
    (function meteor() {
      setTimeout(function () {
        if (!document.hidden) {
          var m = document.createElement('b');
          m.className = 'meteor';
          m.style.left = (15 + Math.random() * 70) + '%';
          m.style.top = (5 + Math.random() * 40) + '%';
          m.style.setProperty('--a', (18 + Math.random() * 22).toFixed(0) + 'deg');
          host.appendChild(m);
          m.addEventListener('animationend', function () { m.remove(); });
        }
        meteor();
      }, 5000 + Math.random() * 10000);
    })();
  }

  function Stage(canvas, svg, opts) {
    this.c = canvas; this.svg = svg; this.opts = opts;
    this.cam = { lon: 26, lat: 30, k: 0.4, cx: 0.68, cy: 0.52, dim: 0 };
    this.from = null; this.to = null; this.t0 = 0; this.dur = 900;
    this.spin = 0; this.dirty = true; this.markers = [];
    this.sky = opts.sky || null;
    if (this.sky) {
      var skyCv = this.sky.querySelector('canvas'), rt = 0;
      paintSky(skyCv); liveSky(this.sky);
      addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(function () { paintSky(skyCv); }, 150); });
    }
    var gl = this.gl = canvas.getContext('webgl2', { antialias: true, alpha: true, premultipliedAlpha: true });
    if (!gl) { this.failed = true; return; }
    this.prog = this._program();
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(this.prog, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    this.u = {};
    var self = this;
    ['uElev', 'uBio', 'uRes', 'uCtr', 'uTex', 'uR', 'uLon0', 'uLat0', 'uDim', 'uHasTex', 'uLight', 'uDisp',
     'cAbyss', 'cShelf', 'cForest', 'cDesert', 'cRock', 'cIce', 'cShore', 'cAtmo', 'cGround', 'cLand']
      .forEach(function (n) { self.u[n] = gl.getUniformLocation(self.prog, n); });
    // The main globe's displacement law (src/web/globe.js:193-207).
    var RE = 6371000, TOP_M = 8500;
    gl.uniform3f(this.u.uDisp, 100 / RE, 20 * Math.sqrt(TOP_M) / RE, 20 * TOP_M / RE);
    this.hasTex = 0;
    this._loadTextures();
    this.readTheme();
    this._bindDrag();
    canvas.addEventListener('webglcontextlost', function (e) { e.preventDefault(); self.failed = true; self.opts.onFail && self.opts.onFail(); });
    requestAnimationFrame(function f(now) { self._frame(now); requestAnimationFrame(f); });
  }

  Stage.prototype._program = function () {
    var gl = this.gl;
    function sh(type, src) {
      var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    }
    var p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, VS));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    gl.useProgram(p);
    return p;
  };

  Stage.prototype._loadTextures = function () {
    var gl = this.gl, self = this;
    function load(url, unit, mip) {
      return fetch(url).then(function (r) { return r.blob(); })
        .then(function (b) { return createImageBitmap(b, { colorSpaceConversion: 'none', premultiplyAlpha: 'none' }); })
        .then(function (img) {
          var t = gl.createTexture();
          gl.activeTexture(gl.TEXTURE0 + unit);
          gl.bindTexture(gl.TEXTURE_2D, t);
          gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, img);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
          if (mip) { gl.generateMipmap(gl.TEXTURE_2D); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR); }
          else gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
          return img;
        });
    }
    Promise.all([load(this.opts.elev, 0, true), load(this.opts.biome, 1, false)]).then(function (imgs) {
      gl.useProgram(self.prog);
      gl.uniform1i(self.u.uElev, 0); gl.uniform1i(self.u.uBio, 1);
      gl.uniform2f(self.u.uTex, imgs[0].width, imgs[0].height);
      self.hasTex = 1; self.dirty = true;
      self.opts.onReady && self.opts.onReady();
    }).catch(function () { self.opts.onReady && self.opts.onReady(); });
  };

  /* Materials and lighting are CSS custom properties, exactly as in the
   * site's globe: the renderer knows no theme. */
  Stage.prototype.readTheme = function () {
    if (this.failed) return;
    var cs = getComputedStyle(document.documentElement), gl = this.gl, u = this.u;
    function v(n) { return cs.getPropertyValue(n); }
    gl.useProgram(this.prog);
    [['cAbyss', '--r-abyss'], ['cShelf', '--r-shelf'], ['cForest', '--r-forest'],
     ['cDesert', '--r-desert'], ['cRock', '--r-rock'], ['cIce', '--r-ice'],
     ['cShore', '--r-shore'], ['cAtmo', '--r-atmo'], ['cGround', '--ground'], ['cLand', '--r-desert']]
      .forEach(function (p) { gl.uniform3fv(u[p[0]], hex(v(p[1]))); });
    gl.uniform4f(u.uLight, +v('--relief-ambient') || 0.42, +v('--relief-contrast') || 0.86,
                 +v('--relief-sea-ambient') || 0.8, +v('--relief-sun') || 0.42);
    this.dirty = true;
  };

  Stage.prototype.setMarkers = function (list) {
    if (list !== this.markers) this._nodes = null;   // rebuild the SVG for a new set
    this.markers = list; this.dirty = true;
  };

  /* Fly to a named camera state. Reduced motion jumps. */
  Stage.prototype.fly = function (state, instant) {
    var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.from = Object.assign({}, this.cam);
    // Take the short way round in longitude.
    var d = ((state.lon - this.from.lon + 540) % 360) - 180;
    this.to = Object.assign({}, state, { lon: this.from.lon + d });
    this.spin = state.spin || 0;
    this.t0 = performance.now();
    this.dur = instant || reduce ? 0 : (state.dur || 1100);
    this.dirty = true;
  };

  /* Drag spins the globe; a press that barely moves is a click, handed to
   * opts.onPick with the nearest marker (if any is within reach). Hovering
   * reports the nearest marker to opts.onHover, so the page can show it. */
  Stage.prototype._bindDrag = function () {
    var self = this, last = null, down = null, moved = 0;
    var hit = this.opts.dragTarget;
    if (!hit) return;
    hit.addEventListener('pointerdown', function (e) {
      down = [e.clientX, e.clientY]; moved = 0;
      if (!self._onGlobe(e.clientX, e.clientY)) return;
      last = [e.clientX, e.clientY];
      hit.setPointerCapture(e.pointerId);
    });
    hit.addEventListener('pointermove', function (e) {
      if (!last) {
        var m = self.nearest(e.clientX, e.clientY, e.pointerType === 'touch' ? 22 : 12);
        hit.classList.toggle('pick', !!m);
        self.opts.onHover && self.opts.onHover(m, e.clientX, e.clientY);
        return;
      }
      moved += Math.abs(e.clientX - last[0]) + Math.abs(e.clientY - last[1]);
      if (moved < 4) return;
      if (!hit.classList.contains('grabbing')) {
        hit.classList.add('grabbing'); self.spin = 0; self.to = null;
        self.opts.onHover && self.opts.onHover(null);
      }
      var R = self._radius();
      self.cam.lon -= (e.clientX - last[0]) / R * 57.3;
      self.cam.lat = Math.max(-80, Math.min(80, self.cam.lat + (e.clientY - last[1]) / R * 57.3));
      last = [e.clientX, e.clientY]; self.dirty = true;
    });
    function up(e) {
      var click = down && moved < 4 && e.type === 'pointerup';
      last = null; down = null; hit.classList.remove('grabbing');
      if (click) {
        var m = self.nearest(e.clientX, e.clientY, e.pointerType === 'touch' ? 22 : 12);
        if (m && self.opts.onPick) self.opts.onPick(m);
      }
    }
    hit.addEventListener('pointerup', up); hit.addEventListener('pointercancel', up);
    hit.addEventListener('pointerleave', function () { hit.classList.remove('pick'); self.opts.onHover && self.opts.onHover(null); });
  };

  /* The visible, pickable marker closest to a screen point, within maxPx. */
  Stage.prototype.nearest = function (x, y, maxPx) {
    var best = null, bd = maxPx * maxPx;
    for (var i = 0; i < this.markers.length; i++) {
      var m = this.markers[i];
      if (!m.slug) continue;
      var p = this.project(m.lon, m.lat);
      if (p[2] <= 0.05) continue;
      var d = (p[0] - x) * (p[0] - x) + (p[1] - y) * (p[1] - y);
      if (d < bd) { bd = d; best = m; }
    }
    return best;
  };

  Stage.prototype._radius = function () {
    return this.cam.k * Math.min(innerWidth, innerHeight);
  };

  Stage.prototype._onGlobe = function (x, y) {
    var R = this._radius(), dx = x - this.cam.cx * innerWidth, dy = y - this.cam.cy * innerHeight;
    return dx * dx + dy * dy < R * R;
  };

  /* Forward orthographic projection - the inverse of the shader's. */
  Stage.prototype.project = function (lon, lat) {
    var c = this.cam, l0 = c.lon * DEG, p0 = c.lat * DEG, la = lat * DEG, dl = lon * DEG - l0;
    var x = Math.cos(la) * Math.sin(dl);
    var y = Math.cos(p0) * Math.sin(la) - Math.sin(p0) * Math.cos(la) * Math.cos(dl);
    var z = Math.sin(p0) * Math.sin(la) + Math.cos(p0) * Math.cos(la) * Math.cos(dl);
    var R = this._radius();
    return [c.cx * innerWidth + x * R, c.cy * innerHeight - y * R, z];
  };

  Stage.prototype._frame = function (now) {
    if (this.to) {
      var t = this.dur ? Math.min(1, (now - this.t0) / this.dur) : 1, e = ease(t), f = this.from, g = this.to;
      ['lon', 'lat', 'k', 'cx', 'cy', 'dim'].forEach(function (k) {
        this.cam[k] = f[k] + (g[k] - f[k]) * e;
      }, this);
      if (t >= 1) this.to = null;
      this.dirty = true;
    } else if (this.spin && !document.hidden) {
      this.cam.lon += this.spin; this.dirty = true;
    }
    if (!this.dirty) return;
    this.dirty = false;
    this._draw();
    this._drawMarkers();
    this._parallax();
  };

  /* The sky drifts a little with the camera (bounded, so a spinning globe
   * rocks it gently instead of scrolling it away) and dims with the stage. */
  Stage.prototype._parallax = function () {
    if (!this.sky) return;
    var c = this.cam, s = 1 + (c.k - 0.45) * 0.05;
    var dx = -Math.sin(c.lon * DEG) * innerWidth * 0.022, dy = c.lat / 90 * innerHeight * 0.03;
    this.sky.style.transform = 'translate(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px) scale(' + s.toFixed(4) + ')';
    this.sky.style.opacity = (1 - c.dim * 0.7).toFixed(3);
  };

  Stage.prototype._draw = function () {
    if (this.failed) return;
    var gl = this.gl, dpr = Math.min(devicePixelRatio || 1, 1.5);
    var w = Math.round(innerWidth * dpr), h = Math.round(innerHeight * dpr);
    if (this.c.width !== w || this.c.height !== h) { this.c.width = w; this.c.height = h; }
    gl.viewport(0, 0, w, h);
    var c = this.cam, u = this.u, R = this._radius() * dpr;
    gl.uniform2f(u.uRes, w, h);
    gl.uniform2f(u.uCtr, c.cx * w, h - c.cy * h);
    gl.uniform1f(u.uR, R);
    gl.uniform1f(u.uLon0, c.lon * DEG);
    gl.uniform1f(u.uLat0, c.lat * DEG);
    gl.uniform1f(u.uDim, c.dim);
    gl.uniform1f(u.uHasTex, this.hasTex);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };

  /* Markers: plain SVG circles, one per item, visibility by the z sign. */
  Stage.prototype._drawMarkers = function () {
    var svg = this.svg;
    if (!this._nodes || this._nodes.length !== this.markers.length) {
      svg.textContent = '';
      this._nodes = this.markers.map(function (m) {
        var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('class', 'mk ' + (m.cls || ''));
        var c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        c.setAttribute('r', m.r || 4);
        g.appendChild(c);
        if (m.pulse) {
          var p = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
          p.setAttribute('r', m.r || 4); p.setAttribute('class', 'pulse'); g.appendChild(p);
        }
        if (m.label) {
          var t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          t.textContent = m.label; t.setAttribute('x', (m.r || 4) + 10); t.setAttribute('y', 5);
          g.appendChild(t);
        }
        if (m.title) {
          var ti = document.createElementNS('http://www.w3.org/2000/svg', 'title');
          ti.textContent = m.title; g.appendChild(ti);
        }
        svg.appendChild(g);
        return g;
      });
    }
    for (var i = 0; i < this.markers.length; i++) {
      var m = this.markers[i], p = this.project(m.lon, m.lat), n = this._nodes[i];
      if (p[2] <= 0.02) { n.style.display = 'none'; continue; }
      n.style.display = '';
      n.setAttribute('transform', 'translate(' + p[0].toFixed(1) + ',' + p[1].toFixed(1) + ')');
      n.style.opacity = Math.min(1, p[2] * 4).toFixed(2);
    }
  };

  global.AtlasStage = Stage;
})(window);
