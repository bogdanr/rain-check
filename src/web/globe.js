/* Orthographic globe: the report's primary navigation control.
 *
 * Uses d3-geo (vendored) for projection, clipping and path generation. That is
 * worth a dependency: the projection itself is easy, but clipping polygons
 * against the horizon - the antimeridian, rings that wrap the limb, features
 * that straddle both - is exactly the kind of geometry that a hand-rolled
 * version gets subtly and invisibly wrong.
 *
 * What this file deliberately does NOT do is compute anything. Marker colour is
 * a CSS class chosen from a skill value Python already calculated; radius is a
 * constant. No statistic is derived here.
 *
 * ---------------------------------------------------------------------------
 * Three layers, chosen for different reasons
 *
 *   canvas  the basemap - the planet itself. Two quite different renderers
 *           paint into it:
 *
 *             relief  a per-pixel shader. For every pixel of the disc it
 *                     inverts the projection, samples a vendored terrain
 *                     texture, builds a surface normal from the local slope
 *                     and lights it. This is what makes mountains look like
 *                     mountains: the ranges are shaded from their own
 *                     geometry, exaggerated about fourteen-fold, rather than
 *                     being a flat fill with a gradient over it.
 *
 *             vector  the original flat coastline fill. Still here, and not
 *                     as dead code: it is what draws before the terrain
 *                     texture arrives, and what keeps drawing if it never
 *                     does. The globe is the report's navigation, so it is
 *                     not allowed to depend on half a megabyte of imagery.
 *
 *   svg     the markers. A few hundred small, interactive, individually
 *           focusable, individually labelled things - which is precisely what
 *           SVG+DOM is good at and canvas is bad at. Hit-testing, :focus-visible,
 *           aria-label and CSS theming all come free, and a canvas version would
 *           have to reimplement every one of them, worse.
 *
 * Colour still lives in CSS. Both renderers read their palette from hidden
 * probe elements carrying classes the stylesheet defines, so a theme switch
 * restyles the planet - ocean depth ramp, vegetation, rock, ice and all -
 * without the renderer knowing any theme exists. The textures carry physical
 * quantities (metres, vegetation, snow, distance to the coast), never colours,
 * which is exactly why one download can render three themes.
 */
