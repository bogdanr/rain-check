/* Atlas Noir - the stage.
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
 * The live sky (sky.js supplies the data):
 *  - the real Sun lights the globe: day, night, dusk, ocean glint, and an
 *    atmosphere that glows where the light grazes it;
 *  - the real Moon hangs in the sky, in phase, and silvers the night side;
 *  - the stars turn to the current sidereal time;
 *  - satellite clouds and rain sit on the globe, either as a flat layer with
 *    shadows (weak devices, docked or dimmed globe) or as a ray-marched
 *    volume: clouds with tops, self-shadowing and silver linings, rain
 *    curtains under them. The volume is drawn at reduced resolution while
 *    anything moves, then refined over a few jittered frames at a higher one
 *    and left alone: an idle page costs nothing.
 *  - near the selected city, a Meteosat frame at about 1-2 km (sky.js
 *    detail) is laid over the 19 km world field with a soft edge; elsewhere
 *    the world field is read through a small noise warp, so cloud edges
 *    follow cloud shapes instead of its pixel grid.
 *
 * Markers are an SVG layer projected with the same orthographic maths, so
 * they stay focusable and labelled.
 */
(function (global) {
  'use strict';

  var DEG = Math.PI / 180;

  /* A performance mark, once per name (DevTools > Performance, and the
   * browser checks read them). */
  function mark(n) {
    try { if (!performance.getEntriesByName('rc:' + n).length) performance.mark('rc:' + n); } catch (e) { /* no User Timing */ }
  }
  function sess(k, v) {
    try { if (v === undefined) return sessionStorage.getItem(k); if (v === null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, v); }
    catch (e) { /* private mode */ }
    return null;
  }

  /* The city-detail frame, shared by the globe and the volume: cover, top
   * and weight at a world uv (weight 0 outside the frame, and fading to 0
   * over its outer 15%). uDetLod is chosen per frame from how many of its
   * texels fall on a screen pixel, so the zoomed-out globe does not shimmer. */
  var DET = [
    'uniform sampler2D uDet;',
    'uniform vec4 uDetBox;',                    // lon0, lat0, lon1, lat1 (degrees)
    'uniform float uHasDet, uDetLod;',
    'vec3 detAt(vec2 uv){',
    '  if (uHasDet < 0.5) return vec3(0.0);',
    '  vec2 t = vec2(((uv.x - 0.5) * 360.0 - uDetBox.x) / (uDetBox.z - uDetBox.x),',
    '                (uDetBox.w - (0.5 - uv.y) * 180.0) / (uDetBox.w - uDetBox.y));',
    '  if (t.x <= 0.0 || t.y <= 0.0 || t.x >= 1.0 || t.y >= 1.0) return vec3(0.0);',
    '  vec3 s = textureLod(uDet, t, uDetLod).rgb;',
    '  float e = min(min(t.x, 1.0 - t.x), min(t.y, 1.0 - t.y));',
    '  return vec3(s.rg, s.b * smoothstep(0.0, 0.15, e));',
    '}'
  ].join('\n');

  /* The 10 m wind (wind.js, setWind): the model grid as an RG16F texture,
   * u east and v north in m/s, interpolated to the page clock. Columns wrap
   * round the date line; rows clamp past the outermost (75 degrees). */
  var WIND = [
    'uniform sampler2D uWind;',
    'uniform vec4 uWG;',                        // lon0, dlon, lat0, dlat (degrees)
    'uniform vec2 uWN;',                        // columns, rows
    'uniform float uWindOn;',
    'uniform vec3 uWvG;',                       // WAVE: glint, caps, wind (FS)
    'vec2 windAt(vec2 uv){',
    '  float lo = (uv.x - 0.5) * 360.0, la = (0.5 - uv.y) * 180.0;',
    '  return texture(uWind, vec2(((lo - uWG.x) / uWG.y + 0.5) / uWN.x, ((la - uWG.z) / uWG.w + 0.5) / uWN.y)).rg;',
    '}'
  ].join('\n');

  var VS = '#version 300 es\n' +
    'in vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }';

  /* Rain is not painted as a radar map. It is weather inside the cloud:
   * the IMERG channel ((step + 1) / 110, step i = 0.1 * 10^(i/40) mm/h, so
   * 0.37 is 1 mm/h and 0.74 is 10 mm/h) thickens the cloud where it rains
   * and greys and darkens it with intensity, the way a rain cloud looks from
   * above; the volume also hangs shafts under it. With clouds switched off,
   * rain shows as the same grey veil on its own. */

  var FS = [
    '#version 300 es',
    'precision highp float;',
    'uniform sampler2D uElev, uBio, uWxA, uWxB, uRain, uLights;',
    'uniform vec2 uRes, uCtr, uTex;',          // viewport px, globe centre px (GL y-up), elev size
    'uniform float uR, uLon0, uLat0, uDim, uHasTex;',
    'uniform vec3 cAbyss, cShelf, cForest, cDesert, cRock, cIce, cShore, cAtmo, cGround, cLand;',
    'uniform vec4 uLight;',                     // ambient, contrast, seaAmbient, sun
    'uniform vec3 uDisp;',                      // per metre, per root-metre, tallest (radius fractions)
    // Live sky. uLive 0 = the fixed studio light of the report; 1 = the real Sun.
    'uniform vec3 uSun, uMoon;',                // view-space directions
    'uniform float uLive, uMoonK, uWx, uFlat, uMix, uHasRain, uHasLights, uRainOn, uSoft;',
    'uniform vec4 uLk3;',                       // the look: y = flat-layer opacity
    DET,
    WIND,
    'out vec4 o;',
    'const float PI = 3.14159265;',
    'float metres(float c){',
    '  c *= 255.0;',
    '  if (c > 128.0){ float t = (c - 128.0) / 127.0; return t * t * 8500.0; }',
    '  float d = (128.0 - c) / 127.0; return -pow(d, 1.0 / 0.65) * 9000.0;',
    '}',
    'float hAt(vec2 uv, float lod){ return metres(textureLod(uElev, uv, lod).r); }',
    // Only land is displaced; the sea stays on the sphere.
    'float disp(float h){ h = max(h, 0.0); return min(h * uDisp.x, sqrt(h) * uDisp.y); }',
    'vec2 uvOf(vec3 n, out float lat){',
    '  float sl = sin(uLat0), cl = cos(uLat0);',
    '  lat = asin(clamp(n.y * cl + n.z * sl, -1.0, 1.0));',
    '  float lon = uLon0 + atan(n.x, n.z * cl - n.y * sl);',
    '  return vec2(fract(lon / (2.0 * PI) + 0.5), 0.5 - lat / PI);',
    '}',
    // The flat layer and, at lod 0, the city-detail frame over it; the
    // shadows read a coarse mip of the world field alone.
    'float cover(vec2 uv, float lod){',
    '  float c = mix(textureLod(uWxA, uv, lod).r, textureLod(uWxB, uv, lod).r, uMix);',
    '  if (lod < 0.5){ vec3 dt = detAt(uv); c = mix(c, dt.x, dt.z); }',
    '  return c;',
    '}',
    // Radial gap between a point on the view ray and the terrain under it.
    'float gapAt(vec3 p, out vec3 n){',
    '  float r = length(p); n = p / r; float la;',
    '  return r - 1.0 - disp(hAt(uvOf(n, la), 0.0));',
    '}',
    'void main(){',
    '  vec2 q = (gl_FragCoord.xy - uCtr) / uR;',
    '  float rho2 = dot(q, q), rho = sqrt(rho2);',
    '  vec3 S = uLive > 0.5 ? uSun : normalize(vec3(-0.42, 0.46, 0.78));',
    // Space is transparent: the star sky is its own layer underneath. Only the
    // thin atmosphere is drawn here, as premultiplied light with alpha 0, so
    // it adds to the stars instead of covering them.
    '  float a = max(rho - 1.0, 0.0);',
    '  float halo = exp(-a * 70.0) * 0.28 + exp(-a * 14.0) * 0.045;',
    '  vec3 bg = cAtmo * halo;',
    '  if (uLive > 0.5){',
    // The air is lit where the Sun is: blue over the day side, a thin orange
    // band over the terminator, and a bright crescent when the Sun is behind
    // the Earth and its light comes round the limb.
    '    vec3 nL = normalize(vec3(q, 0.0));',
    '    float mL = dot(nL, S);',
    '    float lit = smoothstep(-0.30, 0.25, mL);',
    '    float dusk = exp(-mL * mL * 28.0);',
    '    float back = pow(max(dot(nL.xy, normalize(S.xy + 1e-5)), 0.0), 8.0) * smoothstep(0.1, -0.6, S.z);',
    '    bg = cAtmo * halo * (0.16 + 0.95 * lit) + vec3(1.0, 0.48, 0.2) * halo * dusk * 0.9 +',
    '         vec3(1.0, 0.78, 0.5) * back * (exp(-a * 30.0) * 0.9 + exp(-a * 6.0) * 0.12);',
    '  }',
    '  bg *= 1.0 - uDim * 0.5;',
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
    '  float mu = dot(n, S);',
    '  float sph = max(mu, 0.0);',
    '  float day = uLive > 0.5 ? smoothstep(-0.12, 0.10, mu) : 1.0;',
    '  float sunBase = 1.0 - uLight.w * 0.72;',
    '  vec3 hv = normalize(S + vec3(0.0, 0.0, 1.0));',
    '  float wet = textureLod(uRain, uv, 0.0).r * uRainOn;',
    '  float here = uWx > 0.5 ? cover(uv, 0.0) : 0.0;',
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
    '      col *= 1.0 - 0.28 * smoothstep(0.05, 0.5, wet) * (1.0 - sn);',   // rain-darkened ground
    '      col *= (uLight.x + uLight.y * (hill - 1.0) + uLight.y * 0.55) * (sunBase + uLight.w * sph);',
    '      col += pow(max(dot(n, hv), 0.0), 40.0) * 0.22 * smoothstep(0.05, 0.5, wet) * day;',
    '    } else {',
    '      float dep = pow(clamp(-h / 9000.0, 0.0, 1.0), 0.468);',
    '      col = mix(cShelf, cAbyss, dep);',
    '      col = mix(col, cIce, step(0.02, bio.g) * bio.g);',
    '      col *= (uLight.z + 0.25 * (hill - 1.0)) * (sunBase + uLight.w * sph * 0.7);',
    // Sun glint: a tight core and a wide sheen, both from the real Sun.
    '      float gl = max(dot(n, hv), 0.0);',
    '      float core = pow(gl, 400.0), sheen = pow(gl, 32.0) * 0.35;',
    // With the wind on, the wind shapes the sea as it does seen from orbit.
    // Cox & Munk (1954): the variance of the sea's slopes grows with the
    // wind, 0.003 + 0.00512 U, so a calm sea is a small bright mirror of the
    // Sun and a gale a broad dull sheen (the fixed core above is a slope
    // variance of about 0.005, a light breeze). Monahan & O Muircheartaigh
    // (1980): the whitecap fraction is 3.84e-6 U^3.41 - under 1 % at 10 m/s,
    // about 10 % at 20 - drawn 2.5 times over so storm belts read at globe
    // scale.
    '      if (uWindOn > 0.5){',
    '        float U = length(windAt(uv)) * uWvG.z, s2 = 0.003 + 0.00512 * U * uWvG.x;',
    '        float c2 = gl * gl, t2 = (1.0 - c2) / max(c2, 1e-4);',
    '        core = exp(-t2 / s2) * pow(0.005 / s2, 0.7);',
    '        sheen *= 0.7 + 0.3 * smoothstep(2.0, 12.0, U);',
    '        float wc = min(0.25, 2.5 * uWvG.y * 3.84e-6 * pow(U, 3.41)) * (1.0 - step(0.02, bio.g) * bio.g);',
    '        col = mix(col, vec3(0.78, 0.82, 0.86) * (sunBase + uLight.w * sph), wc);',
    '      }',
    '      col += (sheen + core * uLive * 0.9) * day * (1.0 - here * 0.8) * vec3(1.0, 0.95, 0.85);',
    '      col += cShore * pow(bio.b, 14.0) * 0.45;',
    '    }',
    '  }',
    '  if (uLive > 0.5){',
    // Shadows of the clouds, cast along the real sunlight from about the
    // height the cloud layer is drawn at, softened by a coarse mip.
    '    if (uWx > 0.5){',
    '      float la2; vec3 nS = normalize(n + S * (0.026 / max(mu, 0.2)));',
    '      float sc = cover(uvOf(nS, la2), 2.5);',
    // Soft: thin cloud lets most sunlight through, so its shadow is faint.
    '      col *= 1.0 - 0.62 * mix(sc, sc * sc * (1.6 - 0.6 * sc), uSoft) * day;',
    '    }',
    '    vec3 night = vec3(0.10, 0.13, 0.22);',
    '    vec3 moon = vec3(0.50, 0.60, 0.85) * uMoonK * max(dot(n, uMoon), 0.0) * 0.32;',
    '    vec3 dusk = mix(vec3(1.0, 0.52, 0.30), vec3(1.0), smoothstep(0.0, 0.28, mu));',
    '    col *= mix(night + moon, dusk, day);',
    // The flat cloud layer: satellite cover, lit by the same Sun and Moon.
    // Where it rains there is cloud, greyer and darker the heavier the rain.
    '    float rw = smoothstep(0.08, 0.75, wet);',
    '    vec3 lit = (0.55 + 0.75 * sph) * mix(night * 1.6 + moon * 1.8, dusk, day);',
    '    if (uWx > 0.5 && uFlat > 0.0){',
    '      float c = max(here, smoothstep(0.02, 0.3, wet) * 0.8);',
    '      vec3 cc = mix(vec3(0.93, 0.95, 1.0), vec3(0.40, 0.44, 0.52), rw) * lit;',
    '      float op = c * 0.9;',
    '      if (uSoft > 0.5){',
    // Soft flat layer: thin cover is a see-through veil, thick cover nearly
    // opaque; and a little relief from one coarse read toward the Sun - the
    // side of a mass facing the Sun is lit, the far side and dense cores
    // are a softer grey.
    '        op = min(1.0, (1.0 - exp(-c * c * 3.2)) * 0.97 * uLk3.y);',
    '        float la3; float up = cover(uvOf(normalize(n + S * 0.012), la3), 1.5);',
    '        float rel = clamp((c - up) * 2.2, -0.45, 0.45);',
    '        cc *= (1.0 + rel * 0.55 * day) * mix(1.0, 0.86, smoothstep(0.55, 1.0, c));',
    '      }',
    '      col = mix(col, cc, op * uFlat);',
    '    } else if (uWx < 0.5 && wet > 0.0){',
    '      col = mix(col, mix(vec3(0.62, 0.66, 0.74), vec3(0.36, 0.40, 0.48), rw) * lit, smoothstep(0.02, 0.4, wet) * 0.6);',
    '    }',
    // City lights on the night side: the lit cores plus a soft glow from a
    // coarse mip, so towns read as well as megacities. Cloud dims them but
    // does not black them out; thin cloud glows over a city.
    '    if (uHasLights > 0.5){',
    '      float li = texture(uLights, uv).r, lg = textureLod(uLights, uv, 3.0).r;',
    '      col += vec3(1.0, 0.72, 0.42) * (pow(li, 1.1) * 2.6 + lg * 0.9) * (1.0 - day) * (1.0 - 0.4 * here);',
    '    }',
    '    float rim = pow(1.0 - clamp(n.z, 0.0, 1.0), 5.0);',
    '    col += (cAtmo * (0.12 + 0.88 * day) + vec3(1.0, 0.5, 0.2) * exp(-mu * mu * 30.0) * 0.6) * rim * 0.22;',
    '  } else {',
    '    col += cAtmo * pow(1.0 - clamp(n.z, 0.0, 1.0), 5.0) * 0.22;',  // thin limb haze
    '  }',
    '  col = mix(col, cGround, uDim);',                       // stage dims behind reading
    '  o = vec4(col * cov + bg * (1.0 - cov), cov);',
    '}'
  ].join('\n');

  /* ── The volume: clouds and rain, ray-marched in a shell above the globe ──
   *
   * The shell runs from 1.010 to 1.046 Earth radii (about 64 to 290 km if it
   * were to scale; real cloud tops are 2-18 km, so heights are exaggerated
   * about 15-20x on top of the base lift that clears the relief).
   * Between the ground and the base, rain falls where IMERG says it does.
   * Cover and top height come from the satellite (sky.js); the 3D noise only
   * decides the shape inside cover that is really there. */
  var CFS = [
    '#version 300 es',
    'precision highp float;',
    'precision highp sampler3D;',
    'uniform sampler2D uWxA, uWxB, uRain, uLights;',
    'uniform sampler3D uNoise;',
    'uniform vec2 uCtr, uJit;',
    'uniform float uR, uMix, uFrame, uT, uHasRain, uHasLights, uMoonK, uThin, uRainOn;',
    'uniform float uWarp, uEro, uStepK;',       // warp (world texels), edge erosion (1-1.5), march step (shell heights)
    'uniform float uSoft;',                     // 1 = soft, translucent look; 0 = the old one (?soft=0)
    'uniform float uTypes;',                    // 1 = cloud types by height; 0 = one kind (?types=0)
    'uniform vec4 uLk0, uLk1, uLk2, uLk3;',     // the look (LOOK, stage.setLook)
    'uniform mat3 uW2V;',
    'uniform vec3 uSun, uMoon, uCity;',
    'uniform int uSteps, uLSteps;',
    DET,
    'out vec4 o;',
    'const float PI = 3.14159265;',
    'const float CB = 1.010, CT = 1.046, HH = CT - CB;',
    'const float SIG = 420.0, SIGR = 60.0;',
    'float hash(vec2 q){ return fract(sin(dot(q, vec2(12.9898, 78.233))) * 43758.5453); }',
    'vec2 uvW(vec3 d){ return vec2(atan(d.x, d.z) / (2.0 * PI) + 0.5, 0.5 - asin(clamp(d.y, -1.0, 1.0)) / PI); }',
    'vec2 wxW(vec2 uv){ return mix(textureLod(uWxA, uv, 0.0).rg, textureLod(uWxB, uv, 0.0).rg, uMix); }',
    'vec2 wxAt(vec2 uv){ vec3 dt = detAt(uv); return mix(wxW(uv), dt.xy, dt.z); }',
    // The main march reads the world field through a small warp (about a
    // texel either way, varying over a few texels): cloud edges then follow
    // noise shapes instead of the 19 km grid's straight lines and diamonds.
    // Never over the city-detail frame, which has no grid to hide.
    'vec2 wxWarp(vec2 uv, vec3 d){',
    '  vec3 dt = detAt(uv);',
    '  vec2 w;',
    // No cloud within a few texels (a coarse mip): nothing for the warp to
    // pull in, so skip its two 3-D reads.
    '  bool near = uSoft < 0.5 || mix(textureLod(uWxA, uv, 1.6).r, textureLod(uWxB, uv, 1.6).r, uMix) > 0.008;',
    '  if (uWarp > 0.0 && dt.z < 0.99 && near){',
    '    vec3 c = d * 7.0;',
    '    vec2 off = vec2(textureLod(uNoise, c + vec3(0.31, 0.17, 0.53), 0.0).g,',
    '                    textureLod(uNoise, c + vec3(0.71, 0.43, 0.09), 0.0).g) - 0.6;',
    '    w = wxW(uv + off * uWarp / vec2(textureSize(uWxA, 0)));',
    '  } else w = wxW(uv);',
    '  return mix(w, dt.xy, dt.z);',
    '}',
    'float remap(float v, float a, float b){ return clamp((v - a) / max(b - a, 1e-4), 0.0, 1.0); }',
    // Henyey-Greenstein, scaled so isotropic is 1.
    'float hg(float c, float g){ float g2 = g * g; return (1.0 - g2) / pow(1.0 + g2 - 2.0 * g * c, 1.5); }',
    // How cirrus-like a column is: a cold top over thin cover (a veil of ice,
    // not a deck). 0 with the old look.
    'float cirrus(vec2 wx){ return uSoft * min(uLk3.x, 1.0) * smoothstep(0.55, 0.8, wx.g) * (1.0 - smoothstep(0.22, 0.5, wx.r)); }',
    // Extinction per unit density for a column (look: density, veil): thin
    // satellite cover is a see-through veil, medium and thick cover full;
    // cirrus thinner still. The rim of each cloud is made translucent per
    // sample in main(), so the mass of a cloud is kept.
    'float sigK(vec2 wx){',
    '  if (uSoft < 0.5) return 1.0;',
    '  return uLk0.x * mix(uLk0.w, 1.0, smoothstep(0.1, 0.55, wx.r)) * mix(1.0, 0.5, cirrus(wx));',
    '}',
    // Cloud types, estimated per column from what the satellites give us -
    // cover, how cold the top is, and rain (x = low puffy cumulus, y = low
    // sheet (stratus), z = mid layer, w = storm tower). Cirrus is cirrus().
    'vec4 ctype(vec2 wx, float wet){',
    '  if (uTypes < 0.5 || uSoft < 0.5) return vec4(0.0);',
    '  float lo = 1.0 - smoothstep(0.3, 0.45, wx.g), mid = smoothstep(0.3, 0.45, wx.g) * (1.0 - smoothstep(0.6, 0.75, wx.g));',
    '  float storm = smoothstep(0.62, 0.8, wx.g) * smoothstep(0.45, 0.7, wx.r) * smoothstep(0.2, 0.45, wet);',
    '  float sheet = smoothstep(0.45, 0.7, wx.r);',
    '  return clamp(vec4(lo * (1.0 - sheet), lo * sheet, mid * smoothstep(0.3, 0.5, wx.r), storm) * uLk2.w, 0.0, 1.0);',
    '}',
    // q: 2 = full detail (the view march), 1 = base noise, 0 = no noise read
    // (the noise's mean; the far steps of the light march, where the shape
    // no longer shows but the 3-D reads cost the most).
    'float cloud(vec3 d, float r, vec2 wx, float wet, int q){',
    '  float hf = (r - CB) / HH, cov = wx.r;',
    '  if (cov < 0.02 || hf < 0.0 || hf > 1.0) return 0.0;',
    '  float ci = cirrus(wx);',
    '  vec4 ty = ctype(wx, wet);',
    // Colder tops stand taller; a thin cold veil stays a veil, a dense bright
    // mass fills from the base to its top.
    '  float topH = mix(0.12, 1.0, wx.g);',
    '  float thick = mix(0.10, topH, cov * cov);',
    // Types: cirrus is a thin sheet at the top; a mid layer stays a layer;
    // a storm stands from the base to the top.
    '  thick = mix(thick, min(thick, 0.12), ci);',
    '  thick = mix(thick, min(thick, 0.4), ty.z);',
    '  thick = mix(thick, topH, ty.w);',
    '  float botH = max(topH - thick, 0.0);',
    // Cumulus and storms have flat bases.
    '  float bw = mix(mix(0.07, 0.10, uSoft), 0.025, min(ty.x + ty.w, 1.0));',
    '  float prof = smoothstep(botH, botH + bw, hf) * (1.0 - smoothstep(mix(botH, topH, 0.5), topH, hf));',
    // Storm: the anvil - denser in the top third, where it spreads out.
    '  prof *= 1.0 + 0.5 * ty.w * smoothstep(0.6, 0.9, (hf - botH) / max(topH - botH, 1e-3));',
    '  if (prof < 0.01) return 0.0;',
    '  float nz = 0.55;',
    '  vec3 c = d * 7.0 + vec3(uT * 0.0012, hf * 0.45, uT * 0.0008);',
    '  if (q > 0){',
    '    nz = texture(uNoise, c).r;',
    // Stratus and mid layers are smoother sheets.
    '    nz = mix(nz, 0.3 + 0.7 * nz, ty.y * 0.5 + ty.z * 0.25);',
    // Cumulus: separate puffs - the noise at a finer scale, cut harder.
    '    if (q > 1 && ty.x > 0.01) nz = mix(nz, smoothstep(0.25, 0.75, texture(uNoise, c * 1.7 + vec3(0.37, 0.0, 0.61)).r), ty.x * 0.5);',
    // Cirrus: the noise stretched east-west (around the polar axis): streaks.
    '    if (q > 1 && ci > 0.01) nz = mix(nz, texture(uNoise, vec3(d.x * 3.0, d.y * 34.0, d.z * 3.0) + vec3(uT * 0.002, 0.0, 0.0)).r, ci);',
    '  }',
    '  float dn = remap(nz * prof, 1.0 - cov, 1.0) * cov;',
    // Lumpy tops (look: lump): the height profile raises the noise threshold
    // instead of scaling the density, so a dense deck rises into rounded
    // domes rather than ending in a flat plate. No extra texture read.
    '  if (uSoft > 0.5 && uLk1.x > 0.0) dn = mix(dn, remap(nz, 1.0 - cov * prof, 1.0) * cov, min(uLk1.x, 1.0));',
    // Edges eroded by finer noise; harder, and with a finer octave, the
    // larger a satellite pixel stands on screen (uEro, from the zoom).
    '  if (q > 1 && dn > 0.0 && dn < 0.7 + 0.25 * (uEro - 1.0)){',
    '    float det = texture(uNoise, d * 41.0 + vec3(0.0, hf * 1.3, 0.0)).g;',
    '    if (uEro > 1.01) det = mix(det, textureLod(uNoise, d * 131.0 + vec3(hf * 2.1, 0.0, 0.0), 0.0).g, 0.5 * (uEro - 1.0));',
    // Puffy (inverted, billowing) detail over the upper part of dense cloud
    // (look: billow), wispy at the bases and in thin or cirrus cloud.
    '    det = mix(det, 1.0 - det, uSoft * min(uLk1.y, 1.0) * smoothstep(0.2, 0.6, hf) * smoothstep(0.3, 0.7, cov) * (1.0 - ci));',
    '    dn = remap(dn, (1.0 - det) * 0.3 * uEro * (1.0 - 0.4 * hf), 1.0);',
    '  }',
    // Thinned a little over the selected city, so its marker stays readable.
    '  vec3 e = d - uCity;',
    '  return dn * (1.0 - uThin * exp(-dot(e, e) / 0.0012));',
    '}',
    'float rainD(vec3 d, float r, vec2 rr){',
    '  if (rr.r < 0.01) return 0.0;',
    '  float hh = (r - 1.0) / (CB - 1.0);',
    // Streaks: fine across, long down, scrolling downward with time.
    '  float st = texture(uNoise, vec3(d.x * 150.0, (r - 1.0) * 16.0 + uT * 0.3, d.z * 150.0)).g;',
    '  return pow(rr.r, 0.8) * (0.3 + 0.7 * smoothstep(0.35, 0.8, st)) * (1.0 - smoothstep(0.75, 1.0, hh));',
    '}',
    'void main(){',
    '  vec2 q = (gl_FragCoord.xy + uJit - uCtr) / uR;',
    '  float rho2 = dot(q, q);',
    '  if (rho2 >= CT * CT){ o = vec4(0.0); return; }',
    '  float zT = sqrt(CT * CT - rho2);',
    '  float zB = rho2 < 1.0 ? sqrt(1.0 - rho2) : -zT;',
    '  mat3 V2W = transpose(uW2V);',
    // Near-vertical rays cross little ground: if neither end sees cloud or
    // rain, nothing is there.
    '  if (rho2 < 0.85){',
    '    vec3 a = normalize(V2W * vec3(q, zT)), b = normalize(V2W * vec3(q, zB));',
    '    vec2 ua = uvW(a), ub = uvW(b);',
    '    float m = max(max(wxAt(ua).r, wxAt(ub).r), max(textureLod(uRain, ua, 1.0).r, textureLod(uRain, ub, 1.0).r) * uHasRain);',
    '    if (m < 0.02){ o = vec4(0.0); return; }',
    '  }',
    '  float len = zT - zB;',
    '  int N = int(clamp(len / (HH * uStepK), 10.0, float(uSteps)));',
    '  float ds = len / float(N);',
    '  float j = fract(hash(gl_FragCoord.xy) + uFrame * 0.61803);',
    '  vec3 S = uSun;',
    '  float cosT = -S.z;',                      // light travels along -S, leaves toward the viewer (+z)
    '  float ph = 0.55 + 0.25 * hg(cosT, 0.7) + 0.2 * hg(cosT, -0.3);',
    '  float ph2 = 0.6 + 0.4 * hg(cosT, 0.35);',  // the phase of light scattered more than once: flatter
    '  float T = 1.0; vec3 L = vec3(0.0);',
    '  float s = j * ds;',
    '  for (int i = 0; i < 96; i++){',
    '    if (s >= len || T < 0.02) break;',
    '    vec3 p = vec3(q, zT - s);',
    '    s += ds;',
    '    float r = length(p);',
    '    vec3 d = V2W * p / r;',
    '    vec2 uv = uvW(d);',
    '    float mu = dot(p, S) / r;',
    '    float day = smoothstep(-0.12, 0.10, mu);',
    '    vec3 sunC = mix(vec3(1.0, 0.40, 0.15), vec3(1.0, 0.96, 0.90), smoothstep(-0.02, 0.35, mu));',
    '    vec3 moonL = vec3(0.55, 0.64, 0.86) * uMoonK * max(dot(p / r, uMoon), 0.0) * 0.30;',
    '    vec3 skyA = vec3(0.36, 0.52, 0.80) * 0.5 * day;',
    '    if (r >= CB){',
    '      vec2 wx = wxWarp(uv, d);',
    '      float wetC = textureLod(uRain, uv, 0.0).r * uHasRain;',
    '      float dn = cloud(d, r, wx, wetC, 2);',
    // Empty sky above the rain layer: take a double step.
    '      if (dn <= 0.001){ if (uSoft > 0.5 && wx.r < 0.02 && r - ds > CB) s += ds; continue; }',
    '      float hf = (r - CB) / HH, sk = sigK(wx);',
    // Translucent rims (look: rim, rim width): the faint outer band of a
    // cloud lets light through; its interior keeps full extinction.
    '      float rk = uSoft > 0.5 ? mix(uLk0.y, 1.0, smoothstep(0.0, max(uLk0.z, 0.01), dn)) : 1.0;',
    '      float sunVis = smoothstep(-0.05, 0.04, mu + hf * 0.05);',
    '      float tl = 0.0;',
    // Faint samples add little light: a shorter march toward the Sun.
    '      int ls = uSoft > 0.5 && dn * sk * rk < 0.05 ? min(uLSteps, 3) : uLSteps;',
    '      if (sunVis > 0.0){',
    '        float st = 0.0035; vec3 pp = p;',
    '        for (int k = 0; k < 6; k++){',
    '          if (k >= ls) break;',
    '          pp += S * st;',
    '          float rr = length(pp);',
    '          if (rr > CT) break;',
    '          vec3 dd = V2W * pp / rr;',
    '          vec2 wl = wxAt(uvW(dd));',
    // Soft: noise only on the first two steps; beyond them the shadow is
    // the column's mean shape (no 3-D read) - most of the old light cost.
    '          tl += cloud(dd, rr, wl, 0.0, uSoft > 0.5 && k >= 2 ? 0 : 1) * sigK(wl) * st;',
    '          st *= 1.8;',
    '        }',
    '      }',
    // Self-shadowing (look: shadow): full extinction toward the Sun, so domes
    // shade each other.
    '      float od = SIG * tl * (uSoft > 0.5 ? uLk1.z : 1.0);',
    // Old: a floor under single scattering. Soft: single scattering plus two
    // weaker, deeper, flatter octaves of multiple scattering (look: glow),
    // normalised so a sunlit top is as bright as before and only thin cloud
    // and rims glow.
    '      float Ts = max(exp(-od), exp(-od * 0.25) * 0.3) * ph;',
    '      if (uSoft > 0.5) Ts = (exp(-od) * ph + uLk1.w * (0.5 * exp(-od * 0.5) * ph2 + 0.25 * exp(-od * 0.25))) / (1.0 + 0.75 * uLk1.w) * uLk2.z;',
    // Powder (look): little light comes back out of a wisp, so the fronts
    // of puffs are brighter than their fringes and the gaps between.
    '      float pw = uSoft > 0.5 ? mix(1.0, 1.0 - exp(-dn * 12.0), min(uLk2.x, 1.0)) : 1.0;',
    // Deep in dense cloud the sky is hidden: the ambient term (look) dims.
    '      float amb = uSoft > 0.5 ? uLk2.y * mix(1.0, 0.6, smoothstep(0.3, 1.0, dn)) : 1.0;',
    '      float alb = mix(1.0, 0.38, smoothstep(0.08, 0.75, wetC) * (1.0 - 0.6 * hf));',   // grey rain cloud, dark storm bases
    '      vec3 glow = vec3(0.0);',
    '      if (uHasLights > 0.5 && day < 0.95) glow = vec3(1.0, 0.62, 0.32) * textureLod(uLights, uv, 4.0).r * 2.2 * (1.0 - hf) * (1.0 - day);',
    '      vec3 Li = (sunC * Ts * pw * sunVis * 1.55 + (skyA * (0.45 + 0.55 * hf) + moonL * (0.6 + 0.4 * hf)) * amb + glow) * alb;',
    '      float Tr = exp(-SIG * sk * rk * dn * ds);',
    '      L += T * Li * (1.0 - Tr); T *= Tr;',
    '    } else {',
    '      vec2 rr = textureLod(uRain, uv, 0.0).rg * uHasRain;',
    '      float dn = rainD(d, r, rr);',
    '      if (dn <= 0.001) continue;',
    '      float sunVis = smoothstep(-0.05, 0.04, mu);',
    '      float above = wxAt(uv).r;',
    '      vec3 Li = (sunC * sunVis * (0.3 + 0.35 * hg(cosT, 0.75)) * (1.0 - 0.55 * above) + skyA * 1.3 + moonL) *',
    '                mix(vec3(0.62, 0.72, 0.86), vec3(0.95), rr.g);',
    '      float Tr = exp(-SIGR * dn * ds);',
    '      L += T * Li * (1.0 - Tr); T *= Tr;',
    '    }',
    '  }',
    '  o = vec4(L, 1.0 - T);',
    '}'
  ].join('\n');

  /* Composite: the volume buffer, tone-mapped, faded behind reading; then
   * the Sun's own glare when it stands in the visible sky. */
  var KFS = [
    '#version 300 es',
    'precision highp float;',
    'uniform sampler2D uCl;',
    'uniform vec2 uRes;',
    'uniform float uDim, uFade, uDpr;',
    'uniform vec3 cGround;',
    'uniform vec3 uSunS;',                      // sun px (GL y-up), visible flag
    'uniform vec3 uGlobe;',                     // globe centre px, radius px
    // Lightning (bolts.js, setFlashes): up to 16 flashes, device px (GL y-up).
    // uFl = x, y, reach, strength; uFl2 = cloud cover there, 0, 0, 0.
    'uniform vec4 uFl[16], uFl2[16];',
    'uniform int uFlN;',
    'out vec4 o;',
    'vec3 aces(vec3 x){ return clamp(x * (2.51 * x + 0.03) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0); }',
    'void main(){',
    '  vec4 c = uFade > 0.0 ? texture(uCl, gl_FragCoord.xy / uRes) : vec4(0.0);',
    '  float a = c.a * uFade * (1.0 - uDim * 0.6);',
    '  vec3 col = c.a > 0.002 ? aces(c.rgb / c.a) : vec3(0.0);',
    '  col = mix(col, cGround, uDim * 0.85);',
    '  vec3 add = vec3(0.0);',
    '  if (uSunS.z > 0.5){',
    '    float d = length(gl_FragCoord.xy - uSunS.xy) / uDpr;',
    '    float vis = smoothstep(uGlobe.z * 0.99, uGlobe.z * 1.03, length(uSunS.xy - uGlobe.xy));',
    '    add = vec3(1.0, 0.94, 0.84) * (smoothstep(7.0, 5.0, d) + exp(-d / 30.0) * 0.45 + exp(-d / 150.0) * 0.12) * vis * (1.0 - uDim * 0.7);',
    '  }',
    // A flash lights what is there: the cloud drawn at this pixel (its
    // opacity, so the light takes the shape of the real puffs), and - where
    // the cloud is thin or open - faintly the ground under it. Falls off
    // with the square of the distance from the channel, smoothly to nothing
    // at its reach; nothing over empty space.
    '  if (uFlN > 0){',
    '    float ca = c.a * uFade, inG = 1.0 - smoothstep(uGlobe.z * 0.995, uGlobe.z * 1.005, length(gl_FragCoord.xy - uGlobe.xy));',
    '    vec3 fc = vec3(0.0); float fg = 0.0;',
    '    for (int i = 0; i < 16; i++){',
    '      if (i >= uFlN) break;',
    '      vec2 d = gl_FragCoord.xy - uFl[i].xy;',
    '      float rr = uFl[i].z, q = dot(d, d) / (rr * rr);',
    '      if (q >= 1.0) continue;',
    '      float g = uFl[i].w / (1.0 + q * 16.0) * (1.0 - q) * (1.0 - q);',
    '      fc += vec3(g); fg += g * (1.0 - uFl2[i].x * 0.7);',
    '    }',
    '    float lc = 1.0 - exp(-fc.x * 1.4);',
    // Bluish where it is faint (scattered through the cloud), whiter where bright.
    '    vec3 tint = mix(vec3(0.62, 0.72, 1.0), vec3(0.95, 0.97, 1.0), smoothstep(0.3, 0.9, lc));',
    '    add += tint * lc * ca * (0.35 + 0.65 * ca);',
    '    add += vec3(0.62, 0.7, 0.9) * (1.0 - exp(-fg)) * 0.22 * (1.0 - ca) * inG;',
    '  }',
    '  o = vec4(col * a + add, a);',
    '}'
  ].join('\n');

  /* A stored frame copied back as it is (premultiplied, pixel for pixel):
   * the still globe under a lightning frame (_draw, fb.base). */
  var BFS = [
    '#version 300 es',
    'precision highp float;',
    'uniform sampler2D uB;',
    'out vec4 o;',
    'void main(){ o = texelFetch(uB, ivec2(gl_FragCoord.xy), 0); }'
  ].join('\n');

  /* Wind waves on the open sea (setWind), drawn over the stored still globe
   * (fb.base) and under the cloud composite, so the animation never makes
   * the terrain re-shade or the clouds re-march.
   *
   * A wave is far smaller than a pixel at globe scale, so the crests are
   * drawn at a fixed screen size (WAVE_PX) - exaggerated, and the legend
   * says by how much. The pattern is a tile of a directional sea built once
   * (waveTile), laid along the local wind. The wind turns across the ocean,
   * and a pattern rotated per pixel would shear; instead the direction is
   * quantized into 12 fixed orientations and each pixel blends the two
   * nearest, each of which is seamless on its own (directional flow, as in
   * Vlachos 2010). Wavelength is fixed for the same reason; the wind speed
   * sets how steep the sea is, how much long sea there is under the chop,
   * and how many crests break white (from ~7 m/s, Beaufort 4). Two layers
   * scroll downwind at deep-water speeds (shorter waves cover more
   * wavelengths a second), so the pattern moves and changes. Out: light to
   * add, and in alpha how much of the flat sea to take away. */
  /* The wave look: every tunable of the wind on the sea (the ?tune=1 panel
   * edits these live, stage.setWave). */
  var WAVE = {
    long: 18,        // long sea, crest to crest, CSS px
    chop: 8,         // chop, crest to crest, CSS px
    speed: 1.0,      // how fast the crests run
    steep: 0.3,      // wave slopes (tuned by eye, 2026-09-27)
    sky: 1.0,        // sky light in the faces (what draws the crests)
    sparkle: 1.0,    // sun sparkle on the crests
    foam: 1.0,       // whitecaps on the crests
    glint: 1.0,      // how much the wind spreads the Sun's glint (Cox-Munk)
    caps: 1.0,       // whitecap brightening of the whole sea (Monahan)
    wind: 1.0,       // wind speed multiplier, to preview calm or gale
    fps: 24          // wave frames a second
  };
  var WAVE0 = JSON.parse(JSON.stringify(WAVE));
  // The real sea the crests are compared with: the peak wavelength of a
  // fully developed sea at 10 m/s (Pierson-Moskowitz, w_p = 0.855 g / U,
  // L = 2 pi g / w_p^2), about 88 m.
  var REAL_SEA_M = 2 * Math.PI * 10 * 10 / (0.855 * 0.855 * 9.81);
  Stage.REAL_SEA_M = REAL_SEA_M;
  var WFS = [
    '#version 300 es',
    'precision highp float;',
    'uniform sampler2D uElev, uBio, uWxA, uWxB, uWave;',
    'uniform vec2 uCtr;',
    'uniform float uR, uLon0, uLat0, uT, uFade, uMix, uWx, uFlat, uMoonK;',
    'uniform vec2 uLam;',                        // the two wavelengths, globe radii
    'uniform vec4 uWv0, uWv1;',                  // WAVE: steep, sky, sparkle, foam; speed, wind
    'uniform vec3 uSun, uMoon;',
    WIND,
    'out vec4 o;',
    'const float PI = 3.14159265;',
    'float metres(float c){',
    '  c *= 255.0;',
    '  if (c > 128.0){ float t = (c - 128.0) / 127.0; return t * t * 8500.0; }',
    '  float d = (128.0 - c) / 127.0; return -pow(d, 1.0 / 0.65) * 9000.0;',
    '}',
    'vec2 uvOf(vec3 n, out float lat){',
    '  float sl = sin(uLat0), cl = cos(uLat0);',
    '  lat = asin(clamp(n.y * cl + n.z * sl, -1.0, 1.0));',
    '  float lon = uLon0 + atan(n.x, n.z * cl - n.y * sl);',
    '  return vec2(fract(lon / (2.0 * PI) + 0.5), 0.5 - lat / PI);',
    '}',
    'float cover(vec2 uv, float lod){ return mix(textureLod(uWxA, uv, lod).r, textureLod(uWxB, uv, lod).r, uMix); }',
    // One orientation: height and screen-plane slope of both layers.
    'vec3 sea(vec2 q, float ang, float kL, float kC){',
    '  vec2 D = vec2(cos(ang), sin(ang)), P = vec2(-D.y, D.x);',
    '  float a = dot(q, D), b = dot(q, P);',
    // Tile: 6 wavelengths along, crests across. Phase speed per wavelength
    // goes as 1/sqrt(wavelength): chop moves ~1.5x as many crests a second.
    // Explicit gradients: the two orientations switch between neighbouring
    // pixels, and the hardware's own derivatives would pick a wrong mip there.
    '  float gL = 1.0 / (6.0 * uLam.x * uR), gC = 1.0 / (6.0 * uLam.y * uR);',
    '  vec4 L = textureGrad(uWave, vec2(a / uLam.x - uT * 0.42 * uWv1.x, b / uLam.x) / 6.0, vec2(D.x, P.x) * gL, vec2(D.y, P.y) * gL);',
    '  float c2 = cos(0.42), s2 = sin(0.42);',
    '  vec2 Dc = D * c2 + P * s2, Pc = vec2(-Dc.y, Dc.x);',
    '  vec4 C = textureGrad(uWave, vec2(dot(q, Dc) / uLam.y - uT * 0.64 * uWv1.x, dot(q, Pc) / uLam.y) / 6.0 + vec2(0.37, 0.61),',
    '                       vec2(Dc.x, Pc.x) * gC, vec2(Dc.y, Pc.y) * gC);',
    '  float h = (L.r * 2.0 - 1.0) * kL + (C.r * 2.0 - 1.0) * kC * 0.6;',
    '  vec2 g = ((L.g * 2.0 - 1.0) * D + (L.b * 2.0 - 1.0) * P) * kL + ((C.g * 2.0 - 1.0) * Dc + (C.b * 2.0 - 1.0) * Pc) * kC;',
    '  return vec3(h, g);',
    '}',
    'void main(){',
    '  vec2 q = (gl_FragCoord.xy - uCtr) / uR;',
    '  float rho2 = dot(q, q);',
    '  o = vec4(0.0);',
    '  if (rho2 >= 1.0) return;',
    '  vec3 n = vec3(q, sqrt(1.0 - rho2));',
    '  float lat; vec2 uv = uvOf(n, lat);',
    '  float h = metres(textureLod(uElev, uv, 0.0).r);',
    '  if (h >= 0.0) return;',
    // Open water only: faded off the coast, sea ice and the limb.
    '  float m = smoothstep(0.0, -40.0, metres(textureLod(uElev, uv, 1.5).r)) * smoothstep(0.06, 0.28, n.z);',
    '  m *= 1.0 - smoothstep(0.02, 0.2, textureLod(uBio, uv, 0.0).g);',
    '  vec2 w = windAt(uv) * uWv1.y; float U = length(w);',
    '  m *= smoothstep(0.3, 1.5, U) * uFade;',
    '  if (m <= 0.002) return;',
    // Downwind on the screen: the east/north tangents in view space.
    '  vec3 pole = vec3(0.0, cos(uLat0), sin(uLat0));',
    '  vec3 E = normalize(cross(pole, n) + vec3(1e-6, 0.0, 0.0)), N = cross(n, E);',
    '  vec2 d = (w.x * E + w.y * N).xy;',
    '  float ang = atan(d.y, d.x) / (2.0 * PI) * 12.0;',
    '  float i0 = floor(ang), f = smoothstep(0.0, 1.0, ang - i0);',
    '  float kL = 0.15 + 0.85 * smoothstep(3.0, 15.0, U), kC = 0.35 + 0.4 * smoothstep(0.5, 7.0, U) - 0.25 * smoothstep(8.0, 18.0, U);',
    '  vec3 s = mix(sea(q, i0 * PI / 6.0, kL, kC), sea(q, (i0 + 1.0) * PI / 6.0, kL, kC), f);',
    '  float steep = (0.22 + 0.5 * smoothstep(2.0, 16.0, U)) * uWv0.x;',
    // The screen-plane slope lifted onto the sphere's tangent plane.
    '  vec2 g = s.yz * steep;',
    '  vec3 G = vec3(g, -dot(g, n.xy) / max(n.z, 0.1));',
    '  vec3 nw = normalize(n - G);',
    '  vec3 S = uSun, hv = normalize(S + vec3(0.0, 0.0, 1.0));',
    '  float mu = dot(n, S), day = smoothstep(-0.12, 0.10, mu);',
    '  vec3 dusk = mix(vec3(1.0, 0.52, 0.30), vec3(1.0), smoothstep(0.0, 0.28, mu));',
    // Clouds: the flat layer lies over the sea in the base; their shadows too.
    '  float att = 1.0;',
    '  if (uWx > 0.5){',
    '    float la2; float sc = cover(uvOf(normalize(n + S * (0.026 / max(mu, 0.2))), la2), 2.5);',
    '    att = (1.0 - 0.85 * cover(uv, 1.0) * uFlat) * (1.0 - 0.55 * sc * day);',
    '  }',
    // Sunlit and shaded faces, and the glint broken into sparkle: light
    // added where a facet turns toward the Sun, taken away where it turns off.
    '  float dl = dot(nw, S) - dot(n, S);',
    '  float sp = pow(max(dot(nw, hv), 0.0), 90.0), sp0 = pow(max(dot(n, hv), 0.0), 90.0);',
    // Sky in the faces: a face tipped away from the eye mirrors more of the
    // bright sky (Fresnel), one facing it shows the dark water - this is
    // what draws the crests outside the glint.
    '  float fr = (n.z - nw.z) * 6.0 + dot(nw.xy - n.xy, normalize(n.xy + 1e-4)) * 1.5;',
    '  vec3 add = vec3(0.16, 0.22, 0.28) * dusk * max(dl, 0.0) * day * 1.4 + vec3(1.0, 0.95, 0.85) * dusk * min(max(sp - sp0, 0.0) * 0.45, 0.35) * uWv0.z * day;',
    '  add += vec3(0.30, 0.40, 0.52) * dusk * clamp(fr, 0.0, 1.0) * 0.55 * uWv0.y * day;',
    '  float dark = (max(-dl, 0.0) * 0.55 + max(sp0 - sp, 0.0) * 0.3 * uWv0.z + clamp(-fr, 0.0, 1.0) * 0.3 * uWv0.y) * day;',
    '  vec3 hm = normalize(uMoon + vec3(0.0, 0.0, 1.0));',
    '  add += vec3(0.5, 0.6, 0.85) * uMoonK * pow(max(dot(nw, hm), 0.0), 90.0) * 0.5 * (1.0 - day) * step(0.0, dot(n, uMoon));',
    // Whitecaps: the steepest crests break, more of them the harder it blows.
    '  float wc = smoothstep(6.0, 18.0, U), foam = smoothstep(0.58 - 0.18 * wc, 0.95, s.x / (kL + 0.6 * kC)) * wc;',
    '  float lit = mix(0.03 + 0.2 * uMoonK * max(dot(n, uMoon), 0.0), 0.35 + 0.65 * max(mu, 0.0), day);',
    '  float fa = min(foam * 0.55 * uWv0.w, 0.9);',
    '  add = add * (1.0 - fa) + vec3(0.86, 0.9, 0.94) * mix(vec3(1.0), dusk, day) * lit * fa;',
    '  float a = clamp(dark * (1.0 - fa) + fa, 0.0, 0.9);',
    '  float k = m * att;',
    '  o = vec4(add * k, a * k);',
    '}'
  ].join('\n');

  /* The wave tile: a directional sea with 6 crests along x, seamless both
   * ways (whole wavenumbers only), from a narrow spectrum around the peak
   * with a cos^4 spread, random phases (seeded: the same sea every load).
   * RGBA8: height, slope along, slope across (all to -1..1), unused. */
  function waveTile(N) {
    var comps = [], seed = 7;
    function rnd() { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; }
    for (var kx = 2; kx <= 11; kx++) for (var ky = -5; ky <= 5; ky++) {
      var k = Math.sqrt(kx * kx + ky * ky), th = Math.atan2(ky, kx);
      var a = Math.exp(-Math.pow((k - 6) / 1.8, 2)) * Math.pow(Math.cos(th), 4) / k;
      if (a > 0.004) comps.push([kx, ky, a, rnd() * 6.2832]);
    }
    var n = N * N, H = new Float32Array(n), X = new Float32Array(n), Y = new Float32Array(n), mh = 0, ms = 0;
    for (var c = 0; c < comps.length; c++) {
      var C = comps[c], fx = 6.2832 * C[0] / N, fy = 6.2832 * C[1] / N;
      for (var y = 0; y < N; y++) for (var x = 0; x < N; x++) {
        var ph = fx * x + fy * y + C[3], i = y * N + x;
        // Trochoidal crests: a little of the second harmonic sharpens
        // crests and flattens troughs, as in a real sea.
        var sn = Math.sin(ph), cs = Math.cos(ph);
        H[i] += C[2] * (sn - 0.18 * Math.cos(2 * ph));
        var dd = C[2] * (cs + 0.36 * Math.sin(2 * ph));
        X[i] += dd * C[0]; Y[i] += dd * C[1];
      }
    }
    for (var j = 0; j < n; j++) { mh = Math.max(mh, Math.abs(H[j])); ms = Math.max(ms, Math.abs(X[j]), Math.abs(Y[j])); }
    var out = new Uint8Array(n * 4);
    for (j = 0; j < n; j++) {
      out[j * 4] = Math.round(127.5 + 127.5 * H[j] / mh);
      out[j * 4 + 1] = Math.round(127.5 + 127.5 * X[j] / ms);
      out[j * 4 + 2] = Math.round(127.5 + 127.5 * Y[j] / ms);
      out[j * 4 + 3] = 255;
    }
    return out;
  }

  /* The Moon: a lit sphere with NASA's LRO colour map, drawn as one point
   * sprite at its real direction and in its real phase. Its disc is drawn
   * five times its true size (a true-size Moon is 8 px here). */
  var MVS = [
    '#version 300 es',
    'uniform vec2 uPos; uniform float uSize;',
    'void main(){ gl_Position = vec4(uPos, 0.0, 1.0); gl_PointSize = uSize; }'
  ].join('\n');
  var MFS = [
    '#version 300 es',
    'precision highp float;',
    'uniform sampler2D uTexM;',
    'uniform vec3 uMv, uSun, uUp;',
    'uniform float uFade;',
    'out vec4 o;',
    'void main(){',
    '  vec2 pc = gl_PointCoord * 2.0 - 1.0; pc.y = -pc.y;',
    '  float r2 = dot(pc, pc);',
    '  if (r2 > 1.0) discard;',
    '  vec3 f = -uMv, rt = normalize(cross(uUp, f)), up = cross(f, rt);',
    '  vec3 nn = rt * pc.x + up * pc.y + f * sqrt(1.0 - r2);',
    '  vec2 uv = vec2(atan(dot(nn, rt), dot(nn, f)) / 6.2831853 + 0.5, 0.5 - asin(clamp(dot(nn, up), -1.0, 1.0)) / 3.14159265);',
    '  vec3 alb = texture(uTexM, uv).rgb;',
    '  float lit = smoothstep(-0.02, 0.12, dot(nn, uSun));',
    '  vec3 col = alb * (lit * 1.35 + 0.035);',        // earthshine keeps the dark limb faintly there
    '  float a = smoothstep(1.0, 0.92, r2) * uFade;',
    '  o = vec4(col * a, a);',
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
   * UTC on the data date, turned by uRot to the sidereal time of now when
   * the live sky is on. They go through the same camera rotation as the
   * globe and a pinhole projection behind it, so orbiting the Earth turns
   * the sky with it. Size and brightness follow magnitude; colour is the
   * star's own B-V tint. Nothing is drawn that is not a real star. */
  var SVS = [
    '#version 300 es',
    'in vec2 aPos; in vec3 aCol; in float aMag;',
    'uniform float uLon0, uLat0, uF, uDpr, uFade, uRot;',
    'uniform vec2 uRes;',
    'out vec3 vCol; out float vA; out float vPx; out float vGlow; out float vDpr;',
    'void main(){',
    '  float lon = aPos.x * 3.14159265 - uRot, lat = aPos.y * 1.5707963, dl = lon - uLon0;',
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

  /* Quality tiers for the volume. `move` and `still` are the buffer scale
   * relative to the canvas while the camera moves and once it rests; `acc`
   * is how many jittered frames a resting view is refined over; `dpr` caps
   * the device pixel ratio the whole stage is drawn at; `stepK` is the
   * march step in shell heights. Ultra is for strong GPUs: on a 2x laptop
   * screen high drew the volume at 1.5 x 0.8 = 1.2 px per CSS px, about 60%
   * of the panel's resolution each way, and that softness read as pixels. */
  var TIERS = {
    ultra: { move: 0.6, still: 1.0, steps: 96, lsteps: 6, acc: 8, dpr: 2, stepK: 0.06 },
    high: { move: 0.5, still: 0.8, steps: 72, lsteps: 5, acc: 6, dpr: 1.5, stepK: 0.09 },
    mid:  { move: 0.34, still: 0.55, steps: 40, lsteps: 3, acc: 4, dpr: 1.5, stepK: 0.09 },
    flat: null
  };
  /* The cloud look: every tunable of the soft look in one place (the ?tune=1
   * panel, tune.js, edits these live; "Copy values" there gives JSON to
   * paste here). Packed into four vec4 uniforms, uLk0-uLk3. */
  var LOOK = {
    density: 1.0,    // extinction scale for the whole cloud
    rim: 0.35,       // extinction at a cloud's outer rim (1 = as solid as its core)
    rimWidth: 0.25,  // density band over which the rim becomes solid
    veil: 0.45,      // extinction of thin satellite cover (veils), vs thick cover
    lump: 0.6,       // lumpy, domed tops instead of flat plates
    billow: 0.8,     // puffy (billowing) detail on the upper part of dense cloud
    shadow: 1.0,     // self-shadowing toward the Sun
    glow: 0.8,       // multiple scattering: light glowing through thin cloud
    powder: 0.35,    // darker fringes, brighter fronts of puffs
    ambient: 1.0,    // sky light
    sun: 1.0,        // sunlight
    types: 1.0,      // strength of the estimated cloud types
    cirrus: 1.0,     // strength of cirrus veils
    flat: 1.0        // opacity of the flat cloud layer
  };
  var LOOK0 = JSON.parse(JSON.stringify(LOOK));
  var ORDER = ['flat', 'mid', 'high', 'ultra'];
  // Texture units, fixed for the life of the context.
  var U_ELEV = 0, U_BIO = 1, U_WXA = 2, U_WXB = 3, U_RAIN = 4, U_LIGHTS = 5, U_MOON = 6, U_NOISE = 7, U_CL = 8, U_DET = 9, U_WIND = 10, U_WAVE = 11;

  function Stage(canvas, marks, opts) {
    this.c = canvas; this.mc = marks; this.opts = opts;
    this.cam = { lon: 26, lat: 30, k: 0.4, cx: 0.68, cy: 0.52, dim: 0 };
    this.from = null; this.to = null; this.t0 = 0; this.dur = 900;
    this.spin = 0; this.dirty = true; this.markers = [];
    this.live = false; this.sky = null; this.wx = null; this.mix = 1; this.mixT0 = 0;
    this.acc = 0; this.fb = {}; this._lastMove = 0; this._dts = [];
    this._reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.wave = Object.assign({}, WAVE);
    var gl = this.gl = canvas.getContext('webgl2', { antialias: true, alpha: true, premultipliedAlpha: true });
    if (!gl) { this.failed = true; return; }
    this.prog = this._program(VS, FS);
    this.vaoGlobe = gl.createVertexArray();
    gl.bindVertexArray(this.vaoGlobe);
    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.bindVertexArray(null);
    this.u = this._uniforms(this.prog, ['uElev', 'uBio', 'uWxA', 'uWxB', 'uRain', 'uLights', 'uRes', 'uCtr', 'uTex', 'uR',
      'uLon0', 'uLat0', 'uDim', 'uHasTex', 'uLight', 'uDisp', 'uSun', 'uMoon', 'uLive', 'uMoonK', 'uWx', 'uFlat', 'uMix',
      'uHasRain', 'uHasLights', 'uRainOn', 'cAbyss', 'cShelf', 'cForest', 'cDesert', 'cRock', 'cIce', 'cShore', 'cAtmo', 'cGround', 'cLand',
      'uDet', 'uDetBox', 'uHasDet', 'uDetLod', 'uSoft', 'uLk3', 'uWind', 'uWG', 'uWN', 'uWindOn', 'uWvG']);
    gl.useProgram(this.prog);
    [['uElev', U_ELEV], ['uBio', U_BIO], ['uWxA', U_WXA], ['uWxB', U_WXB], ['uRain', U_RAIN], ['uLights', U_LIGHTS], ['uDet', U_DET], ['uWind', U_WIND]]
      .forEach(function (s) { gl.uniform1i(this.u[s[0]], s[1]); }, this);
    // The main globe's displacement law (src/web/globe.js:193-207).
    var RE = 6371000, TOP_M = 8500;
    gl.uniform3f(this.u.uDisp, 100 / RE, 20 * Math.sqrt(TOP_M) / RE, 20 * TOP_M / RE);
    this.hasTex = 0;
    this.pinned = false; this.showClouds = true; this.showRain = true;
    // ?crisp=0: the old look, for comparison - no warp, no zoom erosion, no
    // city-detail frame.
    this.crisp = new URLSearchParams(location.search).get('crisp') !== '0';
    // ?soft=0: the old cloud look (opaque thin cloud, single-scatter light,
    // hard flat layer), for comparison.
    this.soft = new URLSearchParams(location.search).get('soft') !== '0';
    canvas.dataset.soft = this.soft ? '1' : '0';
    // ?types=0: one kind of cloud at every height, for comparison.
    this.types = new URLSearchParams(location.search).get('types') !== '0';
    this.look = Object.assign({}, LOOK);
    this.tier = this._detectTier();
    canvas.dataset.tier = this.tier;
    this._loadTextures();
    this._loadStars();
    this.readTheme();
    this._bindDrag();
    var self = this;
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
    gl.bindAttribLocation(p, 0, 'p');          // every full-screen pass shares one triangle
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    gl.useProgram(p);
    return p;
  };

  Stage.prototype._uniforms = function (p, names) {
    var gl = this.gl, u = {};
    names.forEach(function (n) { u[n] = gl.getUniformLocation(p, n); });
    return u;
  };

  /* Software renderers get the flat layer; phones and small screens the
   * lighter volume; strong desktop GPUs ultra. ?sky=ultra|high|mid|flat|off
   * overrides, for review, and pins the tier so the frame-time adaptation
   * leaves it alone. */
  Stage.prototype._detectTier = function () {
    var q = new URLSearchParams(location.search).get('sky');
    if (q && (q in TIERS || q === 'off')) { this.pinned = true; return q === 'off' ? 'flat' : q; }
    return this._deviceTier();
  };

  /* What this device gets when nobody has chosen. Ultra goes to desktop
   * GPUs known to carry it; any other capable device starts on high and
   * earns ultra by holding the display's full rate (_adapt). What the tab
   * has learnt - ultra earned, or a ceiling after a step down - is kept for
   * the session, so the next city page starts where this one ended. */
  Stage.prototype._deviceTier = function () {
    var gl = this.gl, ext = gl.getExtension('WEBGL_debug_renderer_info');
    var r = String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    if (/swiftshader|llvmpipe|softpipe|software|basic render/i.test(r)) return 'flat';
    if (matchMedia('(pointer: coarse)').matches || Math.min(innerWidth, innerHeight) < 600) return 'mid';
    var t = 'high';
    if ((navigator.hardwareConcurrency || 0) >= 8 &&
        /nvidia|geforce|quadro|rtx|radeon|apple m\d|apple gpu|intel\(r\) arc/i.test(r)) t = 'ultra';
    if (sess('rc-sky-tier') === 'ultra') t = 'ultra';
    var cap = sess('rc-sky-cap');
    if (cap && ORDER.indexOf(cap) >= 0 && ORDER.indexOf(t) > ORDER.indexOf(cap)) t = cap;
    return t;
  };

  /* The device pixel ratio the stage (and the overlays drawn over it) use. */
  Stage.prototype.dpr = function () {
    var q = TIERS[this.tier];
    return Math.min(devicePixelRatio || 1, q ? q.dpr : 1.5);
  };

  /* Frame times while the camera moves, sorted: step down one tier when the
   * volume cannot hold about 20 fps (35 on ultra), and never climb above
   * that again in this tab; step high up to ultra, once, when it holds the
   * display's full rate with almost no dropped frames. rAF is tied to the
   * display, so "fast" means the shortest frames are one refresh long and
   * the slow tail stays close to them. */
  Stage.prototype._adapt = function (d) {
    var mean = d.reduce(function (a, b) { return a + b; }, 0) / d.length;
    var fast = d[Math.floor(d.length * 0.05)], p90 = d[Math.floor(d.length * 0.9)];
    var i = ORDER.indexOf(this.tier);
    if (i > 0 && mean > (this.tier === 'ultra' ? 28 : 50)) {
      sess('rc-sky-cap', ORDER[i - 1]);
      if (this.tier === 'ultra') sess('rc-sky-tier', null);
      this.setTier(ORDER[i - 1]);
      return;
    }
    if (this.tier === 'high' && !this._promoted && !sess('rc-sky-cap') && fast < 20 && p90 < fast * 1.3 &&
        !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      this._promoted = true;
      sess('rc-sky-tier', 'ultra');
      this.setTier('ultra');
    }
  };

  /* 8 bytes a star: int16 lon/pi, int16 lat/(pi/2), rgb, magnitude byte. */
  Stage.prototype._loadStars = function () {
    var gl = this.gl, self = this;
    if (!this.opts.stars) return;
    fetch(this.opts.stars).then(function (r) { return r.arrayBuffer(); }).then(function (ab) {
      var p = self.sprog = self._program(SVS, SFS);
      self.su = self._uniforms(p, ['uLon0', 'uLat0', 'uF', 'uDpr', 'uFade', 'uRes', 'uRot']);
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

  Stage.prototype._image = function (url, unit, mip) {
    var gl = this.gl;
    return fetch(url).then(function (r) { if (!r.ok) throw new Error(r.status); return r.blob(); })
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
  };

  Stage.prototype._loadTextures = function () {
    var gl = this.gl, self = this;
    Promise.all([this._image(this.opts.elev, U_ELEV, true), this._image(this.opts.biome, U_BIO, false)]).then(function (imgs) {
      gl.useProgram(self.prog);
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
    this.ground = hex(v('--ground'));
    this._mk = markStyle(v); this._sprites = {};
    this.dirty = true;
  };

  /* ── Live sky API ─────────────────────────────────────── */

  /* On: the real Sun, Moon, sidereal stars and weather. Off: the report's
   * fixed studio light, exactly as before. */
  Stage.prototype.setLive = function (on) {
    if (this.failed) return;
    this.live = !!on; this.acc = 0; this.dirty = true;
    if (this.live && !this._skyAssets) {
      this._skyAssets = true;
      var self = this;
      if (this.opts.lights) this._image(this.opts.lights, U_LIGHTS, true).then(function () { self.hasLights = 1; self.acc = 0; self.dirty = true; }).catch(function () {});
      if (this.opts.moon) this._image(this.opts.moon, U_MOON, true).then(function () { self.hasMoon = 1; self.dirty = true; }).catch(function () {});
      try {
        this.kprog = this._program(VS, KFS);
        this.ku = this._uniforms(this.kprog, ['uCl', 'uRes', 'uDim', 'uFade', 'uDpr', 'cGround', 'uSunS', 'uGlobe', 'uFl', 'uFl2', 'uFlN']);
        this.gl.uniform1i(this.ku.uCl, U_CL);
        this.mprog = this._program(MVS, MFS);
        this.mu = this._uniforms(this.mprog, ['uPos', 'uSize', 'uTexM', 'uMv', 'uSun', 'uUp', 'uFade']);
        this.gl.uniform1i(this.mu.uTexM, U_MOON);
      } catch (e) { this.kprog = this.mprog = null; }
      try {
        this.bprog = this._program(VS, BFS);
        this.gl.uniform1i(this._uniforms(this.bprog, ['uB']).uB, U_CL);
      } catch (e) { this.bprog = null; }
    }
  };

  /* Where the Sun and Moon stand (world unit vectors, see AtlasSky.vec),
   * how much of the Moon is lit, and how far the stars have turned since
   * the epoch they were packed for (radians). */
  Stage.prototype.setSky = function (s) {
    this.sky = s; this.acc = 0; this.dirty = true;
  };

  Stage.prototype.setCityDir = function (v) { this.cityW = v; this.acc = 0; this.dirty = true; };

  function tex2(gl, unit, t, w, h, data) {
    gl.activeTexture(gl.TEXTURE0 + unit);
    t = t || gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RG8, w, h, 0, gl.RG, gl.UNSIGNED_BYTE, data);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    return t;
  }

  /* Satellite fields from AtlasSky.weather(), one layer or both. A new cloud
   * field arrives with `a` (what the page was showing) and `b` (the new
   * one); when they differ it cross-fades from a to b once, so a fresher
   * frame, or the Meteosat frame landing on the world mosaic, eases in
   * rather than popping. */
  Stage.prototype.setWeather = function (wx) {
    if (this.failed || !wx) return;
    var gl = this.gl, T = this._wxTex = this._wxTex || {};
    if (wx.ir) {
      T.a = tex2(gl, U_WXA, T.a, wx.ir.w, wx.ir.h, wx.ir.a);
      T.b = tex2(gl, U_WXB, T.b, wx.ir.w, wx.ir.h, wx.ir.b);
      this.hasWx = 1; this._wxH = wx.ir.h;
      var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (wx.ir.a !== wx.ir.b && !reduce) { this.mix = 0; this.mixT0 = performance.now(); } else this.mix = 1;
    }
    if (wx.rain) { T.r = tex2(gl, U_RAIN, T.r, wx.rain.w, wx.rain.h, wx.rain.d); this.hasRain = 1; }
    this.acc = 0; this.dirty = true;
  };

  /* The city-detail frame (AtlasSky.detail), or null to drop it. RGBA:
   * cover, top, weight; mipmapped, since the zoomed-out globe shrinks its
   * 1024 texels into a hundred-odd pixels. */
  Stage.prototype.setDetail = function (det) {
    if (this.failed) return;
    var gl = this.gl;
    if (!det || !this.crisp) { this.det = null; this.c.dataset.detail = ''; this.acc = 0; this.dirty = true; return; }
    gl.activeTexture(gl.TEXTURE0 + U_DET);
    this._detTex = this._detTex || gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this._detTex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, det.w, det.h, 0, gl.RGBA, gl.UNSIGNED_BYTE, det.d);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    this.det = { box: det.box, w: det.w, h: det.h };
    this.c.dataset.detail = det.box.map(function (v) { return v.toFixed(2); }).join(',');
    this.acc = 0; this.dirty = true;
  };

  /* The mip of the detail frame for a globe of radius Rpx (in the pixels of
   * the target being drawn): about one texel per pixel. */
  Stage.prototype._detLod = function (Rpx) {
    var d = this.det, px = (d.box[3] - d.box[1]) * DEG * Rpx;
    return Math.max(0, Math.log2(d.h / Math.max(px, 1)));
  };

  /* The detail frame's uniforms on the bound program, for a globe of
   * radius Rpx in that program's target. */
  Stage.prototype._detUniforms = function (u, Rpx) {
    var gl = this.gl, d = this.det;
    gl.uniform1f(u.uHasDet, d ? 1 : 0);
    if (!d) return;
    gl.uniform4f(u.uDetBox, d.box[0], d.box[1], d.box[2], d.box[3]);
    gl.uniform1f(u.uDetLod, this._detLod(Rpx));
  };

  Stage.prototype.setNoise = function (data, N) {
    if (this.failed) return;
    var gl = this.gl, t = gl.createTexture();
    gl.activeTexture(gl.TEXTURE0 + U_NOISE);
    gl.bindTexture(gl.TEXTURE_3D, t);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage3D(gl.TEXTURE_3D, 0, gl.RG8, N, N, N, 0, gl.RG, gl.UNSIGNED_BYTE, data);
    [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T, gl.TEXTURE_WRAP_R].forEach(function (p) { gl.texParameteri(gl.TEXTURE_3D, p, gl.REPEAT); });
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.generateMipmap(gl.TEXTURE_3D);
    gl.texParameteri(gl.TEXTURE_3D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    this.hasNoise = 1; this.acc = 0; this.dirty = true;
    // The volume fades in over the flat layer rather than replacing it at once.
    this.volT0 = matchMedia('(prefers-reduced-motion: reduce)').matches ? -1e9 : performance.now();
    mark('noise');
  };

  /* The quality tier in use; a slow device steps down by itself. */
  Stage.prototype.setTier = function (t) {
    if (!(t in TIERS)) return;
    // A driver that could not compile the volume only ever has the flat layer.
    if (t !== 'flat' && this.cprog === null) t = 'flat';
    this.tier = t; this.acc = 0; this.dirty = true; this._dts = [];
    this.c.dataset.tier = t;
    this.opts.onTier && this.opts.onTier(t);
  };

  /* The look (LOOK), or part of it; null resets to the defaults. */
  Stage.prototype.setLook = function (o) {
    this.look = o ? Object.assign({}, this.look, o) : Object.assign({}, LOOK0);
    this.acc = 0; this.dirty = true;
    return this.look;
  };
  Stage.prototype.lookDefaults = function () { return Object.assign({}, LOOK0); };
  /* Switches for the comparisons (?soft, ?types) and the tier, live. */
  Stage.prototype.setSwitch = function (k, v) {
    if (k === 'soft' || k === 'types') { this[k] = !!v; this.c.dataset[k] = v ? '1' : '0'; }
    this.acc = 0; this.dirty = true;
  };
  /* Lightning drawn as light on the scene (bolts.js): whether the stage can
   * - it needs the 3-D cloud buffer to know where cloud is drawn; the flat
   * layer and a docked globe fall back to bolts' own sprites - and the
   * flashes of this frame, [{x, y, r, a, cl}] in CSS px (null: none). */
  Stage.prototype.flashOK = function () { return !!(this.kprog && this._volW > 0 && !this.failed); };
  Stage.prototype.setFlashes = function (list, key) {
    if (key === this._flKey) return;
    this._flKey = key; this.flashes = list && list.length ? list : null; this._flDirty = true;
  };

  /* The Sun direction, overridden for tuning (a world vector, or null). */
  Stage.prototype.setSunOverride = function (v) { this.sunOv = v; this.acc = 0; this.dirty = true; };

  /* The 10 m wind for the sea (wind.js): grid {lon: [lon0, dlon, nc],
   * lat: [lat0, dlat, nr]} and the field {u, v} (Float32, m/s, row-major
   * from lat0) for the page clock; null takes the wind off the sea. The
   * glint and whitecaps (FS) change with it at once; the waves (WFS) are
   * animated over the stored globe when the view rests. Changing the field
   * re-shades the globe but keeps the finished clouds. */
  Stage.prototype.setWind = function (grid, field) {
    if (this.failed) return;
    var gl = this.gl;
    if (!grid || !field) {
      if (this.wind) { this.wind = null; this.dirty = true; }
      this.c.dataset.wind = '0';
      return;
    }
    var nc = grid.lon[2], nr = grid.lat[2], n = nc * nr, d = new Float32Array(n * 2);
    for (var i = 0; i < n; i++) { d[i * 2] = field.u[i]; d[i * 2 + 1] = field.v[i]; }
    gl.activeTexture(gl.TEXTURE0 + U_WIND);
    this._windTex = this._windTex || gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this._windTex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RG16F, nc, nr, 0, gl.RG, gl.FLOAT, d);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    this.wind = { g: [grid.lon[0], grid.lon[1], grid.lat[0], grid.lat[1]], n: [nc, nr] };
    this.c.dataset.wind = '1';
    this.dirty = true; this._waveDone = false;
  };

  /* The wind uniforms on a bound program (FS or WFS). */
  Stage.prototype._windU = function (u, live) {
    var gl = this.gl, W = this.wind, on = !!(W && live);
    gl.uniform1f(u.uWindOn, on ? 1 : 0);
    if (!on) return;
    gl.uniform4f(u.uWG, W.g[0], W.g[1], W.g[2], W.g[3]);
    gl.uniform2f(u.uWN, W.n[0], W.n[1]);
    var V = this.wave;
    if (u.uWvG) gl.uniform3f(u.uWvG, V.glint, V.caps, V.wind);
    if (u.uWv0) gl.uniform4f(u.uWv0, V.steep, V.sky, V.sparkle, V.foam);
    if (u.uWv1) gl.uniform4f(u.uWv1, V.speed, V.wind, 0, 0);
  };

  /* The wave look (WAVE), or part of it; null resets to the defaults.
   * Re-shades the globe (glint, whitecaps) but keeps the finished clouds. */
  Stage.prototype.setWave = function (o) {
    this.wave = o ? Object.assign({}, this.wave, o) : Object.assign({}, WAVE0);
    this.dirty = true; this._waveDone = false;
    return this.wave;
  };
  Stage.prototype.waveDefaults = function () { return Object.assign({}, WAVE0); };
  Stage.prototype._wavePx = function () {
    return [Math.max(2, this.wave.long), Math.max(2, this.wave.chop)];
  };

  Stage.prototype._waveProgram = function () {
    if (this.wprog !== undefined) return this.wprog;
    var gl = this.gl;
    try {
      this.wprog = this._program(VS, WFS);
      var wu = this.wu = this._uniforms(this.wprog, ['uElev', 'uBio', 'uWxA', 'uWxB', 'uWave', 'uWind', 'uWG', 'uWN', 'uWindOn',
        'uCtr', 'uR', 'uLon0', 'uLat0', 'uT', 'uFade', 'uMix', 'uWx', 'uFlat', 'uMoonK', 'uLam', 'uSun', 'uMoon', 'uWv0', 'uWv1']);
      [['uElev', U_ELEV], ['uBio', U_BIO], ['uWxA', U_WXA], ['uWxB', U_WXB], ['uWave', U_WAVE], ['uWind', U_WIND]]
        .forEach(function (s) { gl.uniform1i(wu[s[0]], s[1]); });
      var N = 128, t = gl.createTexture();
      gl.activeTexture(gl.TEXTURE0 + U_WAVE);
      gl.bindTexture(gl.TEXTURE_2D, t);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, N, N, 0, gl.RGBA, gl.UNSIGNED_BYTE, waveTile(N));
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    } catch (e) {
      if (global.console) console.warn('live sky: waves unavailable', e);
      this.wprog = null;
    }
    return this.wprog;
  };

  /* Whether the view is one the waves are drawn on, and how strongly: the
   * wind on the sea, the live sky, the globe near and undimmed. Crests only
   * ever show over a resting view (they are screen-sized, and would swim
   * over a moving globe); they ease in over half a second once it rests. */
  Stage.prototype._waveWant = function () {
    var c = this.cam;
    if (!this.wind || !this.live || !this.sky || !this.hasTex || this.failed) return 0;
    return Math.max(0, Math.min(1, (0.3 - c.dim) / 0.15)) * Math.max(0, Math.min(1, (c.k - 0.3) / 0.08));
  };

  /* How many times the crests are drawn larger than a real wind sea at
   * 10 m/s (peak wavelength ~0.83 U^2 m, Pierson-Moskowitz: ~83 m), in the
   * current view; null when no waves show. */
  Stage.prototype.waveScale = function () {
    if (!this._waveWant()) return null;
    // The tile's energy-weighted crest spacing is 1.02x 'long' (waveTile),
    // and a globe radius at the disc centre is _radius() CSS px.
    var km = this._wavePx()[0] * 1.02 / this._radius() * 6371;
    return { km: km, times: km * 1000 / REAL_SEA_M };
  };

  Stage.prototype._drawWaves = function (gl, c, w, h, R, dpr, S, Mo, volW, wx, now, fade) {
    if (fade <= 0 || !this._waveProgram()) return false;
    var wu = this.wu;
    gl.useProgram(this.wprog);
    gl.bindVertexArray(this.vaoGlobe);
    gl.uniform2f(wu.uCtr, c.cx * w, h - c.cy * h);
    gl.uniform1f(wu.uR, R);
    gl.uniform1f(wu.uLon0, c.lon * DEG); gl.uniform1f(wu.uLat0, c.lat * DEG);
    gl.uniform1f(wu.uT, this._reduce ? 0 : (now / 1000) % 1000);
    gl.uniform1f(wu.uFade, fade);
    gl.uniform1f(wu.uMix, this.mix);
    gl.uniform1f(wu.uWx, wx ? 1 : 0);
    gl.uniform1f(wu.uFlat, 1 - volW);
    gl.uniform1f(wu.uMoonK, this.sky.moonK);
    var px = this._wavePx();
    gl.uniform2f(wu.uLam, px[0] * dpr / R, px[1] * dpr / R);
    gl.uniform3fv(wu.uSun, S); gl.uniform3fv(wu.uMoon, Mo);
    this._windU(wu, true);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    this.waveN = (this.waveN || 0) + 1;       // wave frames drawn (perf_atlas.py, check_atlas.py)
    return true;
  };
  Stage.prototype._lookU = function (u) {
    var L = this.look, gl = this.gl;
    if (u.uLk0) gl.uniform4f(u.uLk0, L.density, L.rim, L.rimWidth, L.veil);
    if (u.uLk1) gl.uniform4f(u.uLk1, L.lump, L.billow, L.shadow, L.glow);
    if (u.uLk2) gl.uniform4f(u.uLk2, L.powder, L.ambient, L.sun, L.types);
    if (u.uLk3) gl.uniform4f(u.uLk3, L.cirrus, L.flat, 0, 0);
  };

  /* Which weather layers the reader wants: clouds and rain, each on or off. */
  Stage.prototype.setLayers = function (clouds, rain) {
    this.showClouds = !!clouds; this.showRain = !!rain;
    this.acc = 0; this.dirty = true;
  };

  Stage.prototype._volProgram = function () {
    if (this.cprog !== undefined) return this.cprog;
    try {
      this.cprog = this._program(VS, CFS);
      var cu = this.cu = this._uniforms(this.cprog, ['uWxA', 'uWxB', 'uRain', 'uLights', 'uNoise', 'uCtr', 'uJit', 'uR', 'uMix',
        'uFrame', 'uT', 'uHasRain', 'uHasLights', 'uMoonK', 'uThin', 'uW2V', 'uSun', 'uMoon', 'uCity', 'uSteps', 'uLSteps', 'uRainOn',
        'uWarp', 'uEro', 'uStepK', 'uDet', 'uDetBox', 'uHasDet', 'uDetLod', 'uSoft', 'uTypes', 'uLk0', 'uLk1', 'uLk2', 'uLk3']);
      var gl = this.gl;
      [['uWxA', U_WXA], ['uWxB', U_WXB], ['uRain', U_RAIN], ['uLights', U_LIGHTS], ['uNoise', U_NOISE], ['uDet', U_DET]]
        .forEach(function (s) { gl.uniform1i(cu[s[0]], s[1]); });
    } catch (e) {
      // A driver that cannot compile the volume keeps the flat layer.
      if (global.console) console.warn('live sky: volume unavailable, flat clouds instead', e);
      this.cprog = null; this.setTier('flat');
    }
    return this.cprog;
  };

  Stage.prototype._fbo = function (name, w, h) {
    var gl = this.gl, f = this.fb[name];
    if (f && f.w === w && f.h === h) return f;
    if (!f) { f = this.fb[name] = { t: gl.createTexture(), f: gl.createFramebuffer() }; }
    gl.activeTexture(gl.TEXTURE0 + U_CL);
    gl.bindTexture(gl.TEXTURE_2D, f.t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindFramebuffer(gl.FRAMEBUFFER, f.f);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, f.t, 0);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    f.w = w; f.h = h;
    if (name === 'still') this.acc = 0;
    return f;
  };

  Stage.prototype.setMarkers = function (list) {
    this.markers = list; this.dirty = true;
  };

  /* Fly to a named camera state. Reduced motion jumps. */
  Stage.prototype.fly = function (state, instant) {
    var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.from = Object.assign({}, this.cam);
    // Take the short way round in longitude.
    var d = ((state.lon - this.from.lon + 540) % 360) - 180;
    this.to = Object.assign({}, state, { lon: this.from.lon + d });
    this.spin = reduce ? 0 : state.spin || 0;
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
      last = [e.clientX, e.clientY]; self.dirty = true; self._lastMove = performance.now();
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

  /* World -> view rotation for the current camera, as rows. The same
   * rotation as project() and the shaders' uvOf(). */
  function w2v(c) {
    var l0 = c.lon * DEG, p0 = c.lat * DEG, cl = Math.cos(l0), sl = Math.sin(l0), cp = Math.cos(p0), sp = Math.sin(p0);
    return [[cl, 0, -sl], [-sp * sl, cp, -sp * cl], [cp * sl, sp, cp * cl]];
  }
  function mul(M, v) {
    return [M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2],
            M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2],
            M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2]];
  }
  function halton(i, b) { var f = 1, r = 0; while (i > 0) { f /= b; r += f * (i % b); i = Math.floor(i / b); } return r; }

  var MIX_MS = 6000, VOL_IN_MS = 700;

  Stage.prototype._frame = function (now) {
    var moving = false, dt = Math.min(100, Math.max(0, now - (this._prev || now)));
    if (this.to) {
      var t = this.dur ? Math.min(1, (now - this.t0) / this.dur) : 1, e = ease(t), f = this.from, g = this.to;
      ['lon', 'lat', 'k', 'cx', 'cy', 'dim'].forEach(function (k) {
        this.cam[k] = f[k] + (g[k] - f[k]) * e;
      }, this);
      if (t >= 1) this.to = null;
      this.dirty = true; moving = true;
    } else if (this.spin && !document.hidden) {
      // spin is degrees per 60 Hz frame; by the clock, so a 120 Hz screen
      // turns as fast and no faster. Dimmed behind the chapters, 1.8 deg/s
      // of turn needs no more than ~30 redraws a second.
      this.cam.lon += this.spin * dt * 0.06; moving = true;
      if (this.cam.dim < 0.5 || now - (this._spunT || 0) >= 30) { this.dirty = true; this._spunT = now; }
    }
    if (now - this._lastMove < 160) moving = true;
    /* The two IR frames cross-fade. This is not camera motion: treated as
     * motion, the volume re-seeded its jitter and noise every frame at the
     * low 'move' resolution and shimmered until the fade ended (and the
     * frame-time adaptation could step the tier down meanwhile). Instead each
     * step restarts the still frame's accumulation from one fixed sample, so
     * the pattern holds steady while the clouds morph. */
    if (this.mix < 1 && this.live) {
      this.mix = Math.min(1, (now - this.mixT0) / MIX_MS); this.dirty = true;
      if (!moving) this.acc = 0;
    }
    var q = TIERS[this.tier];
    var fadeIn = this.volT0 !== undefined && this._volOn ? (now - this.volT0) / VOL_IN_MS : 1;
    if (fadeIn < 1) { this.dirty = true; if (!moving) this.acc = 0; }
    var refine = this._volOn && q && !moving && this.acc < q.acc;
    // Lightning changed: redraw the globe and the composite over the cloud
    // buffer already built - no new ray-march - at most 30 times a second.
    var flash = this._flDirty && now - (this._flT || 0) >= 32;
    // The city ring pulses only over a still, undimmed globe: moved while it
    // animates, it made Chrome re-layerize the whole page every frame (the
    // reading view at 2x spent all its main thread there; perf_atlas.py).
    var still = !moving && !this.spin && !this.to && this.cam.dim < 0.2;
    if (still !== this._still) { this._still = still; this._pulseDirty = true; }
    /* Waves: over a resting view only, eased in from when it came to rest,
     * at 24 fps (one still frame with reduced motion). A wave frame is a
     * lightning-style frame: the stored globe, the waves, the finished
     * clouds over them. */
    var ww = this._waveWant(), rest = ww > 0 && !moving && !this.to && !this.spin && !document.hidden;
    if (!rest) { this._restT = null; this._waveDone = false; }
    else if (this._restT == null) this._restT = now;
    this._wfade = rest ? ww * (this._reduce ? 1 : Math.min(1, (now - this._restT) / 500)) : 0;
    var wave = rest && (this._reduce ? !this._waveDone : now - (this._waveT || 0) >= 1000 / Math.max(1, this.wave.fps) - 1);
    // The last frame drew waves and none are due now: one clean frame.
    if (!rest && this._waveDrawn) { this.dirty = true; this._waveDrawn = false; }
    if (!this.dirty && !refine && !flash && !wave) {
      if (this._pulseDirty) this._placePulse();
      this._prev = now; return;
    }
    var flashOnly = !this.dirty && !refine;
    if (flash || !flashOnly) { this._flDirty = false; this._flT = now; }
    if (rest) { this._waveT = now; this._waveDone = true; }
    this.dirty = false;
    if (moving) { this.acc = 0; this._tStill = now / 1000; }
    var t0 = performance.now();
    this._draw(moving, now, flashOnly);
    if (!flashOnly) this._drawMarkers();
    // CPU-side cost of the frame, smoothed (the ?tune=1 panel shows it; the
    // GPU time is only visible as the frame interval, frameMs).
    this.cpuMs = (this.cpuMs || 0) * 0.9 + (performance.now() - t0) * 0.1;
    if (this._prev) this.frameMs = (this.frameMs || 16) * 0.9 + Math.min(now - this._prev, 300) * 0.1;
    // Adapt the tier to the frame times while the volume moves (_adapt).
    if (moving && this._volOn && this._prev && !document.hidden) {
      var dt = now - this._prev;
      if (dt < 300) this._dts.push(dt);
      if (this._dts.length >= 60) {
        var d = this._dts.sort(function (a, b) { return a - b; });
        this._dts = [];
        if (!this.pinned) this._adapt(d);
      }
    }
    this._prev = now;
  };

  Stage.prototype._draw = function (moving, now, flashOnly) {
    if (this.failed) return;
    var gl = this.gl, dpr = this.dpr();
    var w = Math.round(innerWidth * dpr), h = Math.round(innerHeight * dpr);
    if (this.c.width !== w || this.c.height !== h) { this.c.width = w; this.c.height = h; }
    gl.viewport(0, 0, w, h);
    var c = this.cam, u = this.u, R = this._radius() * dpr;
    var F = Math.max(w, h) / 2 / Math.tan(50 * DEG);
    var live = this.live && !!this.sky, M = w2v(c);
    var S = live ? mul(M, this.sunOv || this.sky.sun) : [0, 0, 1], Mo = live ? mul(M, this.sky.moon) : [0, 0, 1];
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);   // premultiplied
    // How much of the volume to show: none behind reading or docked.
    var q = TIERS[this.tier], wx = live && this.hasWx && this.showClouds;
    var rainOn = live && this.hasRain && this.showRain;
    var volW = wx && q && this.hasNoise && this._volProgram()
      ? Math.max(0, Math.min(1, (0.55 - c.dim) / 0.2)) * Math.max(0, Math.min(1, (c.k - 0.14) / 0.08)) : 0;
    if (volW > 0 && this.volT0 !== undefined) {
      var vin = Math.max(0, Math.min(1, (now - this.volT0) / VOL_IN_MS));
      volW *= vin * vin * (3 - 2 * vin);
      if (vin >= 1) mark('volume');
    }
    // Kept on through the fade, so the frame loop keeps drawing it.
    this._volOn = volW > 0 || (wx && q && this.hasNoise && this.volT0 !== undefined && now - this.volT0 < VOL_IN_MS);
    this._volW = volW;
    /* A lightning frame changes only the composite's light. The stars, Moon
     * and globe under it are the last still frame's, kept in fb.base, so it
     * copies them back instead of shading the terrain again: a flash frame
     * cost a full globe draw, 30 times a second while any storm flashed
     * (2.4-3.5 ms of GPU each on the city view; perf_atlas.py). */
    var base = this.fb.base;
    if (flashOnly && this._baseOK && base && base.w === w && base.h === h) {
      gl.bindVertexArray(this.vaoGlobe);
      gl.useProgram(this.bprog);
      gl.activeTexture(gl.TEXTURE0 + U_CL);
      gl.bindTexture(gl.TEXTURE_2D, base.t);
      gl.disable(gl.BLEND);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      gl.enable(gl.BLEND);
      this.baseN = (this.baseN || 0) + 1;   // flash frames served from fb.base
    } else {
      this._drawGlobe(gl, c, u, w, h, R, F, dpr, live, M, S, Mo, volW, rainOn, wx);
      // Keep this still frame for the lightning and wave frames that follow
      // (only when those can show: the volume or the waves are on and
      // nothing moves).
      this._baseOK = false;
      if (!moving && live && this.bprog && (volW > 0 || this._wfade > 0 || this._waveWant() > 0)) {
        base = this._fbo('base', w, h);
        gl.activeTexture(gl.TEXTURE0 + U_CL);
        gl.bindTexture(gl.TEXTURE_2D, base.t);
        gl.copyTexSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 0, 0, w, h);
        this._baseOK = true;
      }
    }
    var drew = !moving && live && this._drawWaves(gl, c, w, h, R, dpr, S, Mo, volW, wx, now, this._wfade || 0);
    // Only on a change, so the DOM is not touched 24 times a second.
    if (drew !== this._waveAttr) { this._waveAttr = drew; this.c.dataset.waves = drew ? '1' : '0'; }
    this._waveDrawn = drew;
    if (!live || !this.kprog) return;
    this._composite(gl, c, w, h, R, F, dpr, M, S, Mo, q, volW, rainOn, moving, now, flashOnly);
  };

  /* Stars first; the Moon; the globe (opaque where it covers, glow
   * elsewhere) on top. */
  Stage.prototype._drawGlobe = function (gl, c, u, w, h, R, F, dpr, live, M, S, Mo, volW, rainOn, wx) {
    if (this.nStars) {
      var su = this.su;
      gl.useProgram(this.sprog); gl.bindVertexArray(this.vaoStars);
      gl.uniform1f(su.uLon0, c.lon * DEG); gl.uniform1f(su.uLat0, c.lat * DEG);
      // A 100-degree field across the wider side of the window: wide enough
      // that the frame holds a real night's worth of stars, not a keyhole.
      gl.uniform1f(su.uF, F);
      gl.uniform1f(su.uDpr, dpr); gl.uniform1f(su.uFade, 1 - c.dim * 0.75);
      gl.uniform2f(su.uRes, w, h);
      gl.uniform1f(su.uRot, live ? this.sky.rot : 0);
      gl.drawArrays(gl.POINTS, 0, this.nStars);
    }
    gl.bindVertexArray(this.vaoGlobe);
    this.moonPx = null;
    if (live && this.hasMoon && this.mprog && Mo[2] < -0.05) {
      var mu = this.mu, mx = Mo[0] / -Mo[2] * F, my = Mo[1] / -Mo[2] * F;
      var size = Math.min(2 * F * Math.tan(0.26 * 5 * DEG), gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE)[1]);
      gl.useProgram(this.mprog);
      gl.uniform2f(mu.uPos, mx / (w / 2), my / (h / 2));
      gl.uniform1f(mu.uSize, size);
      gl.uniform3fv(mu.uMv, Mo); gl.uniform3fv(mu.uSun, S); gl.uniform3fv(mu.uUp, mul(M, [0, 1, 0]));
      gl.uniform1f(mu.uFade, 1 - c.dim * 0.75);
      gl.drawArrays(gl.POINTS, 0, 1);
      this.moonPx = [(w / 2 + mx) / dpr, (h / 2 - my) / dpr];
    }
    gl.useProgram(this.prog);
    gl.uniform2f(u.uRes, w, h);
    gl.uniform2f(u.uCtr, c.cx * w, h - c.cy * h);
    gl.uniform1f(u.uR, R);
    gl.uniform1f(u.uLon0, c.lon * DEG);
    gl.uniform1f(u.uLat0, c.lat * DEG);
    gl.uniform1f(u.uDim, c.dim);
    gl.uniform1f(u.uHasTex, this.hasTex);
    gl.uniform1f(u.uLive, live ? 1 : 0);
    gl.uniform3fv(u.uSun, S); gl.uniform3fv(u.uMoon, Mo);
    gl.uniform1f(u.uMoonK, live ? this.sky.moonK : 0);
    gl.uniform1f(u.uWx, wx ? 1 : 0);
    gl.uniform1f(u.uFlat, 1 - volW);
    gl.uniform1f(u.uMix, this.mix);
    gl.uniform1f(u.uHasRain, this.hasRain ? 1 : 0);
    gl.uniform1f(u.uHasLights, this.hasLights ? 1 : 0);
    gl.uniform1f(u.uRainOn, rainOn ? 1 : 0);
    gl.uniform1f(u.uSoft, this.soft ? 1 : 0);
    this._windU(u, live);
    this._lookU(u);
    this._detUniforms(u, R);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };

  /* The cloud volume (ray-marched into its buffer, or the finished still
   * buffer reused) composited over the globe with the Sun's glare and the
   * lightning. */
  Stage.prototype._composite = function (gl, c, w, h, R, F, dpr, M, S, Mo, q, volW, rainOn, moving, now, flashOnly) {
    var src = null;
    if (volW > 0 && flashOnly && this.fb.still && this.acc >= q.acc) src = this.fb.still.t;
    else if (volW > 0) {
      var mode = moving ? 'move' : 'still', sc = q[mode];
      var fw = Math.max(64, Math.round(w * sc)), fh = Math.max(64, Math.round(h * sc));
      var fb = this._fbo(mode, fw, fh), cu = this.cu;
      var wgt = moving ? 1 : 1 / (this.acc + 1);
      gl.bindFramebuffer(gl.FRAMEBUFFER, fb.f);
      gl.viewport(0, 0, fw, fh);
      if (wgt >= 1) gl.disable(gl.BLEND);
      else { gl.enable(gl.BLEND); gl.blendColor(0, 0, 0, wgt); gl.blendFunc(gl.CONSTANT_ALPHA, gl.ONE_MINUS_CONSTANT_ALPHA); }
      gl.useProgram(this.cprog);
      gl.uniform2f(cu.uCtr, c.cx * fw, fh - c.cy * fh);
      gl.uniform1f(cu.uR, R * fw / w);
      gl.uniform2f(cu.uJit, moving ? 0 : halton(this.acc + 1, 2) - 0.5, moving ? 0 : halton(this.acc + 1, 3) - 0.5);
      gl.uniformMatrix3fv(cu.uW2V, false, [M[0][0], M[1][0], M[2][0], M[0][1], M[1][1], M[2][1], M[0][2], M[1][2], M[2][2]]);
      gl.uniform3fv(cu.uSun, S); gl.uniform3fv(cu.uMoon, Mo);
      gl.uniform3fv(cu.uCity, this.cityW || [0, 0, 0]);
      gl.uniform1f(cu.uThin, this.cityW ? 0.4 : 0);
      gl.uniform1f(cu.uMoonK, this.sky.moonK);
      gl.uniform1f(cu.uMix, this.mix);
      gl.uniform1f(cu.uFrame, moving ? (now / 16.7) % 64 : this.acc);
      gl.uniform1f(cu.uT, moving ? now / 1000 : this._tStill || 0);
      gl.uniform1f(cu.uHasRain, rainOn ? 1 : 0);
      gl.uniform1f(cu.uRainOn, rainOn ? 1 : 0);
      gl.uniform1f(cu.uHasLights, this.hasLights ? 1 : 0);
      gl.uniform1i(cu.uSteps, q.steps); gl.uniform1i(cu.uLSteps, q.lsteps);
      gl.uniform1f(cu.uStepK, q.stepK);
      gl.uniform1f(cu.uSoft, this.soft ? 1 : 0);
      this._lookU(cu);
      gl.uniform1f(cu.uTypes, this.types ? 1 : 0);
      // How many buffer pixels one world-field texel spans: past one, its
      // grid starts to show, so the warp comes in and the edges erode
      // harder, up to 1.5x at four pixels a texel (the city close-up).
      var tpx = R * fw / w * Math.PI / (this._wxH || 1024);
      gl.uniform1f(cu.uWarp, this.crisp ? Math.max(0, Math.min(1.5, (tpx - 0.6) * 1.5)) : 0);
      gl.uniform1f(cu.uEro, this.crisp ? 1 + 0.5 * Math.max(0, Math.min(1, (tpx - 1) / 3)) : 1);
      this._detUniforms(cu, R * fw / w);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, w, h);
      gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      if (!moving) this.acc++;
      src = fb.t;
    }
    // Composite the volume and the Sun's glare.
    var ku = this.ku;
    gl.useProgram(this.kprog);
    gl.activeTexture(gl.TEXTURE0 + U_CL);
    gl.bindTexture(gl.TEXTURE_2D, src);
    gl.uniform2f(ku.uRes, w, h);
    gl.uniform1f(ku.uDim, c.dim);
    gl.uniform1f(ku.uFade, src ? volW : 0);
    gl.uniform1f(ku.uDpr, dpr);
    gl.uniform3fv(ku.cGround, this.ground || [0, 0, 0]);
    var sunUp = S[2] < -0.05;
    gl.uniform3f(ku.uSunS, sunUp ? w / 2 + S[0] / -S[2] * F : 0, sunUp ? h / 2 + S[1] / -S[2] * F : 0, sunUp ? 1 : 0);
    gl.uniform3f(ku.uGlobe, c.cx * w, h - c.cy * h, R);
    var fl = src && this.flashes, nf = fl ? Math.min(16, fl.length) : 0;
    if (nf) {
      var A = this._flA || (this._flA = new Float32Array(64)), B = this._flB || (this._flB = new Float32Array(64));
      for (var i = 0; i < nf; i++) {
        var e = fl[i];
        A[i * 4] = e.x * dpr; A[i * 4 + 1] = h - e.y * dpr; A[i * 4 + 2] = Math.max(2, e.r * dpr); A[i * 4 + 3] = e.a;
        B[i * 4] = e.cl;
      }
      gl.uniform4fv(ku.uFl, A); gl.uniform4fv(ku.uFl2, B);
    }
    gl.uniform1i(ku.uFlN, nf);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };

  /* Markers: one 2D canvas (#marks), each marker stamped from a sprite drawn
   * once per look, size and pixel ratio. They used to be SVG nodes, and a
   * moving globe restyled and re-layerized all of them every frame and
   * re-rastered their drop-shadow glows: src/perf_atlas.py measured about
   * 60 % of a core spinning in the world view and 35 % in the reading view.
   * The looks are the old CSS ones (.mk.* in atlas.css, now gone), read from
   * the theme's custom properties. The "here" pulse is an HTML ring (#pulse)
   * whose CSS animation runs on the compositor; only its place is set here.
   * As SVG it repainted 60 times a second on a still page (~30 % of a core). */
  function markStyle(v) {
    var s = { accent: v('--accent').trim() || '#5cc8ff', acc: v('--accent-rgb').trim() || '92, 200, 255',
              warn: v('--warn').trim() || '#f5bd4f', bad: v('--bad').trim() || '#ff6f61',
              ink: v('--ink-strong').trim() || '#fff', mono: v('--mono').trim() || 'monospace', tc: [] };
    for (var i = 0; i < 5; i++) s.tc.push(v('--t' + i).trim());
    return s;
  }

  Stage.prototype._sprite = function (m, d) {
    var r = m.r || 4, cls = ' ' + (m.cls || '') + ' ', key = cls + r + '|' + d;
    var sp = this._sprites[key];
    if (sp) return sp;
    var s = this._mk, fill = s.accent, stroke = null, lw = 0, glow = 0, gcol = null, t;
    function has(c) { return cls.indexOf(' ' + c + ' ') >= 0; }
    if (has('city')) fill = (t = /\bt(\d)\b/.exec(cls)) && s.tc[+t[1]] ? 'rgba(' + s.tc[+t[1]] + ', .85)' : 'rgba(' + s.acc + ', .55)';
    if (has('here')) { fill = '#fff'; stroke = s.accent; lw = 3; glow = 8; gcol = 'rgba(' + s.acc + ', .9)'; }
    if (has('ok')) { fill = 'rgba(' + s.acc + ', .85)'; glow = 5; gcol = 'rgba(' + s.acc + ', .8)'; }
    if (has('broken')) { fill = null; stroke = s.warn; lw = 1.4; }
    if (has('none')) { fill = 'rgba(255, 111, 97, .12)'; stroke = s.bad; lw = 1.2; }
    // CSS drop-shadow's length is the blur's standard deviation; canvas
    // shadowBlur is twice it, in device pixels. Room for three of them.
    var pad = Math.ceil(r + lw / 2 + glow * 3 + 1), n = Math.ceil(2 * pad * d);
    var shape = document.createElement('canvas');
    shape.width = shape.height = n;
    var g = shape.getContext('2d');
    g.setTransform(d, 0, 0, d, pad * d, pad * d);
    g.beginPath(); g.arc(0, 0, r, 0, 2 * Math.PI);
    if (fill) { g.fillStyle = fill; g.fill(); }
    if (stroke) { g.strokeStyle = stroke; g.lineWidth = lw; g.stroke(); }
    var c = shape;
    if (glow) {   // the shadow of the whole shape, as drop-shadow() casts it
      c = document.createElement('canvas');
      c.width = c.height = n;
      var h = c.getContext('2d');
      h.shadowColor = gcol; h.shadowBlur = 2 * glow * d;
      h.drawImage(shape, 0, 0);
    }
    return (this._sprites[key] = { c: c, o: pad });
  };

  Stage.prototype._drawMarkers = function () {
    var cv = this.mc;
    if (!cv || !cv.getContext) return;
    var d = Math.min(devicePixelRatio || 1, 2);
    var W = Math.round(innerWidth * d), H = Math.round(innerHeight * d);
    if (cv.width !== W || cv.height !== H) { cv.width = W; cv.height = H; }
    var g = this._mg || (this._mg = cv.getContext('2d')), s = this._mk, here = null;
    if (!this._fontHook && document.fonts) {   // the label's web font may land later
      var self = this;
      this._fontHook = true;
      document.fonts.ready.then(function () { self.dirty = true; });
    }
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.globalAlpha = 1;
    g.clearRect(0, 0, W, H);
    for (var i = 0; i < this.markers.length; i++) {
      var m = this.markers[i], p = this.project(m.lon, m.lat);
      if (p[2] <= 0.02) continue;
      var a = Math.min(1, p[2] * 4), sp = this._sprite(m, d);
      g.globalAlpha = a;
      g.drawImage(sp.c, (p[0] - sp.o) * d, (p[1] - sp.o) * d);
      if (m.pulse) here = [p[0], p[1], a];
      if (m.label) {
        g.setTransform(d, 0, 0, d, 0, 0);
        g.font = '600 13px ' + s.mono;
        if ('letterSpacing' in g) g.letterSpacing = '0.78px';
        g.lineJoin = 'round'; g.lineWidth = 4; g.strokeStyle = 'rgba(0, 0, 0, .8)'; g.fillStyle = s.ink;
        var tx = p[0] + (m.r || 4) + 10, ty = p[1] + 5, txt = String(m.label).toUpperCase();
        g.strokeText(txt, tx, ty); g.fillText(txt, tx, ty);
        g.setTransform(1, 0, 0, 1, 0, 0);
      }
    }
    this._here = here;
    this._placePulse();
  };

  Stage.prototype._placePulse = function () {
    var pu = this.opts.pulse, here = this._still ? this._here : null;
    this._pulseDirty = false;
    if (!pu) return;
    if (here) {
      pu.style.transform = 'translate(' + here[0].toFixed(1) + 'px,' + here[1].toFixed(1) + 'px)';
      pu.style.opacity = here[2].toFixed(2);
    }
    if (pu.hidden !== !here) pu.hidden = !here;
  };

  global.AtlasStage = Stage;
})(window);
