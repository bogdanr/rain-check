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

  /* The sky is the real one: catalogue stars (HYG v4.1, to magnitude 7,
   * packed by export.py) at the ground longitude they stood over at 00:00
   * UTC on the data date. They go through the same camera rotation as the
   * globe and a pinhole projection behind it, so orbiting the Earth turns
   * the sky with it. Size and brightness follow magnitude; colour is the
   * star's own B-V tint. Nothing is drawn that is not a real star. */
  var SVS = [
    '#version 300 es',
    'in vec2 aPos; in vec3 aCol; in float aMag;',
    'uniform float uLon0, uLat0, uF, uDpr, uFade;',
    'uniform vec2 uRes;',
    'out vec3 vCol; out float vA; out float vPx; out float vGlow; out float vDpr;',
    'void main(){',
    '  float lon = aPos.x * 3.14159265, lat = aPos.y * 1.5707963, dl = lon - uLon0;',
    '  float sl = sin(uLat0), cl = cos(uLat0);',
    '  vec3 d = vec3(cos(lat) * sin(dl), cl * sin(lat) - sl * cos(lat) * cos(dl), sl * sin(lat) + cl * cos(lat) * cos(dl));',
    // Only the half of the sky behind the Earth, seen from the camera.
    '  if (d.z > -0.05){ gl_Position = vec4(2.0, 2.0, 0.0, 1.0); gl_PointSize = 0.0; return; }',
    '  vec2 s = d.xy / -d.z * uF;',
    '  gl_Position = vec4(s / (uRes * 0.5), 0.0, 1.0);',
    '  float m = aMag * 9.0 - 1.5;',
    '  float flux = pow(10.0, -0.4 * (m - 3.0));',             // 1 at magnitude 3
    // Faint stars are a whisper (their density is what draws the Milky Way);
    // the brightest few hundred carry the picture.
    '  vA = clamp(0.07 + 0.8 * sqrt(flux), 0.0, 1.0) * uFade;',
    // Only stars brighter than about magnitude 3 carry a soft glow; its
    // radius grows with flux. The sprite is sized to hold it, never spikes.
    '  vGlow = clamp(sqrt(flux) - 1.0, 0.0, 3.0);',
    '  vDpr = uDpr;',
    '  vPx = (4.0 + 6.0 * vGlow) * uDpr;',
    '  gl_PointSize = vPx;',
    '  vCol = aCol;',
    '}'
  ].join('\n');
  var SFS = [
    '#version 300 es',
    'precision mediump float;',
    'in vec3 vCol; in float vA; in float vPx; in float vGlow; in float vDpr; out vec4 o;',
    'void main(){',
    // Distance from the star centre in CSS pixels, so the core is about one
    // pixel wide whatever the sprite size.
    '  float d = length(gl_PointCoord - 0.5) * vPx / vDpr;',
    '  float core = exp(-d * d / (1.3 + 0.5 * min(vGlow, 1.0)));',
    '  float gs = 1.0 + 2.2 * vGlow;',
    '  float glow = vGlow > 0.0 ? 0.3 * exp(-d * d / (gs * gs)) : 0.0;',
    '  float a = min(1.0, vA * (core + glow));',
    '  o = vec4(vCol * a, a);',
    '}'
  ].join('\n');

  function Stage(canvas, svg, opts) {
    this.c = canvas; this.svg = svg; this.opts = opts;
    this.cam = { lon: 26, lat: 30, k: 0.4, cx: 0.68, cy: 0.52, dim: 0 };
    this.from = null; this.to = null; this.t0 = 0; this.dur = 900;
    this.spin = 0; this.dirty = true; this.markers = [];
    var gl = this.gl = canvas.getContext('webgl2', { antialias: true, alpha: true, premultipliedAlpha: true });
    if (!gl) { this.failed = true; return; }
    this.prog = this._program(VS, FS);
    this.vaoGlobe = gl.createVertexArray();
    gl.bindVertexArray(this.vaoGlobe);
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(this.prog, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.bindVertexArray(null);
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
    this._loadStars();
    this.readTheme();
    this._bindDrag();
    canvas.addEventListener('webglcontextlost', function (e) { e.preventDefault(); self.failed = true; self.opts.onFail && self.opts.onFail(); });
    requestAnimationFrame(function f(now) { self._frame(now); requestAnimationFrame(f); });
  }

  Stage.prototype._program = function (vs, fs) {
    var gl = this.gl;
    function sh(type, src) {
      var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    }
    var p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    gl.useProgram(p);
    return p;
  };

  /* 8 bytes a star: int16 lon/pi, int16 lat/(pi/2), rgb, magnitude byte. */
  Stage.prototype._loadStars = function () {
    var gl = this.gl, self = this;
    if (!this.opts.stars) return;
    fetch(this.opts.stars).then(function (r) { return r.arrayBuffer(); }).then(function (ab) {
      var p = self.sprog = self._program(SVS, SFS);
      self.su = {};
      ['uLon0', 'uLat0', 'uF', 'uDpr', 'uFade', 'uRes'].forEach(function (n) { self.su[n] = gl.getUniformLocation(p, n); });
      self.vaoStars = gl.createVertexArray();
      gl.bindVertexArray(self.vaoStars);
      var b = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, b);
      gl.bufferData(gl.ARRAY_BUFFER, ab, gl.STATIC_DRAW);
      function at(name, n, type, off) {
        var l = gl.getAttribLocation(p, name); gl.enableVertexAttribArray(l);
        gl.vertexAttribPointer(l, n, type, true, 8, off);
      }
      at('aPos', 2, gl.SHORT, 0); at('aCol', 3, gl.UNSIGNED_BYTE, 4); at('aMag', 1, gl.UNSIGNED_BYTE, 7);
      gl.bindVertexArray(null);
      self.nStars = ab.byteLength / 8; self.dirty = true;
    }).catch(function () { /* no stars: space stays black */ });
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
  };

  Stage.prototype._draw = function () {
    if (this.failed) return;
    var gl = this.gl, dpr = Math.min(devicePixelRatio || 1, 1.5);
    var w = Math.round(innerWidth * dpr), h = Math.round(innerHeight * dpr);
    if (this.c.width !== w || this.c.height !== h) { this.c.width = w; this.c.height = h; }
    gl.viewport(0, 0, w, h);
    var c = this.cam, u = this.u, R = this._radius() * dpr;
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);   // premultiplied
    // Stars first; the globe (opaque where it covers, glow elsewhere) on top.
    if (this.nStars) {
      var su = this.su;
      gl.useProgram(this.sprog); gl.bindVertexArray(this.vaoStars);
      gl.uniform1f(su.uLon0, c.lon * DEG); gl.uniform1f(su.uLat0, c.lat * DEG);
      // A 100-degree field across the wider side of the window: wide enough
      // that the frame holds a real night's worth of stars, not a keyhole.
      gl.uniform1f(su.uF, Math.max(w, h) / 2 / Math.tan(50 * DEG));
      gl.uniform1f(su.uDpr, dpr); gl.uniform1f(su.uFade, 1 - c.dim * 0.75);
      gl.uniform2f(su.uRes, w, h);
      gl.drawArrays(gl.POINTS, 0, this.nStars);
    }
    gl.useProgram(this.prog); gl.bindVertexArray(this.vaoGlobe);
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