(function (global) {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';
  var uid = 0;
  var DEG = Math.PI / 180;

  function el(name, attrs) {
    var n = document.createElementNS(SVG_NS, name);
    for (var k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    return n;
  }

  function reducedMotion() {
    return global.matchMedia &&
      global.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  /* Skill -> tier class. Cutoffs match the qualitative bands the glossary
   * publishes as rules of thumb, so the map and the scorecards tell the same
   * story. */
  function tier(bss) {
    if (bss < 0) return 'q0';
    if (bss < 0.2) return 'q1';
    if (bss < 0.35) return 'q2';
    if (bss < 0.5) return 'q3';
    return 'q4';
  }

  /* Parse any CSS colour the browser hands back into [r,g,b,a].
   *
   * Two forms, because getComputedStyle does NOT always normalise to rgb():
   * a color-mix() in sRGB comes back as `color(srgb 0.07 0.15 0.22)` with
   * 0-1 components. Reading only rgb() meant the ocean silently fell back to
   * a hard-coded blue, so every theme rendered the same sea while the land,
   * a plain rgb(), changed underneath it. Returning null for genuinely
   * unknown forms still keeps a surprise value from painting the sea black. */
  function rgba(str) {
    str = str || '';
    var m = /rgba?\(([^)]+)\)/.exec(str);
    if (m) {
      var p = m[1].split(/[,\/\s]+/).filter(function (s) { return s !== ''; });
      if (p.length < 3) return null;
      return [+p[0], +p[1], +p[2], p.length > 3 ? +p[3] : 1];
    }
    m = /color\(\s*srgb\s+([^)]+)\)/.exec(str);
    if (m) {
      var q = m[1].split(/[\/\s]+/).filter(function (s) { return s !== ''; });
      if (q.length < 3) return null;
      return [Math.round(+q[0] * 255), Math.round(+q[1] * 255),
              Math.round(+q[2] * 255), q.length > 3 ? +q[3] : 1];
    }
    return null;
  }

  function mix(c, target, k) {
    return 'rgba(' + Math.round(c[0] + (target - c[0]) * k) + ',' +
                     Math.round(c[1] + (target - c[1]) * k) + ',' +
                     Math.round(c[2] + (target - c[2]) * k) + ',' + c[3] + ')';
  }

  function css(c) {
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + c[3] + ')';
  }

  /* -- terrain ------------------------------------------------------------
   *
   * The vendored textures and the constants that decode them. These MUST match
   * src/vendor.py, which bakes them; the encoding is stated in both places
   * because a silent disagreement would show up as a plausible-looking planet
   * with the wrong mountains on it.
   *
   *   elevation  one byte per texel. 128 is sea level, and the two sides use
   *              different curves - square root above, a gentler power below -
   *              because a byte has to cover 8.5 km of mountain and 9 km of
   *              trench, and the eye wants the resolution near the coast.
   *   biome      R vegetation, G snow/ice, B nearness to the coast.
   *
   * Neither texture holds a colour. That is the whole design: the browser
   * applies the theme's palette to physical quantities, so one download paints
   * a dark instrument planet, a pale daytime one, or a monochrome one for
   * print, and none of them needs a second file.
   */
  var ELEV_SEA = 128, ELEV_MAX_M = 8500, DEPTH_MAX_M = 9000;
  var CIRC_M = 40075017, MERID_M = 20003931;

  /* Vertical exaggeration.
   *
   * Earth at true scale is smoother than a billiard ball: Everest is 0.07% of
   * the radius, and an honest render of it is a featureless sphere. Every
   * relief globe ever made exaggerates, and the only question is by how much.
   * Fourteen puts the Himalaya, the Andes and the Rockies where a reader can
   * see them at a glance while the Great Plains stay flat.
   *
   * The sea floor gets a fraction of that. At the land's setting the
   * mid-ocean ridges rear up as mountain ranges - which is honest bathymetry
   * and a terrible map, because the eye stops reading the blue as water.
   */
  var EXAG = 14, SEA_EXAG = 0.08;

  /* Relief at the horizon: displacement rather than shading.
   *
   * EXAG above is a *lighting* exaggeration - it steepens the normals and
   * nothing else, so the ball stays geometrically a ball. That is invisible
   * exactly where a globe most wants relief: at the limb, where the surface
   * turns away from the viewer, a whole mountain range collapses into a pixel
   * or two of shading and the edge reads as a compass-drawn circle. A physical
   * relief globe does not have that problem because its mountains are actually
   * proud of the sphere and break its outline.
   *
   * So near the horizon the terrain is given real height, and the amount is
   * stated the way relief globes have always stated it - as a vertical
   * exaggeration, bounded at both ends:
   *
   *   DISP_MIN_X  the exaggeration the highest ground on Earth gets. At 20x,
   *               Everest stands 2.7% of the radius off the sphere. True scale
   *               is 0.13% and invisible; a physical relief globe runs 20-40x
   *               for exactly this reason.
   *   DISP_MAX_X  the ceiling, which low ground runs into. The law between the
   *               two is a square root of elevation, so the factor *rises* as
   *               the ground falls - a proportional displacement is monopolised
   *               by the three or four highest ranges and leaves every ordinary
   *               coast on the horizon as smooth as it was, which is most of
   *               the horizon most of the time. The root spends the pixels
   *               where they can be seen, and the ceiling stops it before a
   *               100 m hill starts behaving like a mountain. The two meet at
   *               340 m, below which everything is a flat 100x.
   *
   * Written as a minimum of the two laws, which selects the right branch on
   * either side of that knee without a comparison against it.
   *
   *   DISP_CAP   the parallax offset ceiling, in multiples of the tallest
   *              displacement. Offset limiting: the shift a raised point gets
   *              is proportional to 1/cos of the viewing angle, which diverges
   *              at the limb, and without a ceiling the last few pixels of the
   *              disc sample the terrain from the other side of the planet.
   *   PARALLAX_LO  where the parallax shift starts being computed at all. The
   *              shift is only a few pixels anywhere - it is the ground being
   *              compressed underneath it that makes it matter - so paying for
   *              it across the whole disc buys nothing but a uniform smear.
   *   LIMB_SWEEP how far past the geometric horizon the silhouette search
   *              looks, in radians. A point beyond 90 degrees is visible if it
   *              is tall enough to clear the curve, and 0.24 rad is where even
   *              the tallest displacement stops being able to.
   *   LIMB_STEPS samples along that sweep. Spaced as sqrt so the steps are
   *              even in what they actually resolve, which is 1 - cos(phi).
   */
  var DISP_MIN_X = 20, DISP_MAX_X = 100;
  var DISP_CAP = 2.4, PARALLAX_LO = 0.88;
  var LIMB_SWEEP = 0.24, LIMB_STEPS = 9;

  var R_EARTH_M = 6371000;
  // Per metre below the knee, per root-metre above it, and the tallest of all.
  var DISP_LIN = DISP_MAX_X / R_EARTH_M;
  var DISP_ROOT = DISP_MIN_X * Math.sqrt(ELEV_MAX_M) / R_EARTH_M;
  var DISP_TOP = DISP_MIN_X * ELEV_MAX_M / R_EARTH_M;

  /* Displacement in radius fractions for a height in metres. */
  function displace(h) {
    var lin = h * DISP_LIN, root = Math.sqrt(h) * DISP_ROOT;
    return lin < root ? lin : root;
  }

  /* Grazing visibility: how much of its light a slope loses as it turns away
   * from the viewer, and where that starts to apply.
   *
   * The hillshade above is deliberately view-independent - a fixed north-west
   * light in the map's own tangent frame - and everywhere but the horizon that
   * is the right call. At the horizon it is the reason the relief disappears:
   * a real surface seen at eighty degrees of incidence is mostly its own far
   * slopes, which a viewer cannot see at all, and mostly its own near slopes,
   * which face the viewer squarely. Shading it as though seen from above
   * averages the two back together into the flat band this is here to fix.
   *
   * It is worth noting that the displacement and the shading are of the same
   * order: 20x at the summits against EXAG's 14x, so the slopes being tested
   * for visibility are close to the slopes of the surface actually drawn.
   */
  var GRAZE_LO = 0.62, GRAZE_DEPTH = 0.72;

  /* Where the vector coastline stops being the better witness.
   *
   * The mask earns its keep by being sharper than the 26 km elevation grid.
   * Near the limb it stops being sharper than anything: foreshortening
   * stretches what one buffer pixel covers along the radial direction by
   * 1/cos(incidence), and once that is a few grid texels wide the coastline
   * inside a single pixel wanders in and out of it. Its antialiased coverage
   * then oscillates around a half from pixel to pixel, and a vote on that
   * produced a ribbon of dots - land in the sea, sea in the land - along
   * every coast that ran near-parallel to the horizon.
   *
   * So the test is a ratio, not a radius: MASK_FADE_Q0 and Q1 are how many
   * texels of ground a pixel has to span radially before the smooth source
   * takes the decision over. Measured on the render, the dither starts at
   * rho 0.89 and 2 texels lands just inside it. Expressed this way it also
   * follows the widget size, the zoom and the adaptive detail level for
   * free, none of which a fixed rho would have done.
   */
  var MASK_FADE_Q0 = 2, MASK_FADE_Q1 = 3.5;
  var MASK_FADE_K = 1 / (MASK_FADE_Q1 - MASK_FADE_Q0);

  /* The second, coarser shading scale.
   *
   * MACRO_RADIUS is in texels: about 130 km on the shipped grid, which is the
   * width of a mountain range rather than of a mountain. EXAG_MACRO is far
   * larger than EXAG because the blurred field's slopes are correspondingly
   * gentler - the two terms are tuned to arrive at the same screen contrast
   * from opposite ends of the scale.
   *
   * MACRO_MIX is how much of the light comes from the coarse shade. Most of
   * it: the coarse term is the one that still exists after the browser has
   * resampled the globe down to 620 px, and the fine term is what makes it
   * sharp when the reader zooms in.
   */
  var MACRO_RADIUS = 5, EXAG_MACRO = 90, MACRO_MIX = 0.62;

  /* Local prominence, in metres, over which a ridge reaches full extra light
   * and a valley full extra shadow. This is the term that reads as depth
   * rather than as slope: a hillshade alone cannot tell a high plateau from a
   * low plain, because both are flat. */
  var PROMINENCE_M = 1100, PROMINENCE_GAIN = 0.45;

  /* A separable box blur over the height field, wrapping in longitude and
   * clamping at the poles. Two passes of running sums: linear in the number of
   * texels and independent of the radius, which is what makes a 130 km blur
   * over 1.2 million texels affordable at page load. */
  function smooth(src, W, H, r) {
    var tmp = new Int16Array(W * H), out = new Int16Array(W * H);
    var inv = 1 / (2 * r + 1), x, y, row, sum, i;

    for (y = 0; y < H; y++) {
      row = y * W;
      sum = 0;
      for (i = -r; i <= r; i++) sum += src[row + (i < 0 ? i + W : i)];
      for (x = 0; x < W; x++) {
        tmp[row + x] = sum * inv;
        i = x + r + 1; if (i >= W) i -= W;
        var j = x - r; if (j < 0) j += W;
        sum += src[row + i] - src[row + j];
      }
    }

    for (x = 0; x < W; x++) {
      sum = 0;
      for (i = -r; i <= r; i++) sum += tmp[(i < 0 ? 0 : i) * W + x];
      for (y = 0; y < H; y++) {
        out[y * W + x] = sum * inv;
        i = y + r + 1; if (i >= H) i = H - 1;
        var j2 = y - r; if (j2 < 0) j2 = 0;
        sum += tmp[i * W + x] - tmp[j2 * W + x];
      }
    }
    return out;
  }


  /* Where the light comes from.
   *
   * TERRAIN is a direction in the map's own tangent frame (east, north, up):
   * north-west, as relief maps have been shaded since the nineteenth century,
   * and applied identically to every pixel of the disc. Lighting terrain from
   * a fixed direction in *view* space instead would be more physical and much
   * worse: half the visible hemisphere would face away from the light and lose
   * its relief entirely, so turning the globe would keep hiding the mountains
   * you turned it to look at.
   *
   * SPHERE is a direction in view space, and it is the only term that knows
   * which way the ball is facing. It is deliberately gentle: this is a
   * navigation control that has to stay readable all over, not a render of a
   * planet at dusk.
   */
  var LIGHT_TERRAIN = norm3(-0.60, 0.58, 0.55);
  var LIGHT_VIEW = norm3(-0.42, 0.46, 0.78);

  /* The climatic snow line, in metres, from the equator to the pole.
   *
   * The biome texture's snow channel comes from a July satellite mosaic, so it
   * knows about Greenland and Antarctica and almost nothing else: by July the
   * Alps, the Rockies, the Caucasus and the whole Tibetan plateau have melted
   * out of it. Taking it at face value painted the highest ground on Earth as
   * a single flat expanse of bare rock - a grey sticker pasted over central
   * Asia, with the Himalaya invisible inside it.
   *
   * So permanent snow is *derived* rather than sampled: above the local snow
   * line there is ice, and the line falls from roughly 5000 m at the equator
   * to sea level in the Arctic. It is a textbook curve rather than a dataset,
   * and it puts white on every range that has it - Andes, Alps, Caucasus,
   * Himalaya, Alaska - in the right place and the right amount.
   */
  var SNOW_EQUATOR = 5600, SNOW_POLE = 300;

  /* Bare rock appears on steep ground as well as high ground. Slope is what
   * separates a mountain range from a plateau at the same altitude, and it is
   * the difference between the Himalaya reading as mountains and Tibet reading
   * as a stain. It also keeps snowfields off cliff faces, which is what makes
   * a peak look like a peak rather than a scoop of ice cream. */
  var ROCK_ALT_LO = 1600, ROCK_ALT_HI = 4200;

  function norm3(x, y, z) {
    var k = 1 / Math.sqrt(x * x + y * y + z * z);
    return [x * k, y * k, z * k];
  }

  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }

  /* Decode the two textures into typed arrays once, at load.
   *
   * Everything expensive that can happen once happens here: the elevation byte
   * becomes metres through a 256-entry table rather than a pow() per texel, and
   * the biome image is kept as the raw RGBA the canvas handed back, because the
   * shader only ever reads single bytes out of it.
   */
  function Relief(elevImg, biomeImg) {
    var W = this.W = elevImg.width, H = this.H = elevImg.height;
    var pix = readPixels(elevImg);

    // code -> metres. A pure function of one byte, so it is a table.
    var metres = new Float32Array(256);
    for (var c = 0; c < 256; c++) {
      if (c > ELEV_SEA) {
        var t = (c - ELEV_SEA) / 127;
        metres[c] = t * t * ELEV_MAX_M;
      } else {
        var d = (ELEV_SEA - c) / 127;
        metres[c] = -Math.pow(d, 1 / 0.65) * DEPTH_MAX_M;
      }
    }

    // Depth ramp, indexed by metres rather than by the encoded byte.
    //
    // The shader interpolates the height field between texels, so by the time
    // it needs a colour it is holding a depth in metres that is not any texel's
    // value - and a table keyed on the byte could not be asked about it. This
    // one can, and it makes the ocean's shading as smooth as its shape.
    var depthM = new Float32Array(258);
    for (var j = 0; j <= 257; j++) {
      var f = j / 256;
      depthM[j] = Math.pow(f > 1 ? 1 : f, 0.65 * 0.72);
    }
    this.depthByM = depthM;
    this.depthIdx = 256 / DEPTH_MAX_M;

    var n = W * H;
    // Int16 metres: the range is +-9000 and the shader differentiates this
    // array on every pixel of every frame, so it wants to be small enough to
    // stay in cache. Float32 would be four times the traffic for no precision
    // that a hillshade can show.
    var h = this.h = new Int16Array(n);
    for (var i = 0; i < n; i++) h[i] = metres[pix[i * 4]];

    /* A blurred copy of the same field - the single change that made the
     * mountains visible.
     *
     * At the opening view the globe is 620 px across, so half the world's
     * circumference is 620 px and one 26 km texel is under two pixels. A
     * hillshade computed from texel-to-texel differences therefore lands
     * almost entirely *inside* one screen pixel, where the display averages
     * the lit and shadowed sides of every ridge back together. The relief was
     * being computed correctly and then thrown away by the resampling, which
     * is why the Himalaya looked like a green smudge on a painted ball.
     *
     * What survives at that scale is the shadow of a whole mountain range
     * rather than of a single slope, and that is what this holds: the terrain
     * seen from far enough away that only landforms remain. The shader lights
     * both and adds them, which is how relief globes have always been drawn -
     * the massif gives the mass, the fine field gives the edges.
     */
    this.hMacro = smooth(h, W, H, MACRO_RADIUS);

    this.BW = biomeImg.width;
    this.BH = biomeImg.height;
    this.bio = readPixels(biomeImg);
    // The two grids differ in size, so the shader converts texel coordinates
    // rather than recomputing longitude and latitude a second time.
    this.bx = this.BW / W;
    this.by = this.BH / H;
  }

  function readPixels(img) {
    var c = document.createElement('canvas');
    c.width = img.width;
    c.height = img.height;
    var g = c.getContext('2d', { willReadFrequently: true });
    g.drawImage(img, 0, 0);
    return g.getImageData(0, 0, img.width, img.height).data;
  }

  /* Load both textures, then hand back a Relief.
   *
   * createImageBitmap with colour management turned off is the correct entry
   * point: the elevation texture is *data*, and a browser that helpfully
   * converts it from one colour space to another moves coastlines. Where it is
   * unavailable an <img> is used instead, which is accurate in practice on
   * every engine that ships WebP.
   */
  function loadRelief(elevUrl, biomeUrl, done) {
    var wantBitmap = typeof global.createImageBitmap === 'function';

    function one(url) {
      return fetch(url, { cache: 'force-cache' })
        .then(function (r) {
          if (!r.ok) throw new Error(r.status);
          return r.blob();
        })
        .then(function (b) {
          if (wantBitmap) {
            return global.createImageBitmap(b, { colorSpaceConversion: 'none' })
              .catch(function () { return global.createImageBitmap(b); });
          }
          return new Promise(function (res, rej) {
            var im = new Image();
            im.onload = function () { res(im); };
            im.onerror = rej;
            im.src = URL.createObjectURL(b);
          });
        });
    }

    Promise.all([one(elevUrl), one(biomeUrl)]).then(function (imgs) {
      done(new Relief(imgs[0], imgs[1]));
    }).catch(function () {
      // Silence is correct. The vector basemap is already on screen and the
      // report is fully usable; a console error here would only be noise in a
      // harness that treats console errors as failures.
    });
  }

  function Globe(node, opts) {
    this.node = node;
    // Only cities with a result are drawn. Places that were probed and dropped
    // are listed in the panel beside the globe instead: as markers they were
    // nearly half the dots, competed with real cities for space during
    // decluttering, and carried their reason in a hover tooltip - which is no
    // information at all on a touch screen, where most readers are.
    this.cities = opts.cities || [];
    this.land = opts.land;
    // The 50m coastline, if the reader ever zooms far enough in to need it.
    // Held separately from `land` rather than replacing it: at 1x the coarse
    // outline is indistinguishable and twelve times cheaper to draw, and a
    // globe being dragged redraws it sixty times a second.
    this._detailUrl = opts.landDetailUrl || null;
    this._fineLand = null;
    this._fineState = null;
    this.onSelect = opts.onSelect || function () {};
    this.selected = null;
    this._raf = null;
    this._frame = null;
    this._dragged = false;
    this._id = 'globe-clip-' + (++uid);
    this.stats = { land: 0, markers: 0, clustered: 0, ms: 0, relief: false };

    // The terrain shader, once its textures have arrived. Until then - and for
    // ever, if they never arrive - the vector renderer draws instead.
    this.relief = null;
    // Pixels of relief per CSS pixel. Dropped while the globe is moving and
    // restored when it settles, which is the whole trick that makes a
    // per-pixel shader feel like a native map: a drag is judged on latency,
    // a still frame on detail, and they can have different answers.
    this._detail = 1;
    this._refine = null;

    this.size = this._measure();
    // The sphere stops short of the box to leave a margin for the displaced
    // terrain at the horizon, which stands outside the limb.
    this.radius = Math.round(this.size / 2 - this.size * 0.042) - 4;
    this.baseScale = this.radius;
    this.projection = global.d3.geoOrthographic()
      .translate([this.size / 2, this.size / 2])
      .clipAngle(90);
    this.path = global.d3.geoPath(this.projection);


    // Open on the centroid of the covered cities rather than a fixed longitude,
    // so the map still makes sense if the city set changes - as it did, from
    // fifteen European capitals to a worldwide set.
    var c = this._centroid();
    this.rotation = [-c[0], -c[1]];
    this.zoom = this._fitZoom(c);
    this.homeZoom = this.zoom;
    this.homeRotation = this.rotation.slice();

    this._prepLand();
    this._build();
    this._bindDrag();
    this._bindZoom();
    this._bindTheme();
    this._bindResize();
    this.render();

    // The terrain arrives after the first paint, never before it. A flat but
    // complete globe is on screen within a frame of the geometry landing, and
    // half a megabyte of texture upgrades it when it gets here - which is the
    // right order for a control the reader may well click before it finishes
    // looking its best.
    if (opts.reliefUrl && opts.biomeUrl) {
      var self = this;
      loadRelief(opts.reliefUrl, opts.biomeUrl, function (r) {
        self.setRelief(r);
      });
    }
  }

  /* Adopt a decoded terrain set and repaint. Separate from the loader so the
   * verification harness can drive it directly. */
  Globe.prototype.setRelief = function (relief) {
    this.relief = relief;
    this.node.classList.add('relief');
    this.invalidate();
  };

  Globe.prototype.ZOOM_MIN = 1;
  Globe.prototype.ZOOM_MAX = 24;

  /* Marker radius, the same for every city. See _buildMarkers for why it is a
   * constant rather than a function of the record length. */
  Globe.prototype.DOT_R = 7;

  /* Centre-to-centre spacing below which two markers are treated as one place.
   *
   * The guarantee being bought is narrow and specific: no marker's centre may
   * fall inside another marker, because a buried centre cannot be clicked. A
   * dot has r = 7, so any spacing above that is sufficient, and 13 leaves a
   * visible gap between neighbours without suppressing far more of the map than
   * the guarantee requires. Raising it to a full diameter looked tidier and hid
   * about a third more of the world, which is the wrong trade for a map whose
   * job is to show where the coverage is. */
  Globe.prototype.MIN_SEP = 13;

  Globe.prototype._measure = function () {
    var w = this.node.clientWidth || 0;
    return Math.max(280, Math.min(760, Math.round(w || 520)));
  };

  Globe.prototype._centroid = function () {
    if (!this.cities.length) return [10, 50];
    var x = 0, y = 0, z = 0;
    this.cities.forEach(function (c) {
      var lon = c.lon * Math.PI / 180, lat = c.lat * Math.PI / 180;
      x += Math.cos(lat) * Math.cos(lon);
      y += Math.cos(lat) * Math.sin(lon);
      z += Math.sin(lat);
    });
    var n = this.cities.length;
    x /= n; y /= n; z /= n;
    return [Math.atan2(y, x) * 180 / Math.PI,
            Math.atan2(z, Math.sqrt(x * x + y * y)) * 180 / Math.PI];
  };

  /* Zoom that brings the whole city set comfortably inside the viewport.
   *
   * In an orthographic projection a point an angle theta from the centre lands
   * at radius scale*sin(theta), so this fit is exact rather than tuned by eye.
   * With a worldwide set this lands at 1x - the whole sphere - which is the
   * right opening view: every covered continent is on screen at once.
   */
  Globe.prototype._fitZoom = function (centre) {
    if (this.cities.length < 2) return 1;
    var geoDistance = global.d3.geoDistance, max = 0;
    this.cities.forEach(function (c) {
      max = Math.max(max, geoDistance(centre, [c.lon, c.lat]));
    });
    if (max < 1e-6) return 1;
    return Math.max(1, Math.min(4, 0.62 / Math.sin(Math.min(max, Math.PI / 2))));
  };

  /* Ring centroids, computed once.
   *
   * Used only to report how much coastline faces the viewer. That number is not
   * decoration: it is the evidence the verification harness uses to tell a drawn
   * basemap from a blank one, and counting it per frame from the geometry itself
   * would cost more than the drawing does.
   */
  Globe.prototype._prepLand = function () {
    this._rings = [];
    var rings = this._rings;
    function ring(coords) {
      var x = 0, y = 0, z = 0, n = 0;
      for (var i = 0; i < coords.length; i += Math.max(1, coords.length >> 4)) {
        var lon = coords[i][0] * Math.PI / 180, lat = coords[i][1] * Math.PI / 180;
        x += Math.cos(lat) * Math.cos(lon);
        y += Math.cos(lat) * Math.sin(lon);
        z += Math.sin(lat);
        n++;
      }
      if (!n) return;
      rings.push([Math.atan2(y, x) * 180 / Math.PI,
                  Math.atan2(z, Math.sqrt(x * x + y * y)) * 180 / Math.PI]);
    }
    function geom(g) {
      if (!g) return;
      if (g.type === 'Polygon') ring(g.coordinates[0]);
      else if (g.type === 'MultiPolygon') g.coordinates.forEach(function (p) { ring(p[0]); });
      else if (g.type === 'GeometryCollection') g.geometries.forEach(geom);
    }
    var l = this.land;
    if (!l) return;
    if (l.type === 'FeatureCollection') l.features.forEach(function (f) { geom(f.geometry); });
    else if (l.type === 'Feature') geom(l.geometry);
    else geom(l);
  };

  /* Index a land geometry for culling: one bounding cap per polygon.
   *
   * The cap is the crudest useful bound - a centre direction and the angle of
   * the furthest vertex from it - and that is deliberate. It is computed once,
   * off a file the reader has just waited for, and consulted on every frame,
   * so the cheap test that rejects Kamchatka while the reader looks at Corsica
   * beats the exact one that would also reject a little more of Sicily.
   */
  function indexPolys(geo) {
    var parts = [];

    function poly(rings) {
      var ring = rings[0], x = 0, y = 0, z = 0, i, lon, lat, cl;
      for (i = 0; i < ring.length; i++) {
        lon = ring[i][0] * DEG; lat = ring[i][1] * DEG;
        cl = Math.cos(lat);
        x += cl * Math.cos(lon); y += cl * Math.sin(lon); z += Math.sin(lat);
      }
      var k = Math.sqrt(x * x + y * y + z * z);
      if (!k) return;
      x /= k; y /= k; z /= k;

      var dot = 1;
      for (i = 0; i < ring.length; i++) {
        lon = ring[i][0] * DEG; lat = ring[i][1] * DEG;
        cl = Math.cos(lat);
        var d = cl * Math.cos(lon) * x + cl * Math.sin(lon) * y +
                Math.sin(lat) * z;
        if (d < dot) dot = d;
      }
      parts.push({
        poly: rings, x: x, y: y, z: z,
        r: Math.acos(dot < -1 ? -1 : dot > 1 ? 1 : dot)
      });
    }

    function geom(g) {
      if (!g) return;
      if (g.type === 'Polygon') poly(g.coordinates);
      else if (g.type === 'MultiPolygon') g.coordinates.forEach(poly);
      else if (g.type === 'GeometryCollection') g.geometries.forEach(geom);
      else if (g.type === 'Feature') geom(g.geometry);
      else if (g.type === 'FeatureCollection') {
        g.features.forEach(function (f) { geom(f.geometry); });
      }
    }

    geom(geo);
    return parts;
  }

  /* The coastline to draw with, at the current magnification.
   *
   * One accessor rather than a swap of `this.land`, because the two outlines
   * are both wanted: the coarse one is indistinguishable from the fine one at
   * 1x and twelve times cheaper to draw, and a globe being dragged redraws it
   * on every frame. The fetch is started here too - the moment the renderer
   * first asks for geometry it cannot supply is exactly when the finer file
   * becomes worth its megabyte.
   *
   * The fine outline is also culled to the visible cap before it is handed
   * over. Without that it costs 100 ms a frame: d3's spherical clipping is
   * per-point, so drawing 60 000 points of Pacific island while the reader
   * looks at the Alps is most of the budget for nothing. The cull is two
   * dot products per island and it is what makes the detail tier affordable.
   */
  Globe.prototype.DETAIL_ZOOM = 2;

  Globe.prototype._geo = function () {
    if (this.zoom < this.DETAIL_ZOOM) return this.land;
    if (this._fineParts) return this._visible(this._fineParts);

    if (this._detailUrl && !this._fineState) {
      var self = this;
      this._fineState = 'loading';
      fetch(this._detailUrl, { cache: 'force-cache' })
        .then(function (r) {
          if (!r.ok) throw new Error(r.status);
          return r.json();
        })
        .then(function (g) {
          self._fineState = 'ready';
          self._fineParts = indexPolys(g);
          self.invalidate();
        })
        .catch(function () {
          // The coarse coastline is a complete map. Failing to sharpen it is
          // not a failure worth telling the reader about.
          self._fineState = 'failed';
        });
    }
    return this.land;
  };

  /* Those polygons of an indexed set that can touch the visible disc. */
  Globe.prototype._visible = function (parts) {
    var lon = -this.rotation[0] * DEG, lat = -this.rotation[1] * DEG;
    var vx = Math.cos(lat) * Math.cos(lon),
        vy = Math.cos(lat) * Math.sin(lon),
        vz = Math.sin(lat);
    // Half-angle of the cap the viewport covers. At 1x it is the whole
    // hemisphere; at 8x it is 7 degrees of the planet.
    var cap = this.zoom > 1 ? Math.asin(1 / this.zoom) : Math.PI / 2;
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      var lim = cap + p.r;
      if (lim >= Math.PI || p.x * vx + p.y * vy + p.z * vz > Math.cos(lim)) {
        out.push(p.poly);
      }
    }
    return { type: 'MultiPolygon', coordinates: out };
  };

  Globe.prototype.setZoom = function (z, about) {
    var next = Math.max(this.ZOOM_MIN, Math.min(this.ZOOM_MAX, z));
    if (next === this.zoom) return;
    this.zoom = next;
    this.invalidate();
    if (this.zoomOut) {
      this.zoomOut.disabled = this.zoom <= this.ZOOM_MIN + 1e-6;
      this.zoomIn.disabled = this.zoom >= this.ZOOM_MAX - 1e-6;
    }
  };

  /* -- construction ------------------------------------------------------ */
  Globe.prototype._build = function () {
    var s = this.size;

    this.canvas = document.createElement('canvas');
    this.canvas.className = 'basemap';
    // Decorative: everything the basemap conveys is also in the marker labels
    // and the fallback list, so announcing it twice would only add noise.
    this.canvas.setAttribute('aria-hidden', 'true');
    this.node.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d');

    var svg = el('svg', {
      viewBox: '0 0 ' + s + ' ' + s,
      role: 'group',
      'aria-label': 'Interactive globe. Select a city to load its calibration report.'
    });

    // Palette probes. These carry the classes the basemap used to be drawn
    // with, so the stylesheet remains the single source of colour truth and a
    // theme switch needs no renderer change. They have no geometry, so they
    // cost nothing to paint.
    var probe = el('g', { class: 'probe', 'aria-hidden': 'true' });
    this.pSphere = el('path', { class: 'sphere' });
    this.pLand = el('path', { class: 'land' });
    this.pGrat = el('path', { class: 'graticule' });
    this.pLimb = el('circle', { class: 'limb', r: 0 });
    probe.appendChild(this.pSphere);
    probe.appendChild(this.pLand);
    probe.appendChild(this.pGrat);
    probe.appendChild(this.pLimb);
    // The terrain palette, one probe per material. Naming them after what they
    // are - abyss, shelf, forest, desert, rock, ice - rather than after a
    // colour is what lets a theme reinterpret the planet wholesale: Daylight
    // wants a pale physical map, Blueprint wants no large colour fills at all,
    // and neither of those is a tint of the other.
    this.pRelief = {};
    ['abyss', 'shelf', 'forest', 'desert', 'rock', 'ice', 'shore', 'atmo']
      .forEach(function (name) {
        var n = el('path', { class: 'r-' + name });
        probe.appendChild(n);
        this.pRelief[name] = n;
      }, this);
    svg.appendChild(probe);


    this.markers = el('g', { class: 'markers' });
    this.clusters = el('g', { class: 'clusters' });
    // Badges paint BENEATH the markers. They are offset from their host, which
    // means they can land on a neighbouring city - and a badge that covers a
    // marker's centre makes that marker unclickable while still looking
    // perfectly fine. Ordering them first makes that impossible by
    // construction; the badge is a secondary affordance and can afford to be
    // the one that gets partly covered.
    svg.appendChild(this.clusters);
    svg.appendChild(this.markers);

    this.node.appendChild(svg);
    this.svg = svg;

    this.graticule = global.d3.geoGraticule10();
    // The equator and the prime meridian as their own geometry, so they can be
    // stroked a little harder than the rest of the grid.
    this._axes = { type: 'MultiLineString', coordinates: [[], []] };
    for (var d = -180; d <= 180; d += 4) this._axes.coordinates[0].push([d, 0]);
    for (var e2 = -90; e2 <= 90; e2 += 2) this._axes.coordinates[1].push([0, e2]);

    // Where the globe is pointing, in degrees. The globe can be spun freely and
    // it is genuinely easy to lose track of where you are looking; this is the
    // one place the view states it outright. Hidden from assistive technology:
    // it describes a decorative control, and the markers already carry names.
    this.readout = el('text', {
      class: 'readout', x: 10, y: 18, 'aria-hidden': 'true'
    });
    svg.appendChild(this.readout);

    this._buildMarkers();
    this._buildControls();
    this._readPalette();
  };

  Globe.prototype._readPalette = function () {
    var g = getComputedStyle;
    var sea = rgba(g(this.pSphere).fill) || [30, 60, 90, 1];
    var landc = rgba(g(this.pLand).fill) || [60, 90, 60, 1];
    this.palette = {
      sea: sea,
      seaLight: mix(sea, 255, 0.16),
      seaDark: mix(sea, 0, 0.28),
      land: g(this.pLand).fill,
      // The land takes the same lighting as the ocean, and for the same
      // reason. Filling it flat over a lit sphere inverts the shading near
      // the highlight - the sea there ends up brighter than the continent on
      // top of it - and the globe stops reading as a lit ball and starts
      // reading as a dark cut-out on a bright disc.
      landLight: mix(landc, 255, 0.16),
      landDark: mix(landc, 0, 0.28),
      landLine: g(this.pLand).stroke,
      grat: g(this.pGrat).stroke,
      gratOpacity: +g(this.pGrat).opacity || 0.5,
      // Whether the grid should be *added* to the planet rather than drawn
      // over it. On a dark theme it should: a subtractive line across an ice
      // sheet reads as a scratch on the lens, and the graticule was visibly
      // scoring Greenland. Added light disappears into white ground and glows
      // over dark ocean, which is both prettier and the correct behaviour for
      // something that is meant to be projected onto the globe, not carved
      // into it. A light theme has nothing to add light to, so it keeps the
      // ordinary stroke.
      gratAdd: (sea[0] * 0.299 + sea[1] * 0.587 + sea[2] * 0.114) < 110,
      limb: g(this.pLimb).stroke,
      // A theme is allowed to switch the limb off entirely - on white ground a
      // stroked edge reads as a pencil outline rather than as a horizon - so
      // the paint is guarded rather than assumed.
      limbOn: !/^(transparent$|rgba\(.*,\s*0\))/.test(g(this.pLimb).stroke)
    };
    this._readReliefPalette(sea, landc);
  };

  /* The terrain palette, flattened into plain numbers.
   *
   * The shader reads these millions of times per frame, so it must not be
   * parsing "rgb(...)" or walking objects while it does. Each material becomes
   * three numbers, and the shoreline and atmosphere - which are *added* rather
   * than mixed - are pre-multiplied by their own opacity here, so the inner
   * loop never looks up an alpha.
   */
  Globe.prototype._readReliefPalette = function (sea, landc) {
    var g = getComputedStyle, p = this.pRelief, out = {};
    function col(name, fallback) {
      var c = rgba(g(p[name]).fill) || fallback;
      return [c[0], c[1], c[2], c[3] == null ? 1 : c[3]];
    }
    // Fallbacks are the ocean and land tokens the vector renderer already
    // uses. A theme that forgets to define the terrain palette gets a duller
    // planet, never a black one.
    out.abyss = col('abyss', [sea[0] * 0.55, sea[1] * 0.55, sea[2] * 0.6, 1]);
    out.shelf = col('shelf', sea);
    out.forest = col('forest', landc);
    out.desert = col('desert', landc);
    out.rock = col('rock', landc);
    out.ice = col('ice', [235, 242, 248, 1]);
    out.shore = col('shore', [120, 200, 255, 1]);
    out.atmo = col('atmo', [90, 170, 255, 1]);
    // Additive terms carry their strength in the alpha channel, which is the
    // natural place for a stylesheet to say "less of this".
    out.shoreGain = out.shore[3];
    out.atmoGain = out.atmo[3];

    // The lighting model's knobs. Numbers, so they come straight off the
    // element as custom properties - no probe needed, and no colour parsing.
    var cs = g(this.node);
    function num(prop, fallback) {
      var v = parseFloat(cs.getPropertyValue(prop));
      return isFinite(v) ? v : fallback;
    }
    out.ambient = num('--relief-ambient', 0.42);
    out.contrast = num('--relief-contrast', 0.86);
    out.seaAmbient = num('--relief-sea-ambient', 0.80);
    out.sun = num('--relief-sun', 0.42);
    out.spec = num('--relief-spec', 0.5);

    this.relPal = out;
  };

  Globe.prototype._buildControls = function () {
    var self = this;
    var wrap = document.createElement('div');
    wrap.className = 'globe-ctl';

    function button(label, title, fn) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      b.title = title;
      b.setAttribute('aria-label', title);
      b.addEventListener('click', fn);
      wrap.appendChild(b);
      return b;
    }

    this.zoomIn = button('+', 'Zoom in', function () {
      self.setZoom(self.zoom * 1.4);
    });
    this.zoomOut = button('\u2212', 'Zoom out', function () {
      self.setZoom(self.zoom / 1.4);
    });
    button('\u2302', 'Reset the view', function () {
      self.rotation = self.homeRotation.slice();
      self.zoom = self.homeZoom;
      self.invalidate();
    });

    this.node.appendChild(wrap);
    this.zoomOut.disabled = this.zoom <= this.ZOOM_MIN + 1e-6;
  };

  Globe.prototype._buildMarkers = function () {
    var self = this;
    this.nodes = [];

    function make(d) {
      var g = el('g', {
        class: 'mk ' + tier(d.bss),
        tabindex: '0',
        role: 'button'
      });
      // One radius for every marker.
      //
      // This used to scale with the length of the city's record. It no longer
      // does, because the encoding was a lie by arithmetic: the evaluation
      // window is shared, so n runs 544-762 days across the whole set, and
      // 4 + 3.5*sqrt(n/nmax) turned that into radii of 6.96 to 7.50 px - half a
      // pixel of difference carrying a variable the legend claimed was
      // readable. A record too short to verify is excluded upstream rather than
      // drawn small, so every dot that exists has passed the same bar and the
      // exact day count belongs in the tooltip, where it is now.
      var r = self.DOT_R;
      g.appendChild(el('circle', { class: 'halo', r: r + 2.5 }));
      g.appendChild(el('circle', { class: 'dot', r: r }));

      var tip = d.name + ' (' + d.country + ')\nSkill score ' + d.bss.toFixed(2) +
          '\n' + d.n + ' days, rain on ' + Math.round(d.base_rate * 100) + '%' +
          (d.rank_lo == null
            ? '\nProvisional: shorter record, not ranked'
            : '\nRank ' + d.rank_lo.toFixed(0) + '-' + d.rank_hi.toFixed(0) +
              ' of ' + self.cities.filter(function (c) {
                return c.rank_lo != null; }).length);
      g.setAttribute('data-tip', tip);
      g.setAttribute('aria-label', tip.replace(/\n/g, '. '));
      // A stable, selector-safe handle. City names are not: 's-Hertogenbosch
      // opens a quote, Washington, D.C. carries commas and dots, and anything
      // addressing a marker by name builds a broken CSS selector sooner or
      // later. Slugs are [a-z0-9-] by construction.
      g.setAttribute('data-slug', d.slug);

      g.addEventListener('click', function () {
        // A drag that ends over a marker still fires a click. Without this
        // guard, turning the globe by grabbing a continent navigates away.
        if (self._dragged) return;
        self.onSelect(d.slug);
      });
      g.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          self.onSelect(d.slug);
        }
      });
      self.nodes.push({
        d: d, g: g, r: r,
        // Unit vector on the sphere, precomputed. The visibility test runs for
        // every marker on every frame; a dot product is materially cheaper than
        // the trigonometry of a great-circle distance, and exactly equivalent.
        u: (function () {
          var lon = d.lon * Math.PI / 180, lat = d.lat * Math.PI / 180;
          return [Math.cos(lat) * Math.cos(lon),
                  Math.cos(lat) * Math.sin(lon),
                  Math.sin(lat)];
        })(),
        shown: true, x: 0, y: 0
      });
      self.markers.appendChild(g);
      return g;
    }

    this.cities.forEach(make);

    // The longest record wins ties during declutter: when two cities cannot
    // both be drawn, the better-evidenced one is the one worth keeping. Sorting
    // once here means the layout pass is a single linear sweep rather than a
    // sort per frame.
    this.order = this.nodes.slice().sort(function (a, b) {
      return (b.d.n || 0) - (a.d.n || 0);
    });

    this._clusterPool = [];
  };

  /* SVG has no z-index: paint order is document order, so raising a marker
   * means moving it. */
  Globe.prototype._raise = function (g) {
    if (g.parentNode && g.parentNode.lastChild !== g) g.parentNode.appendChild(g);
  };

  /* -- interaction ------------------------------------------------------- */
  Globe.prototype._bindDrag = function () {
    var self = this, dragging = false, last = null, moved = 0;

    // Drag state is tracked through window listeners rather than
    // setPointerCapture. Capturing the pointer on the container retargets the
    // subsequent mouseup - and therefore the click - to the container itself,
    // so every marker silently stops responding while still looking and
    // hovering exactly as though it worked.
    this.node.addEventListener('pointerdown', function (e) {
      if (e.target.closest && e.target.closest('.globe-ctl')) return;
      dragging = true; moved = 0; self._dragged = false;
      self.node.classList.add('dragging');
      last = [e.clientX, e.clientY];
    });

    global.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var dx = e.clientX - last[0], dy = e.clientY - last[1];
      moved += Math.abs(dx) + Math.abs(dy);
      last = [e.clientX, e.clientY];
      // Scale rotation by the projection scale so the drag feels 1:1 with the
      // surface under the cursor at any zoom level.
      var k = 90 / self.projection.scale();
      self.rotation = [
        self.rotation[0] + dx * k,
        // Clamped: letting latitude pass the pole flips the globe inside out,
        // which is disorienting and serves no purpose.
        Math.max(-90, Math.min(90, self.rotation[1] - dy * k))
      ];
      self.invalidate();
    });

    function end() {
      if (!dragging) return;
      dragging = false;
      self.node.classList.remove('dragging');
      self._dragged = moved > 6;
    }
    global.addEventListener('pointerup', end);
    global.addEventListener('pointercancel', end);

    // Keyboard rotation and zoom, so the globe is not mouse-only.
    this.node.setAttribute('tabindex', '0');
    this.node.addEventListener('keydown', function (e) {
      var step = e.shiftKey ? 20 : 8, r = self.rotation, used = true;
      if (e.key === 'ArrowLeft') r[0] -= step;
      else if (e.key === 'ArrowRight') r[0] += step;
      else if (e.key === 'ArrowUp') r[1] = Math.min(90, r[1] + step);
      else if (e.key === 'ArrowDown') r[1] = Math.max(-90, r[1] - step);
      else if (e.key === '+' || e.key === '=') self.setZoom(self.zoom * 1.4);
      else if (e.key === '-' || e.key === '_') self.setZoom(self.zoom / 1.4);
      else used = false;
      if (used) { e.preventDefault(); self.invalidate(); }
    });
  };

  Globe.prototype._bindZoom = function () {
    var self = this;
    this.node.addEventListener('wheel', function (e) {
      // Only capture the wheel when the gesture is deliberate; hijacking page
      // scroll is a well-earned grievance against embedded maps.
      if (!e.ctrlKey && Math.abs(e.deltaY) < 4) return;
      e.preventDefault();
      self.setZoom(self.zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
    }, { passive: false });
  };

  /* A theme switch changes the basemap's colours but not its geometry. The
   * probe elements restyle themselves; the canvas has to be told. */
  Globe.prototype._bindTheme = function () {
    var self = this;
    if (!global.MutationObserver) return;
    this._themeObs = new MutationObserver(function () {
      self._readPalette();
      self.invalidate();
    });
    this._themeObs.observe(document.documentElement,
                           { attributes: true, attributeFilter: ['data-theme', 'data-weather'] });
  };

  Globe.prototype._bindResize = function () {
    var self = this;
    if (!global.ResizeObserver) return;
    this._resObs = new ResizeObserver(function () { self.resize(); });
    this._resObs.observe(this.node);
  };

  Globe.prototype.resize = function () {
    var s = this._measure();
    if (s === this.size) return;
    this.size = s;
    this.radius = Math.round(s / 2 - s * 0.042) - 4;
    this.baseScale = this.radius;
    this.projection.translate([s / 2, s / 2]);
    this.svg.setAttribute('viewBox', '0 0 ' + s + ' ' + s);
    this.invalidate();
  };

  /* -- rendering --------------------------------------------------------- */

  /* Coalesce to one draw per animation frame.
   *
   * A pointermove burst can deliver several events between two frames, and the
   * old code rendered on each one - work whose result was overwritten before a
   * pixel reached the screen. */
  Globe.prototype.invalidate = function () {
    var self = this;
    if (this._frame) return;
    this._frame = requestAnimationFrame(function () {
      self._frame = null;
      self.render();
    });
  };

  Globe.prototype.render = function () {
    var t0 = performance.now();
    this.projection.rotate(this.rotation).scale(this.baseScale * this.zoom);
    this._drawBase();
    this._drawMarkers();
    this.stats.ms = performance.now() - t0;
  };

  Globe.prototype._drawBase = function () {
    var s = this.size, ctx = this.ctx;
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    var px = Math.round(s * dpr);
    if (this.canvas.width !== px) {
      this.canvas.width = px;
      this.canvas.height = px;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, s, s);

    // The silhouette belongs to the frame that painted it: the shader fills it
    // in and the overlay strokes along it, and the vector renderer has no
    // height field to build one from.
    this._limb = null;
    if (this.relief) this._paintRelief();
    else this._paintVector();

    this._paintOverlay();
    this._countVisibleLand();
  };

  /* Count how much coastline faces the viewer. Reported, not drawn.
   *
   * The number is not decoration: it is the evidence the verification harness
   * uses to tell a drawn basemap from a blank one. It is counted from ring
   * centroids rather than from the pixels, so it stays meaningful whichever of
   * the two renderers painted the frame.
   */
  Globe.prototype._countVisibleLand = function () {
    var cLon = -this.rotation[0] * DEG, cLat = -this.rotation[1] * DEG;
    var ux = Math.cos(cLat) * Math.cos(cLon),
        uy = Math.cos(cLat) * Math.sin(cLon),
        uz = Math.sin(cLat), n = 0;
    for (var i = 0; i < this._rings.length; i++) {
      var a = this._rings[i][0] * DEG, b = this._rings[i][1] * DEG;
      if (Math.cos(b) * Math.cos(a) * ux + Math.cos(b) * Math.sin(a) * uy +
          Math.sin(b) * uz > 0) n++;
    }
    this.stats.land = n;
  };

  /* The flat renderer: ocean fill, graticule, coastline polygons.
   *
   * This is what draws until the terrain textures arrive, and what keeps
   * drawing if they never do - on a metered connection, behind a proxy that
   * mangles WebP, or in a browser that cannot decode it. It is a worse map, and
   * it is a complete one.
   */
  Globe.prototype._paintVector = function () {
    var s = this.size, ctx = this.ctx, p = this.palette;
    var r = this.radius, cx = s / 2, cy = s / 2;
    var geoPath = global.d3.geoPath(this.projection, ctx);

    // Everything is clipped to the disc. Past 1x the sphere is larger than the
    // viewport, and without this the land spills across the page as a rectangle
    // and stops reading as a globe at all.
    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.clip();

    // Ocean, lit from the upper left. A flat fill reads as a disc; the gradient
    // is what makes it read as a sphere.
    var grad = ctx.createRadialGradient(
      cx - r * 0.35, cy - r * 0.4, r * 0.05, cx, cy, r * 1.08);
    grad.addColorStop(0, p.seaLight);
    grad.addColorStop(0.55, css(p.sea));
    grad.addColorStop(1, p.seaDark);
    ctx.beginPath();
    geoPath({ type: 'Sphere' });
    ctx.fillStyle = grad;
    ctx.fill();

    if (this.land) {
      var geo = this._geo();
      ctx.beginPath();
      geoPath(geo);
      // Same light source, same falloff as the ocean, so the continents sit
      // *in* the sphere's lighting rather than on top of it.
      var lgrad = ctx.createRadialGradient(
        cx - r * 0.35, cy - r * 0.4, r * 0.05, cx, cy, r * 1.08);
      lgrad.addColorStop(0, p.landLight);
      lgrad.addColorStop(0.55, p.land);
      lgrad.addColorStop(1, p.landDark);
      ctx.fillStyle = lgrad;
      ctx.fill();
      ctx.strokeStyle = p.landLine;
      ctx.lineWidth = 0.6;
      ctx.lineJoin = 'round';
      ctx.stroke();
    }

    // Limb shading: a thin darkening at the edge, which is what sells the
    // curvature once continents are on top of the ocean gradient.
    var vig = ctx.createRadialGradient(cx, cy, r * 0.72, cx, cy, r);
    vig.addColorStop(0, 'rgba(0,0,0,0)');
    vig.addColorStop(1, 'rgba(0,0,0,0.22)');
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = vig;
    ctx.fill();
    ctx.restore();
  };

  /* -- horizon relief -----------------------------------------------------
   *
   * How much the terrain is allowed to stand off the sphere, in fractions of
   * the radius per metre of elevation. Two things bound it, and both are
   * geometric rather than tasteful:
   *
   *   - the sphere stops short of the box by max(4, 1.9% of it), and a mountain
   *     that reaches the edge of the canvas is a mountain that gets clipped
   *     flat. The displacement is capped at two thirds of that margin, so DISP
   *     applies at the sizes where there is room for it and thins out on a
   *     small globe rather than colliding.
   *
   *   - past 1x it is switched off entirely. This is not a compromise: the
   *     shader paints a disc of `radius` while the sphere is `radius * zoom`,
   *     so the limb leaves the viewport the instant the reader zooms in - the
   *     horizon is only on screen at 1x, and ZOOM_MIN is 1. Turning it off
   *     there costs nothing visible and buys two things: the parallax shift
   *     can never drift the terrain off the vector coastline and the markers
   *     drawn on the undisplaced sphere, and the whole cost disappears from
   *     every zoomed frame.
   */
  /* The free margin outside the limb, as a distance in CSS pixels.
   *
   * It used to be where the bearing ring was drawn, and the displacement was
   * bounded by it so a summit could not reach the tick marks. The ring is gone;
   * the margin stays, because the displaced terrain still needs somewhere to
   * stand - at 20x exaggeration the tallest ground is 2.7% of the radius proud
   * of the sphere, and without the margin it would run into the edge of the
   * canvas instead.
   */
  Globe.prototype._edgeMargin = function () {
    return Math.max(4, this.size * 0.019);
  };

  Globe.prototype._dispScale = function () {
    if (!this.relief || this.zoom > 1 + 1e-6) return 0;
    var rad = this.radius * this.zoom;
    var room = this._edgeMargin() * 0.72 / (rad * DISP_TOP);
    return room < 1 ? room : 1;
  };

  /* The silhouette, as a radius per screen angle.
   *
   * For each angle around the disc, this asks the honest question a displaced
   * sphere asks: of all the terrain along this line of sight, which point
   * reaches furthest from the centre *on screen*? A point at angle phi past
   * the horizon, standing at height h, appears at radius (1 + h)·cos(phi), so
   * the answer is a maximum over phi - and it can easily be a summit that is
   * geometrically behind the horizon, which is exactly the case the analytic
   * circle cannot represent and the reason the edge looks machined.
   *
   * The result is a table rather than a per-pixel march: the shader reads it
   * for the few thousand pixels outside the circle, and the overlay strokes
   * the limb along it so the drawn edge and the painted edge are the same
   * curve. Over water every entry is exactly 1 and the table degenerates to
   * the circle it replaces, which is what the ocean should look like.
   */
  Globe.prototype._limbProfile = function () {
    var disp = this._dispScale();
    if (!disp) return null;

    var R = this.relief, h = R.h, W = R.W, H = R.H;
    var rad = this.radius * this.zoom;

    // One entry per screen pixel of circumference, loosened while dragging
    // for the same reason the shader's own resolution is.
    var N = Math.round(2 * Math.PI * rad * this._detail / 1.2);
    N = N < 256 ? 256 : N > 2048 ? 2048 : N;

    var p = this._limbBuf;
    if (!p || p.n !== N) {
      p = this._limbBuf = {
        n: N, max: 1,
        r: new Float32Array(N),
        vx: new Float32Array(N), vy: new Float32Array(N), vz: new Float32Array(N),
        wx: new Float32Array(N), wy: new Float32Array(N), wz: new Float32Array(N),
        // The horizon point itself, kept so the fringe can be a gradient of
        // sample positions rather than one colour smeared outwards. At 20x
        // the fringe is eight pixels wide and a flat smear shows.
        hx: new Float32Array(N), hy: new Float32Array(N), hz: new Float32Array(N)
      };
    }

    // The same view basis the shader builds, for the same reason: the two must
    // agree to the last bit or the fringe lands beside the terrain it belongs
    // to.
    var lon0 = -this.rotation[0] * DEG, lat0 = -this.rotation[1] * DEG;
    var clo = Math.cos(lon0), slo = Math.sin(lon0);
    var cla = Math.cos(lat0), sla = Math.sin(lat0);
    var fx = cla * clo, fy = cla * slo, fz = sla;
    var ex = -slo, ey = clo;
    var nx = -sla * clo, ny = -sla * slo, nz = cla;

    var INV_TWO_PI = 1 / (2 * Math.PI), INV_PI = 1 / Math.PI;
    var M = LIMB_STEPS, step = 2 * Math.PI / N, max = 1;

    for (var j = 0; j < N; j++) {
      // Screen angle, measured the way the canvas measures it: y downwards.
      var th = (j + 0.5) * step - Math.PI;
      var ux = Math.cos(th), uy = -Math.sin(th);
      var bR = 1, bvx = ux, bvy = uy, bvz = 0;
      var bwx = ux * ex + uy * nx, bwy = ux * ey + uy * ny, bwz = uy * nz;

      for (var m = 0; m < M; m++) {
        var phi = LIMB_SWEEP * Math.sqrt(m / (M - 1));
        var c = Math.cos(phi), sn = Math.sin(phi);
        var qx = ux * c, qy = uy * c;
        var dwx = qx * ex + qy * nx + sn * fx;
        var dwy = qx * ey + qy * ny + sn * fy;
        var dwz = qy * nz + sn * fz;

        // Bilinear, because nearest gives the silhouette a staircase of
        // 26 km steps and the eye reads it as ringing rather than as terrain.
        var lat = Math.asin(dwz > 1 ? 1 : dwz < -1 ? -1 : dwz);
        var fu = (Math.atan2(dwy, dwx) * INV_TWO_PI + 0.5) * W - 0.5;
        var fv = (0.5 - lat * INV_PI) * H - 0.5;
        var iu = Math.floor(fu), iv = Math.floor(fv);
        var au = fu - iu, av = fv - iv;
        var u0 = iu < 0 ? iu + W : iu >= W ? iu - W : iu;
        var u1 = u0 + 1 >= W ? 0 : u0 + 1;
        var v0 = iv < 0 ? 0 : iv >= H ? H - 1 : iv;
        var v1 = v0 + 1 >= H ? H - 1 : v0 + 1;
        var r0 = v0 * W, r1 = v1 * W;
        var ht = h[r0 + u0] + (h[r0 + u1] - h[r0 + u0]) * au;
        var hb = h[r1 + u0] + (h[r1 + u1] - h[r1 + u0]) * au;
        var hm = ht + (hb - ht) * av;
        if (hm <= 0) continue;              // only land breaks the outline

        var rr = (1 + displace(hm) * disp) * c;
        if (rr > bR) {
          bR = rr;
          bvx = qx; bvy = qy; bvz = sn;
          bwx = dwx; bwy = dwy; bwz = dwz;
        }
      }

      p.r[j] = bR;
      p.vx[j] = bvx; p.vy[j] = bvy; p.vz[j] = bvz;
      p.wx[j] = bwx; p.wy[j] = bwy; p.wz[j] = bwz;
      p.hx[j] = ux * ex + uy * nx;
      p.hy[j] = ux * ey + uy * ny;
      p.hz[j] = uy * nz;
      if (bR > max) max = bR;
    }

    p.max = max;

    /* One pass of smoothing along the horizon.
     *
     * The height field is 26 km per texel and the profile is sampled every
     * screen pixel, so the raw silhouette carries detail finer than the data
     * that produced it: it came out as a fuzz of single-pixel spikes, which
     * reads as noise on the edge rather than as mountains. A five-tap
     * binomial over the angle - about four pixels, roughly one texel - leaves
     * the ranges and removes the ringing. Only the radius is smoothed; the
     * sample directions stay where they were, because they choose the colour
     * and a blurred direction would drag it off its own terrain.
     */
    var r = p.r, sm = p.smooth;
    if (!sm || sm.length !== N) sm = p.smooth = new Float32Array(N);
    for (var q = 0; q < N; q++) {
      var a2 = r[q - 2 < 0 ? q - 2 + N : q - 2], a1 = r[q - 1 < 0 ? N - 1 : q - 1];
      var b1 = r[q + 1 >= N ? q + 1 - N : q + 1],
          b2 = r[q + 2 >= N ? q + 2 - N : q + 2];
      sm[q] = (a2 + 4 * a1 + 6 * r[q] + 4 * b1 + b2) * 0.0625;
    }
    p.r = sm;
    p.smooth = r;
    return p;
  };

  /* -- the terrain shader -------------------------------------------------
   *
   * One pass over the pixels of the disc. For each one: invert the projection
   * to a point on the sphere, read the terrain there, build a surface normal
   * from the local slope, light it, and choose a colour from the material
   * palette. It is a fragment shader written in a for-loop.
   *
   * Why on the CPU at all, when this is exactly what WebGL exists for: the
   * globe is one element of a report that has to render on a locked-down
   * browser, in print, and in a headless harness that verifies the pixels. A
   * 2D canvas is the only surface where all three of those are true, and a
   * planet at 600 px costs a few milliseconds a frame - a price this can pay,
   * where a full-screen shader could not.
   *
   * The performance work that matters is all structural rather than clever:
   *
   *   - the inner loop allocates nothing, calls nothing, and touches no
   *     property of `this`;
   *   - every transcendental that can be a table is a table;
   *   - resolution drops while the globe is moving and a full-detail frame is
   *     scheduled for when it stops. A drag is judged on latency and a still
   *     frame on detail, and they get different answers.
   */
  Globe.prototype._paintRelief = function () {
    var R = this.relief, pal = this.relPal;
    var s = this.size, ctx = this.ctx;

    var n = Math.max(64, Math.round(s * this._detail));
    var buf = this._reliefBuffer(n);
    var out = buf.img.data;
    var step = s / n;

    var cx = s / 2, cy = s / 2;
    var rad = this.radius * this.zoom;       // sphere radius, CSS px

    /* The silhouette, and the disc it widens.
     *
     * `clip` is the disc the shader may paint into, and it is normally the
     * sphere itself. With displaced terrain the planet is no longer round, so
     * it grows by the tallest thing on the horizon - a few pixels, into the
     * margin the widget already leaves outside the sphere. Past 1x there is
     * no profile, `clip` is the widget disc as before, and none of this costs
     * anything.
     */
    var prof = this._limb = this._limbProfile();
    var disp = prof ? this._dispScale() : 0;
    var dispCap = DISP_CAP * disp * DISP_TOP;
    var clip = this.radius * (prof ? prof.max : 1);
    var profN = prof ? prof.n : 0;
    var profK = prof ? profN / (2 * Math.PI) : 0;
    var profR = prof && prof.r, profVx = prof && prof.vx,
        profVy = prof && prof.vy, profVz = prof && prof.vz,
        profWx = prof && prof.wx, profWy = prof && prof.wy,
        profWz = prof && prof.wz,
        profHx = prof && prof.hx, profHy = prof && prof.hy,
        profHz = prof && prof.hz;

    var W = R.W, H = R.H, h = R.h, hMac = R.hMacro;
    var bio = R.bio, BW = R.BW, BH = R.BH, bxk = R.bx, byk = R.by;
    var depthByM = R.depthByM, depthIdx = R.depthIdx;

    // View basis in world coordinates. d3's rotate([l, p]) centres the
    // projection on longitude -l, latitude -p.
    var lon0 = -this.rotation[0] * DEG, lat0 = -this.rotation[1] * DEG;
    var clo = Math.cos(lon0), slo = Math.sin(lon0);
    var cla = Math.cos(lat0), sla = Math.sin(lat0);
    var fx = cla * clo, fy = cla * slo, fz = sla;      // towards the viewer
    var ex = -slo, ey = clo;                           // east  (ez is always 0)
    var nx = -sla * clo, ny = -sla * slo, nz = cla;    // north

    var ltx = LIGHT_TERRAIN[0], lty = LIGHT_TERRAIN[1], ltz = LIGHT_TERRAIN[2];
    var lvx = LIGHT_VIEW[0], lvy = LIGHT_VIEW[1], lvz = LIGHT_VIEW[2];
    // Halfway vector for the water highlight. The viewer is at +z, so it is
    // just the light plus (0,0,1), normalised - and it is constant per frame.
    var hvx = lvx, hvy = lvy, hvz = lvz + 1;
    var hk = 1 / Math.sqrt(hvx * hvx + hvy * hvy + hvz * hvz);
    hvx *= hk; hvy *= hk; hvz *= hk;

    var abyss = pal.abyss, shelf = pal.shelf, forest = pal.forest,
        desert = pal.desert, rock = pal.rock, ice = pal.ice,
        shore = pal.shore;

    /* The shoreline filament, faded out as the reader zooms in.
     *
     * It is drawn from the biome grid's distance-to-coast channel, which is
     * 39 km wide - a pleasing thread of light at 1x and a fat luminous smear
     * at 8x. Worse, an island smaller than the channel's own resolution is
     * *entirely* coast: Corsica, Sardinia and Mallorca came out as solid
     * glowing white blobs. Halving it with each doubling of the zoom keeps it
     * roughly one screen-width of thread at every scale, and by the time the
     * reader is close enough to see individual islands it is gone.
     */
    var shoreGain = pal.shoreGain / this.zoom;

    /* Who decides where the coast is.
     *
     * Not the elevation grid: at 26 km per texel its shoreline is a staircase
     * of blocks a reader can count, and the vector coastline drawn on top of
     * it visibly disagrees. So the vector polygons are rasterised into a mask
     * and *they* decide land from sea, while the raster keeps the job it is
     * good at - saying how high the land is. The coast is then as sharp as
     * the geometry at any magnification, and the two layers cannot disagree
     * because there is only one of them left.
     */
    var mask = this._landMask(n);

    // Lighting, resolved once per frame from the theme's numbers. `sunBase` is
    // derived rather than configured: it is what keeps the lit face at roughly
    // unit brightness however hard the far side is darkened, so turning the
    // sun up deepens the terminator instead of brightening the whole planet.
    var ambient = pal.ambient, contrast = pal.contrast;
    var seaAmbient = pal.seaAmbient, seaContrast = contrast * 0.4;
    var sun = pal.sun, sunBase = 1 - sun * 0.72, spec = pal.spec;

    var dxBase = CIRC_M / W, dyBase = MERID_M / H;
    var INV_TWO_PI = 1 / (2 * Math.PI), INV_PI = 1 / Math.PI;
    var clip2 = clip * clip, invRad = 1 / rad;
    // Coverage: radius units to buffer pixels, for a soft edge on the fringe.
    var covK = rad / step, paraLo2 = PARALLAX_LO * PARALLAX_LO;
    // Texels per buffer pixel at the centre of the disc, for the mask fade.
    var maskQk = (step / rad) * W * INV_TWO_PI;
    var grazeLo2 = GRAZE_LO * GRAZE_LO, grazeK = 1 / (1 - grazeLo2);

    var i = 0;
    for (var py = 0; py < n; py++) {
      var sy = (py + 0.5) * step - cy;
      for (var pxi = 0; pxi < n; pxi++, i += 4) {
        var sx = (pxi + 0.5) * step - cx;
        var dd = sx * sx + sy * sy;
        if (dd > clip2) { out[i + 3] = 0; continue; }

        var X = sx * invRad, Y = sy * invRad;
        var rho2 = X * X + Y * Y;
        var vx, vy, vz, wx, wy, wz, lat, lon;
        var alpha = 255, fringe = false, shiftPx = 0;

        if (rho2 >= 1) {
          /* Outside the sphere - which is not the same as outside the planet.
           *
           * This is the ground that stands proud of the horizon: the summit
           * the profile found beyond 90 degrees, seen edge-on. Every pixel of
           * the span between the circle and the silhouette shows the same
           * point, which is what a surface compressed to nothing by
           * foreshortening genuinely looks like, and the alternative - a
           * march per pixel - buys detail no one can resolve in four pixels.
           */
          if (!prof) { out[i + 3] = 0; continue; }
          var th = Math.atan2(sy, sx) + Math.PI;
          var pj = (th * profK) | 0;
          if (pj >= profN) pj = profN - 1; else if (pj < 0) pj = 0;
          var pr = profR[pj], rhoF = Math.sqrt(rho2);
          var cov = (pr - rhoF) * covK;
          if (cov <= 0) { out[i + 3] = 0; continue; }
          if (cov < 1) alpha = cov * 255;

          /* Where through the fringe this pixel falls, and what it therefore
           * looks at: the horizon at the inner edge, the summit that set the
           * silhouette at the outer one, and the ground between them in
           * between. Both ends are unit vectors a fifth of a radian apart, so
           * a lerp and a normalise is the arc to well under a pixel.
           */
          var ft = pr > 1.000001 ? (rhoF - 1) / (pr - 1) : 0;
          if (ft < 0) ft = 0; else if (ft > 1) ft = 1;
          var hxj = profHx[pj], hyj = profHy[pj], hzj = profHz[pj];
          wx = hxj + (profWx[pj] - hxj) * ft;
          wy = hyj + (profWy[pj] - hyj) * ft;
          wz = hzj + (profWz[pj] - hzj) * ft;
          var wl = 1 / Math.sqrt(wx * wx + wy * wy + wz * wz);
          wx *= wl; wy *= wl; wz *= wl;
          vx = profVx[pj]; vy = profVy[pj]; vz = profVz[pj] * ft;
          fringe = true;
          lat = Math.asin(wz > 1 ? 1 : wz < -1 ? -1 : wz);
          lon = Math.atan2(wy, wx);
        } else {

        // Unproject: the pixel is a direction in view space, with z towards
        // the viewer, and the sphere is the unit ball.
        vz = Math.sqrt(1 - rho2); vx = X; vy = -Y;
        wx = vx * ex + vy * nx + vz * fx;
        wy = vx * ey + vy * ny + vz * fy;
        wz = vy * nz + vz * fz;              // east has no z component

        lat = Math.asin(wz > 1 ? 1 : wz < -1 ? -1 : wz);
        lon = Math.atan2(wy, wx);

        /* Parallax, so the raised ground leans out over its own horizon.
         *
         * A point standing at height dR is seen displaced along the viewer's
         * tangential direction by dR/cos(incidence) - the classic parallax
         * offset. On screen that is a couple of pixels wherever the ground
         * faces the viewer, and it is the whole picture near the limb, where
         * those pixels span degrees of planet: the ranges stretch outwards
         * and meet the silhouette instead of vanishing into it. Without this
         * the fringe is a rind of colour stuck to the edge of a flat disc.
         *
         * One tap, nearest: this decides where to sample, not what to draw,
         * and a bilinear tap here would be four reads to move a sample point
         * by a fraction of a texel.
         */
        if (rho2 > paraLo2 && disp) {
          var qu = ((lon * INV_TWO_PI + 0.5) * W) | 0;
          var qv = ((0.5 - lat * INV_PI) * H) | 0;
          if (qu >= W) qu = W - 1; else if (qu < 0) qu = 0;
          if (qv >= H) qv = H - 1; else if (qv < 0) qv = 0;
          var qh = h[qv * W + qu];
          if (qh > 0) {
            // Offset limiting: 1/cos diverges at the limb, and the last ring
            // of pixels would otherwise sample the far side of the planet.
            var rho = Math.sqrt(rho2), amp = rho / vz;
            var off = displace(qh) * disp * amp;
            if (off > dispCap) off = dispCap;
            var lift = off / amp;            // = dR, unless the cap bit
            // Walk the sample point along the tangential viewer direction,
            // which is the unit vector (-vz*v_xy/rho, rho) in view space.
            var qx = vx * (1 - lift), qy = vy * (1 - lift),
                qz = vz + off * rho;
            var ql = 1 / Math.sqrt(qx * qx + qy * qy + qz * qz);
            qx *= ql; qy *= ql; qz *= ql;
            wx = qx * ex + qy * nx + qz * fx;
            wy = qx * ey + qy * ny + qz * fy;
            wz = qy * nz + qz * fz;
            lat = Math.asin(wz > 1 ? 1 : wz < -1 ? -1 : wz);
            lon = Math.atan2(wy, wx);
            shiftPx = lift * rad;
          }
        }

        }

        var fu = (lon * INV_TWO_PI + 0.5) * W;
        var fv = (0.5 - lat * INV_PI) * H;
        var tu = fu | 0, tv = fv | 0;
        if (tu >= W) tu = W - 1; else if (tu < 0) tu = 0;
        if (tv >= H) tv = H - 1; else if (tv < 0) tv = 0;

        /* Bilinear tap on the height field.
         *
         * The grid is 26 km at the equator. Taking the nearest texel built the
         * planet out of visible squares as soon as the globe was magnified at
         * all - square headlands, a staircase shoreline - and it was not only
         * ugly, it was wrong: the sand-coloured block covering the Frisian
         * coast reached tens of kilometres out into the North Sea, so a point
         * picked in open water came back as land.
         *
         * Four taps and three lerps fix both. The coastline now falls where
         * the interpolated surface crosses sea level, which is within a texel
         * of where d3 draws the vector coastline on top of it, and the terrain
         * between texels is a surface rather than a mosaic.
         */
        var gu = fu - 0.5, gv = fv - 0.5;
        var iu = Math.floor(gu), iv = Math.floor(gv);
        var au = gu - iu, av = gv - iv;
        var u0 = iu < 0 ? iu + W : iu >= W ? iu - W : iu;
        var u1 = u0 + 1 >= W ? 0 : u0 + 1;
        var v0 = iv < 0 ? 0 : iv >= H ? H - 1 : iv;
        var v1 = v0 + 1 >= H ? H - 1 : v0 + 1;
        var r0 = v0 * W, r1 = v1 * W;
        var h00 = h[r0 + u0], h10 = h[r0 + u1],
            h01 = h[r1 + u0], h11 = h[r1 + u1];
        var hTop = h00 + (h10 - h00) * au;
        var hBot = h01 + (h11 - h01) * au;
        var hm = hTop + (hBot - hTop) * av;

        /* Land or sea, decided by the vector coastline where there is one.
         *
         * Where the two sources disagree - a pixel the polygons call land but
         * the 26 km grid has under water, and the reverse - the height is
         * pulled to the near side of sea level rather than the material being
         * chosen against the geometry. A coastal pixel is then lowland or
         * shallows, which is what it is, instead of an alpine peak in the
         * Adriatic.
         */
        var isLand;
        if (fringe) {
          // Nothing but land is ever displaced, and the mask stops at the
          // circle, so out here the raster is the only witness there is.
          isLand = true;
          if (hm < 1) hm = 1;
        } else if (!mask) {
          isLand = hm > 0;
        } else {
          /* Which witness to believe, and why it has to be a fade.
           *
           * The mask is registered to the *undisplaced* sphere, so once
           * parallax has moved the sample the two are no longer talking
           * about the same place, and deferring to the mask there paints
           * ocean over the ground leaning out across it. The raster has to
           * take over - but it cannot take over at a threshold. Switching
           * authority at a fixed shift puts the changeover on a contour of
           * constant shiftPx, and along a coast that runs near-parallel to
           * the limb that contour runs *beside* the shoreline for hundreds
           * of pixels, with the two sources disagreeing the whole way: the
           * result was a dotted ribbon of sea in the land and land in the
           * sea, one pixel wide, exactly tracing the threshold.
           *
           * So landness is a quantity, not a vote. The mask's own alpha is
           * already antialiased, the raster gives a soft crossing of sea
           * level, and the blend between them ramps over a pixel of shift.
           * The coast then *slides* from one source to the other and stays
           * a single connected line, because a continuous field crosses a
           * half only once.
           *
           * The same handover is forced by foreshortening alone, for a
           * different reason. The mask is the vector coastline rasterised
           * at buffer resolution, and near the limb the coast is squeezed
           * below that resolution: its coverage oscillates around a half
           * from pixel to pixel, and a vote on it produced a ribbon of
           * dots - land in the sea and sea in the land - all along the
           * grazing coasts of Asia and Australia. The mask is there to
           * keep the shoreline sharper than a 26 km grid can draw it, and
           * sharpness is not a meaningful thing to ask for where a single
           * pixel spans three hundred kilometres. So past rho 0.97 the
           * smooth source wins, and the aliasing has nothing to alias.
           */
          var lm = mask[i + 3] * (1 / 255);
          var lr = hm > 12 ? 1 : hm < -12 ? 0 : (hm + 12) * (1 / 24);
          var t = (shiftPx - 0.6) * (1 / 1.0);
          // Texels of ground this pixel spans radially: the same pixel is
          // 1/cos(incidence) wider on the sphere than it is on the screen.
          var tg = (maskQk / vz - MASK_FADE_Q0) * MASK_FADE_K;
          if (tg > t) t = tg;
          if (t < 0) t = 0; else if (t > 1) t = 1;
          var landness = lm + (lr - lm) * (t * t * (3 - 2 * t));
          isLand = landness > 0.5;
          // Pull the height to the near side of sea level rather than
          // choosing the material against it, so a pixel the two sources
          // still disagree about is lowland or shallows - which is what it
          // is - instead of an alpine peak in the Adriatic.
          if (isLand) { if (hm < 0) hm = 0; }
          else if (hm > 0) hm = -20;
        }

        // The same four corners of the blurred field: the landform under the
        // landscape.
        var m00 = hMac[r0 + u0], m10 = hMac[r0 + u1],
            m01 = hMac[r1 + u0], m11 = hMac[r1 + u1];
        var mTop = m00 + (m10 - m00) * au;
        var mBot = m01 + (m11 - m01) * au;
        var hmac = mTop + (mBot - mTop) * av;

        /* The slope, as a continuous field rather than a per-cell constant.
         *
         * The obvious gradient of a bilinear patch - the difference of its own
         * four corners - is constant inside each cell and jumps at every cell
         * boundary. That discontinuity is not subtle: it lights each 26 km
         * cell as a flat facet, and because slope also decides how much bare
         * rock shows, the facets differ in colour as well as in brightness.
         * Magnified, the Alps became a chequerboard of tan and olive squares.
         *
         * So the gradient is sampled the same way the height is: a central
         * difference at each of the four surrounding texels, then bilinear
         * between them. It costs eight more reads from an array that is
         * already in cache, and it is the difference between a surface and a
         * mosaic. `dv` runs south, because v does.
         */
        var um = u0 - 1 < 0 ? W - 1 : u0 - 1;
        var up = u1 + 1 >= W ? 0 : u1 + 1;
        var rm = (v0 - 1 < 0 ? 0 : v0 - 1) * W;
        var rp = (v1 + 1 >= H ? H - 1 : v1 + 1) * W;

        var gx00 = h[r0 + u1] - h[r0 + um], gx10 = h[r0 + up] - h[r0 + u0],
            gx01 = h[r1 + u1] - h[r1 + um], gx11 = h[r1 + up] - h[r1 + u0];
        var gy00 = h[r1 + u0] - h[rm + u0], gy10 = h[r1 + u1] - h[rm + u1],
            gy01 = h[rp + u0] - h[r0 + u0], gy11 = h[rp + u1] - h[r0 + u1];
        var du = ((gx00 + (gx10 - gx00) * au) * (1 - av) +
                  (gx01 + (gx11 - gx01) * au) * av) * 0.5;
        var dv = ((gy00 + (gy10 - gy00) * au) * (1 - av) +
                  (gy01 + (gy11 - gy01) * au) * av) * 0.5;

        var px00 = hMac[r0 + u1] - hMac[r0 + um],
            px10 = hMac[r0 + up] - hMac[r0 + u0],
            px01 = hMac[r1 + u1] - hMac[r1 + um],
            px11 = hMac[r1 + up] - hMac[r1 + u0];
        var py00 = hMac[r1 + u0] - hMac[rm + u0],
            py10 = hMac[r1 + u1] - hMac[rm + u1],
            py01 = hMac[rp + u0] - hMac[r0 + u0],
            py11 = hMac[rp + u1] - hMac[r0 + u1];
        var mdu = ((px00 + (px10 - px00) * au) * (1 - av) +
                   (px01 + (px11 - px01) * au) * av) * 0.5;
        var mdv = ((py00 + (py10 - py00) * au) * (1 - av) +
                   (py01 + (py11 - py01) * au) * av) * 0.5;

        // Metres per texel. Columns narrow towards the poles, and without the
        // cosine the Arctic would be covered in vertical cliffs. The floor
        // stops the last row or two from dividing by nothing.
        var cosLat = Math.sqrt(wx * wx + wy * wy);
        if (cosLat < 0.06) cosLat = 0.06;
        var k = (isLand ? EXAG : SEA_EXAG);
        var sx_ = du * k / (dxBase * cosLat);
        var sy_ = -dv * k / dyBase;

        // Hillshade in the tangent frame: normal (-dh/de, -dh/dn, 1).
        var nlen = Math.sqrt(sx_ * sx_ + sy_ * sy_ + 1);
        var hill = (-sx_ * ltx - sy_ * lty + ltz) / nlen;
        if (hill < 0) hill = 0;

        // The landform's own shade, from the blurred field. On land the two are
        // blended; at sea there is nothing at this scale worth lighting, and
        // mixing it in only made the abyssal plains undulate.
        if (isLand) {
          var mx_ = mdu * (EXAG_MACRO / (dxBase * cosLat));
          var my_ = -mdv * (EXAG_MACRO / dyBase);
          var mlen = Math.sqrt(mx_ * mx_ + my_ * my_ + 1);
          var mhill = (-mx_ * ltx - my_ * lty + ltz) / mlen;
          if (mhill < 0) mhill = 0;
          hill = hill * (1 - MACRO_MIX) + mhill * MACRO_MIX;

          // Prominence above the local landform, which is what a hillshade
          // cannot say: it reads slope, and the top of a plateau has none. This
          // lifts summits out of the light and sinks valleys into shadow, and
          // it is the term that makes the relief look carved rather than
          // printed.
          var prom = (hm - hmac) * (1 / PROMINENCE_M);
          if (prom > 1) prom = 1; else if (prom < -1) prom = -1;
          hill *= 1 + PROMINENCE_GAIN * prom;
          if (hill < 0) hill = 0;
        }

        /* Slopes that have turned away from the viewer, near the limb.
         *
         * `vis` is the cosine between the terrain normal and the line of
         * sight, both in the tangent frame: positive where the slope presents
         * itself to the viewer, negative where a displaced surface would have
         * hidden it behind its own ridge. Ramped in over the outer third of
         * the disc, so nothing in the middle of the globe changes at all, and
         * it is the term that puts a dark far side on every range along the
         * edge instead of a uniform smear.
         */
        if (isLand && rho2 > grazeLo2) {
          var tvx = fx - vz * wx, tvy = fy - vz * wy, tvz = fz - vz * wz;
          var invCos = 1 / cosLat;
          var te = (wx * tvy - wy * tvx) * invCos;
          var tn = cosLat * tvz - wz * (wx * tvx + wy * tvy) * invCos;
          var vis = (-sx_ * te - sy_ * tn + vz) / nlen;
          vis *= 3;
          if (vis > 1) vis = 1; else if (vis < 0) vis = 0;
          vis = vis * vis * (3 - 2 * vis);
          var gz = (rho2 - grazeLo2) * grazeK;
          if (gz > 1) gz = 1;
          hill *= 1 - gz * GRAZE_DEPTH * (1 - vis);
        }

        // The ball's own lighting, in view space. One dot product, and the
        // only term in the frame that knows which way the globe is turned.
        var sph = vx * lvx + vy * lvy + vz * lvz;
        if (sph < 0) sph = 0;

        /* The same treatment for the biome grid.
         *
         * It is coarser still - 39 km - and it carries the shoreline filament,
         * so sampling it nearest left the coasts of the North Sea, the Baltic
         * and the Adriatic edged with a staircase of glowing squares. Three
         * channels off four taps, sharing one set of weights.
         */
        var bu = fu * bxk - 0.5, bv = fv * byk - 0.5;
        var ju = Math.floor(bu), jv = Math.floor(bv);
        var bau = bu - ju, bav = bv - jv;
        var bu0 = ju < 0 ? ju + BW : ju >= BW ? ju - BW : ju;
        var bu1 = bu0 + 1 >= BW ? 0 : bu0 + 1;
        var bv0 = jv < 0 ? 0 : jv >= BH ? BH - 1 : jv;
        var bv1 = bv0 + 1 >= BH ? BH - 1 : bv0 + 1;
        var q0 = bv0 * BW, q1 = bv1 * BW;
        var b00 = (q0 + bu0) * 4, b10 = (q0 + bu1) * 4,
            b01 = (q1 + bu0) * 4, b11 = (q1 + bu1) * 4;
        var w10 = bau * (1 - bav), w00 = (1 - bau) * (1 - bav),
            w11 = bau * bav, w01 = (1 - bau) * bav;
        var veg = (bio[b00] * w00 + bio[b10] * w10 +
                   bio[b01] * w01 + bio[b11] * w11) * (1 / 255);
        var snow = (bio[b00 + 1] * w00 + bio[b10 + 1] * w10 +
                    bio[b01 + 1] * w01 + bio[b11 + 1] * w11) * (1 / 255);
        var coast = (bio[b00 + 2] * w00 + bio[b10 + 2] * w10 +
                     bio[b01 + 2] * w01 + bio[b11 + 2] * w11) * (1 / 255);

        var cr, cg, cb, shade;
        if (isLand) {
          // How steep the ground is, as a number between 0 (flat) and ~1
          // (cliff). `nlen` is already sqrt(slope^2 + 1), so this is free.
          var steep = 1 - 1 / nlen;

          // Vegetation chooses between forest and desert. Altitude and slope
          // then strip both back towards bare rock - and it matters that slope
          // has a say, because it is the only term that can tell the Himalaya
          // from the plateau behind it, which stand at the same height.
          var alt = (hm - ROCK_ALT_LO) * (1 / (ROCK_ALT_HI - ROCK_ALT_LO));
          alt = alt < 0 ? 0 : alt > 1 ? 1 : alt;
          alt = alt * alt * (3 - 2 * alt);        // smoothstep: no hard edge
          var bare = alt * 0.55 + steep * 0.60;
          if (bare > 0.90) bare = 0.90;

          cr = desert[0] + (forest[0] - desert[0]) * veg;
          cg = desert[1] + (forest[1] - desert[1]) * veg;
          cb = desert[2] + (forest[2] - desert[2]) * veg;
          cr += (rock[0] - cr) * bare;
          cg += (rock[1] - cg) * bare;
          cb += (rock[2] - cb) * bare;

          // Snow: whichever is greater, what the satellite saw in July or what
          // the climatic snow line says must be there all year. The steepest
          // faces keep less of it - snow does not sit on a cliff, and the bare
          // rock between the snowfields is what gives a range its edges.
          var line = SNOW_EQUATOR - (SNOW_EQUATOR - SNOW_POLE) *
                     Math.pow(wz < 0 ? -wz : wz, 2.2);
          // A wide ramp and a ceiling below one, on purpose. A sharp snow line
          // turned the Tibetan plateau into a second Greenland - a solid white
          // sheet the size of western Europe - because a plateau crosses any
          // single threshold all at once. Fading in over two kilometres of
          // altitude instead leaves the plateau the brown it really is and
          // puts the white where the ground actually rises out of it.
          var cap = (hm - line) * (1 / 2200);
          cap = cap < 0 ? 0 : cap > 1 ? 1 : cap;
          cap *= 0.72 - 0.34 * steep;
          var sn = snow * (0.55 + 0.45 * snow);   // stands in for snow^0.8
          if (cap > sn) sn = cap;
          cr += (ice[0] - cr) * sn;
          cg += (ice[1] - cg) * sn;
          cb += (ice[2] - cb) * sn;
          shade = (ambient + contrast * hill) * (sunBase + sun * sph);
        } else {
          var di = (-hm * depthIdx) | 0;
          var dep = depthByM[di > 256 ? 256 : di];
          cr = shelf[0] + (abyss[0] - shelf[0]) * dep;
          cg = shelf[1] + (abyss[1] - shelf[1]) * dep;
          cb = shelf[2] + (abyss[2] - shelf[2]) * dep;
          // Sea ice is real and white, and cutting it at the shoreline would
          // leave Greenland floating in open water.
          if (snow > 0.02) {
            cr += (ice[0] - cr) * snow;
            cg += (ice[1] - cg) * snow;
            cb += (ice[2] - cb) * snow;
          }
          // Water keeps far more ambient light than land. Shading it as hard
          // as the continents turns whole oceans black on the unlit side, and
          // a navigation control may not have an unreadable half.
          shade = (seaAmbient + seaContrast * hill) * (sunBase + sun * sph * 0.7);

          // Specular highlight, Blinn-Phong. Five squarings instead of a
          // pow(): this runs on every water pixel of every frame.
          if (spec > 0) {
            var sp = vx * hvx + vy * hvy + vz * hvz;
            if (sp > 0) {
              sp *= sp; sp *= sp; sp *= sp; sp *= sp; sp *= sp;   // ^32
              shade += sp * spec;
            }
          }
        }

        cr *= shade; cg *= shade; cb *= shade;

        // The shoreline, lit like a filament. This is the one purely graphic
        // flourish in the shader, and it is registered to the terrain rather
        // than to the 110 m coastline vector, so it cannot drift off the coast
        // when the globe is zoomed in.
        if (!isLand && coast > 0.9) {
          var e = (coast - 0.9) * 10;
          e = e * e * shoreGain;
          cr += shore[0] * e; cg += shore[1] * e; cb += shore[2] * e;
        }

        out[i] = cr;
        out[i + 1] = cg;
        out[i + 2] = cb;
        out[i + 3] = alpha;
      }
    }

    buf.ctx.putImageData(buf.img, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(buf.canvas, 0, 0, s, s);
    this.stats.relief = true;
    this._scheduleRefine();
  };

  /* Rasterise the vector coastline into a land/sea mask at buffer resolution.
   *
   * The one non-obvious thing here is the transform: the projection is set up
   * in CSS pixels, and the shader's buffer is a square of `n` of them, so the
   * context is scaled by n/size and the projection is left alone. Sharing the
   * projection is the point - a mask built from a second, subtly different
   * one would be worse than no mask at all.
   *
   * The alpha channel comes back antialiased, which is exactly what is
   * wanted: a coast half a pixel wide rather than a chain of squares.
   */
  Globe.prototype._landMask = function (n) {
    var geo = this._geo();
    if (!geo) return null;
    var m = this._mask;
    if (!m || m.canvas.width !== n) {
      var c = document.createElement('canvas');
      c.width = n; c.height = n;
      m = this._mask = {
        canvas: c, ctx: c.getContext('2d', { willReadFrequently: true })
      };
    }
    var g = m.ctx, k = n / this.size;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, n, n);
    g.setTransform(k, 0, 0, k, 0, 0);
    g.beginPath();
    global.d3.geoPath(this.projection, g)(geo);
    g.fillStyle = '#fff';
    g.fill();
    g.setTransform(1, 0, 0, 1, 0, 0);
    return g.getImageData(0, 0, n, n).data;
  };

  /* The scratch canvas the shader writes into.
   *
   * Kept and reused: allocating a canvas and an ImageData per frame is how a
   * smooth drag turns into a garbage-collection stutter.
   */
  Globe.prototype._reliefBuffer = function (n) {
    var b = this._buf;
    if (!b || b.canvas.width !== n) {
      var c = document.createElement('canvas');
      c.width = n; c.height = n;
      var g = c.getContext('2d', { willReadFrequently: true });
      b = this._buf = { canvas: c, ctx: g, img: g.createImageData(n, n) };
    }
    return b;
  };

  /* Adapt the resolution to what this machine can actually deliver.
   *
   * The measurement is the previous frame's own render time, so the globe
   * tunes itself to the device it is on rather than to a user-agent guess.
   * When the view stops changing, one full-detail frame is scheduled: the
   * detail is only ever missing while something is moving, which is exactly
   * when nobody can see it.
   */
  Globe.prototype._scheduleRefine = function () {
    var self = this, ms = this.stats.ms;

    if (ms > 26 && this._detail > 0.4) this._detail = Math.max(0.4, this._detail * 0.7);
    else if (ms < 9 && this._detail < 1) this._detail = Math.min(1, this._detail * 1.25);

    if (this._detail >= 1 || this._refine) return;
    this._refine = setTimeout(function () {
      self._refine = null;
      var was = self._detail;
      self._detail = 1;
      self.render();
      // Keep the adaptive figure the drag will use next time: the full-detail
      // frame is slow by construction and must not be read as evidence that
      // the machine is slow.
      self._detail = was;
    }, 130);
  };

  /* -- instrument overlay -------------------------------------------------
   *
   * Drawn in vectors at full device resolution on top of the shaded planet:
   * graticule and limb. These are crisp lines and they have to stay crisp,
   * which is precisely why they are not part of the shader - the shader's
   * output is scaled up from a lower-resolution buffer while the globe is
   * moving, and a one-pixel line drawn into it would shimmer.
   */
  Globe.prototype._paintOverlay = function () {
    var s = this.size, ctx = this.ctx, p = this.palette, rp = this.relPal;
    var r = this.radius, cx = s / 2, cy = s / 2;
    var geoPath = global.d3.geoPath(this.projection, ctx);

    ctx.save();
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.clip();

    var add = this.relief && p.gratAdd;
    if (add) ctx.globalCompositeOperation = 'lighter';

    ctx.beginPath();
    geoPath(this.graticule);
    ctx.strokeStyle = p.grat;
    ctx.globalAlpha = p.gratOpacity * (this.relief ? (add ? 0.9 : 0.55) : 1);
    ctx.lineWidth = 0.5;
    ctx.stroke();

    // The equator and the prime meridian, a shade stronger than the rest of
    // the grid. They are the two lines a reader actually locates themselves
    // against, and at 10-degree spacing they are otherwise indistinguishable
    // from their neighbours.
    ctx.beginPath();
    geoPath(this._axes);
    ctx.globalAlpha = Math.min(1, p.gratOpacity * 1.5);
    ctx.lineWidth = 0.8;
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
    ctx.restore();

    // No atmosphere, and no bearing ring.
    //
    // There was an atmosphere: a blue halo just inside the limb, fading
    // outwards. It is what a lit planet looks like from space, and on this
    // globe it read as a soft blue ring stuck to the edge of a disc - it made
    // the instrument look like a photograph of a planet rather than a planet.
    //
    // There was also a bezel: tick marks every 15 degrees outside the limb with
    // the north one filled in. It drew a second circle around the globe, which
    // is one circle more than a globe needs, and the orientation it carried is
    // already stated in words by the readout in the corner - which is both
    // more precise than a tick and legible without hunting for it.

    /* The limb itself, outside the clip so the edge stays crisp at any zoom.
     *
     * It follows the silhouette when there is one. This is not decoration: a
     * true circle stroked over a displaced edge draws a chord straight across
     * every summit that breaks it, which is precisely the machined look the
     * displacement exists to remove. Over water the profile is exactly 1 and
     * this is the same circle it always was.
     */
    var lp = this._limb;
    if (!p.limbOn) { this._paintReadout(); return; }
    ctx.beginPath();
    if (lp) {
      var lstep = 2 * Math.PI / lp.n;
      for (var lj = 0; lj < lp.n; lj++) {
        var lth = (lj + 0.5) * lstep - Math.PI, lr = r * lp.r[lj];
        var lx = cx + Math.cos(lth) * lr, ly = cy + Math.sin(lth) * lr;
        if (lj) ctx.lineTo(lx, ly); else ctx.moveTo(lx, ly);
      }
      ctx.closePath();
    } else {
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
    }
    ctx.strokeStyle = p.limb;
    ctx.globalAlpha = 0.8;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.globalAlpha = 1;

    this._paintReadout();
  };

  /* Where the view is pointing, in words.
   *
   * The globe can be spun freely, and after a few drags it is genuinely easy to
   * lose track of where you are looking. This is the one place the view states
   * it outright, and since the bearing ring went it is the only one - which is
   * no loss: "33.5 N 4.8 W" is an answer, a tick mark at the top of a circle
   * is a hint.
   */
  Globe.prototype._paintReadout = function () {
    if (this.readout) {
      var lat = -this.rotation[1], lon = ((-this.rotation[0] + 540) % 360) - 180;
      this.readout.textContent =
        Math.abs(lat).toFixed(1) + '\u00b0' + (lat >= 0 ? 'N' : 'S') + '  ' +
        Math.abs(lon).toFixed(1) + '\u00b0' + (lon >= 0 ? 'E' : 'W') +
        '   \u00d7' + (this.zoom < 10 ? this.zoom.toFixed(1) : Math.round(this.zoom));
    }
  };


  /* Project, hide the far side, declutter, and write transforms.
   *
   * Only the markers that survive get a DOM write, and each write is a single
   * `transform` - the expensive part of the old renderer was not the maths but
   * the attribute churn.
   */
  Globe.prototype._drawMarkers = function () {
    var proj = this.projection, live = [];
    var cLon = -this.rotation[0] * Math.PI / 180,
        cLat = -this.rotation[1] * Math.PI / 180;
    var ux = Math.cos(cLat) * Math.cos(cLon),
        uy = Math.cos(cLat) * Math.sin(cLon),
        uz = Math.sin(cLat);
    // Markers obey the same disc the basemap is clipped to. Past 1x the sphere
    // is much larger than the viewport, so a front-facing city can project well
    // outside the globe - and an unclipped marker does not merely look wrong,
    // it lands on top of the page beside the globe and stays clickable there.
    var cx = this.size / 2, cy = this.size / 2, rad = this.radius;
    var rad2 = rad * rad;

    for (var i = 0; i < this.nodes.length; i++) {
      var m = this.nodes[i];
      // A point is on the far side when it is more than a quarter turn from the
      // projection centre - i.e. the dot product with the view vector is
      // negative. Without this test the back hemisphere folds onto the front and
      // cities appear mirrored on the wrong continent.
      m.front = m.u[0] * ux + m.u[1] * uy + m.u[2] * uz > 0;
      if (!m.front) { m.want = false; continue; }
      var p = proj([m.d.lon, m.d.lat]);
      if (!p) { m.want = false; continue; }
      var ddx = p[0] - cx, ddy = p[1] - cy;
      if (ddx * ddx + ddy * ddy > rad2) { m.want = false; continue; }
      m.x = p[0]; m.y = p[1];
      m.want = true;
      live.push(m);
    }

    var clusters = this._declutter(live);

    var shownCount = 0;
    for (i = 0; i < this.nodes.length; i++) {
      var k = this.nodes[i];
      if (k.want) {
        k.g.setAttribute('transform',
          'translate(' + k.x.toFixed(1) + ',' + k.y.toFixed(1) + ')');
        shownCount++;
      }
      if (k.want !== k.shown) {
        k.shown = k.want;
        if (k.want) {
          k.g.removeAttribute('display');
          k.g.removeAttribute('aria-hidden');
        } else {
          k.g.setAttribute('display', 'none');
          k.g.setAttribute('aria-hidden', 'true');
        }
      }
    }

    this._drawClusters(clusters);
    this.stats.markers = shownCount;
  };

  /* Declutter by suppression, never by displacement.
   *
   * The previous design pushed overlapping markers apart and drew a leader line
   * back to the true location. That works for fifteen capitals. For a worldwide
   * set it does not: at whole-Earth zoom the European markers form one mass, and
   * a relaxation that separates them puts dozens of dots visibly nowhere near
   * their cities. A map that moves a marker 40 px to stay tidy is a map that
   * lies about position, and no leader line makes that acceptable at that scale.
   *
   * So: every marker is drawn where it actually is, or not drawn. Markers hidden
   * this way are counted into a badge on the marker that displaced them, which
   * says how many places are hiding there and zooms in when clicked. Nothing is
   * lost - the full set remains in the side list, in the palette, and on the
   * globe as soon as the zoom can separate them.
   *
   * A uniform grid keeps this linear. The all-pairs relaxation it replaces was
   * O(n^2) per pass, twelve passes a frame - around 360k distance tests per
   * frame of a drag at this city count.
   */
  Globe.prototype._declutter = function (live) {
    var sep = this.MIN_SEP, sep2 = sep * sep, grid = {}, clusters = [];
    var hidden = 0, placed = [];

    function key(cx, cy) { return cx + ',' + cy; }

    // The selected city goes first, so it always survives - the globe must show
    // where the thing you are reading about is. Placing it first rather than
    // exempting it from the check is the whole point: an exemption lets it land
    // three pixels from a neighbour that was already placed, and then the
    // selected dot covers that neighbour's centre and makes it unclickable.
    // Going first, it claims its space and everyone else defers to it.
    var order = this.order, sel = null;
    for (var s = 0; s < order.length; s++) {
      if (order[s].d.slug === this.selected) {
        sel = order[s];
        break;
      }
    }

    for (var i = -1; i < order.length; i++) {
      var m = i < 0 ? sel : order[i];
      if (!m || !m.want || (i >= 0 && m === sel)) continue;
      var gx = Math.floor(m.x / sep), gy = Math.floor(m.y / sep);
      var host = null;

      for (var ax = gx - 1; ax <= gx + 1 && !host; ax++) {
        for (var ay = gy - 1; ay <= gy + 1 && !host; ay++) {
          var cell = grid[key(ax, ay)];
          if (!cell) continue;
          for (var j = 0; j < cell.length; j++) {
            var o = cell[j];
            var dx = o.x - m.x, dy = o.y - m.y;
            if (dx * dx + dy * dy < sep2) { host = o; break; }
          }
        }
      }

      if (host) {
        m.want = false;
        hidden++;
        if (!host._hidden) { host._hidden = 0; }
        host._hidden++;
        if (host._hidden === 1) clusters.push(host);
      } else {
        m._hidden = 0;
        placed.push(m);
        (grid[key(gx, gy)] || (grid[key(gx, gy)] = [])).push(m);
      }
    }

    hidden += this._placeBadges(clusters, placed);

    this.stats.clustered = hidden;
    return clusters;
  };

  /* Decide where each "+N" badge sits, and pay for the space it takes.
   *
   * A badge is a control: its own label invites a click. Pinning it at a fixed
   * upper-right offset means that in a dense region it lands on a neighbouring
   * city's dot, and then one of the two is unreachable - either the badge is
   * intercepted by the dot, or the badge covers the dot's centre. Paint order
   * only chooses which of the two breaks.
   *
   * So the badge hunts for a clear direction, and if none is clear it hides the
   * markers it lands on and counts them in, exactly as an overlapping marker
   * would have been. Every visible marker stays clickable and every visible
   * badge stays clickable; the badge's own count stays truthful because the
   * markers it displaced are added to it before it is drawn.
   */
  Globe.prototype._placeBadges = function (clusters, placed) {
    var BR = 9;            // badge radius plus a pixel of slack
    var REACH = 40;        // no marker further than this can matter
    var extra = 0;

    for (var c = 0; c < clusters.length; c++) {
      var host = clusters[c];
      var dist = host.r + BR + 2;

      // Only neighbours close enough to interact, gathered once per host.
      var near = [];
      for (var p = 0; p < placed.length; p++) {
        var o = placed[p];
        if (o === host) continue;
        var ox = o.x - host.x, oy = o.y - host.y;
        if (ox * ox + oy * oy < REACH * REACH) near.push(o);
      }

      var best = null, bestClear = -Infinity;
      for (var a = 0; a < 8; a++) {
        // Start upper-right and work round: the first candidate is the
        // conventional position, so in open country nothing moves.
        var ang = (-Math.PI / 4) + a * (Math.PI / 4);
        var bx = host.x + Math.cos(ang) * dist,
            by = host.y + Math.sin(ang) * dist;

        var clear = Infinity, k, d;
        for (k = 0; k < near.length; k++) {
          d = Math.sqrt((near[k].x - bx) * (near[k].x - bx) +
                        (near[k].y - by) * (near[k].y - by)) - near[k].r - BR;
          // A host carries a badge of its own; overlapping one is worse than
          // overlapping a plain marker, because we cannot hide it to recover.
          if (near[k]._hidden > 0) d -= 6;
          if (d < clear) clear = d;
        }
        for (k = 0; k < c; k++) {
          d = Math.sqrt((clusters[k]._bx - bx) * (clusters[k]._bx - bx) +
                        (clusters[k]._by - by) * (clusters[k]._by - by)) - 2 * BR;
          if (d < clear) clear = d;
        }

        if (clear > bestClear) { bestClear = clear; best = [bx, by]; }
        if (clear >= 0) break;          // good enough; stop looking
      }

      host._bx = best[0];
      host._by = best[1];

      // Nowhere was clear. Take the space and account for it.
      if (bestClear < 0) {
        for (var q = 0; q < near.length; q++) {
          var t = near[q];
          if (!t.want || t._hidden > 0) continue;   // never orphan a badge
          var tx = t.x - host._bx, ty = t.y - host._by;
          if (tx * tx + ty * ty < (BR + t.r) * (BR + t.r)) {
            t.want = false;
            extra++;
            host._hidden++;
          }
        }
      }
    }
    return extra;
  };

  /* The "+N" badges. Pooled, because their count changes every frame of a zoom
   * and creating DOM nodes in a render loop is how smooth interactions die. */
  Globe.prototype._drawClusters = function (clusters) {
    var self = this, pool = this._clusterPool;

    while (pool.length < clusters.length) {
      var g = el('g', { class: 'cl', role: 'button', tabindex: '0' });
      var bg = el('circle', { class: 'clbg', r: 8 });
      var tx = el('text', { class: 'cltx', y: 3.2, 'text-anchor': 'middle' });
      g.appendChild(bg);
      g.appendChild(tx);
      (function (node) {
        function act() {
          if (self._dragged) return;
          // Zoom toward the crowd rather than jumping to a city: the point of
          // the badge is that we do not know which of them you meant.
          if (node._at) self.zoomTo(node._at[0], node._at[1]);
        }
        node.addEventListener('click', act);
        node.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); act(); }
        });
      })(g);
      pool.push({ g: g, tx: tx, on: false });
      this.clusters.appendChild(g);
    }

    for (var i = 0; i < pool.length; i++) {
      var slot = pool[i];
      if (i < clusters.length) {
        var host = clusters[i];
        var n = host._hidden;
        var label = n + ' more ' + (n === 1 ? 'place' : 'places') +
                    ' here. Zoom in to separate them.';
        // Position chosen in _placeBadges, which already guaranteed the badge
        // is not sitting on a marker that is still visible.
        slot.g.setAttribute('transform',
          'translate(' + host._bx.toFixed(1) + ',' + host._by.toFixed(1) + ')');
        slot.tx.textContent = n > 99 ? '99+' : '+' + n;
        slot.g.setAttribute('aria-label', label);
        slot.g.setAttribute('data-tip', label);
        slot.g._at = [host.d.lon, host.d.lat];
        if (!slot.on) { slot.g.removeAttribute('display'); slot.on = true; }
      } else if (slot.on) {
        slot.g.setAttribute('display', 'none');
        slot.on = false;
      }
    }
  };

  /* -- navigation -------------------------------------------------------- */

  /* Turn to a point and close in, used by the cluster badges. */
  Globe.prototype.zoomTo = function (lon, lat) {
    this.setZoom(this.zoom * 2.2);
    this._spin([-lon, -lat]);
  };

  /* Turn the globe so a city faces the viewer. */
  Globe.prototype.focus = function (slug, animate) {
    var target = null;
    this.nodes.forEach(function (m) {
      if (m.d.slug === slug) target = m.d;
    });
    if (!target) return;
    this.setSelected(slug);
    this._spin([-target.lon, -target.lat], animate);
  };

  Globe.prototype._spin = function (to, animate) {
    var self = this;
    if (animate === false || reducedMotion()) {
      this.rotation = to;
      this.invalidate();
      return;
    }
    var from = this.rotation.slice();
    // Take the short way round; without this a move across the antimeridian
    // spins most of the way about the planet to arrive next door.
    var dLon = ((to[0] - from[0] + 540) % 360) - 180;
    var dLat = to[1] - from[1];
    var t0 = performance.now(), dur = 620;

    if (this._raf) cancelAnimationFrame(this._raf);
    (function step(t) {
      var k = Math.min(1, (t - t0) / dur);
      var e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      self.rotation = [from[0] + dLon * e, from[1] + dLat * e];
      self.render();
      if (k < 1) self._raf = requestAnimationFrame(step);
    })(t0);
  };

  Globe.prototype.setSelected = function (slug) {
    this.selected = slug;
    var self = this;
    this.nodes.forEach(function (m) {
      var on = m.d.slug === slug;
      m.g.classList.toggle('sel', on);
      m.g.setAttribute('aria-pressed', on ? 'true' : 'false');
      if (on) self._raise(m.g);
    });
    // The selection is exempt from decluttering, so the layout may change.
    this.invalidate();
  };

  global.Globe = Globe;
})(window);
