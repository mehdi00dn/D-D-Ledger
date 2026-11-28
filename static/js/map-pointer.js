/* Map pointer: hold the right mouse button on the map and your cursor turns into a small dot that everyone looking at the map sees, live.
 *
 * Your own dot follows the mouse exactly.  Other people's dots arrive about twenty times a second over the live channel (map-live.js, kind "ptr");
 * they are played back about 65 ms behind real time (a little over one message interval), joined straight from one position to the next, so the path is the path that was really taken, not an eased one.
 * A tapering trail behind the dot follows the path the dot really took, so it bends when the hand moves in a curve (it is built from the last third of a second of positions, and
 * is as long as the dot is fast).  Nothing here is saved: when the button is released (or
 * the messages stop for a few seconds) the dot shrinks away.
 *
 * Each person has one colour, taken from their user id.  Positions travel in map coordinates (the same ones pins use), so the dot lands on the same spot of the map for everyone whatever their zoom or pan.
 * The dot itself keeps its size on screen.  Only transform and opacity are animated; with reduced motion there is no trail and no grow/shrink.
 *
 *   var pointer = MapPointer.create({ stage, inner, natural: () => ({ w, h }), bounds: () => ({ x0, y0, x1, y1 }) (optional: where a dot may go), toNatural: (clientX, clientY) => ({ x, y }), send: (msg) => {}, myId: () => userId });
 *   pointer.receive(senderId, { x, y, end })
 */
(function () {
  'use strict';

  var DELAY_MS = 65;          // how far behind real time other people's dots are drawn: a little over one message interval (50 ms), so the path is the real path, not an eased one
  var STALE_MS = 3500;        // a dot that has gone quiet this long is taken away (a held button keeps sending a heartbeat)
  var KEEPALIVE_MS = 1000;
  var MAX_DOTS = 40;
  var TRAIL_MS = 340;         // the trail is drawn from the positions of the last 340 ms
  var TRAIL_MAX_PX = 170;     // and never longer than this
  var TRAIL_HALF = 3.2;       // half the trail's width at the dot (it narrows to nothing at the far end)
  var TRAIL_MIN_PX = 8;       // a trail shorter than this is not drawn
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var COLORS = ['#ffb000', '#00b4ff', '#ff3b30', '#5fe000', '#9a4dff', '#ff2d9b', '#00d49a', '#2f5bff', '#e61ee6', '#ffe600'];   // ten full-strength hues that stay apart on light and dark maps

  function num(v, fallback) { v = Number(v); return isFinite(v) ? v : fallback; }
  function now() { return (window.performance && performance.now) ? performance.now() : Date.now(); }
  function colorFor(id) {                 // the same person gets the same colour on every screen, and on every visit
    var h = 2166136261, s = String(id);
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
    return COLORS[h % COLORS.length];
  }

  function create(host) {
    var stage = host.stage, inner = host.inner;
    var reduced = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };

    var layer = document.createElement('div');
    layer.className = 'map-ptr-layer';
    layer.setAttribute('aria-hidden', 'true');
    stage.appendChild(layer);

    var dots = new Map();       // sender id -> dot
    var raf = 0, lastFrame = 0, gradients = 0;

    function makeEl(color) {
      var el = document.createElement('div');
      el.className = 'map-ptr';
      el.style.setProperty('--ptr', color);
      var gid = 'map-ptr-fade-' + (++gradients);
      el.innerHTML = '<svg class="map-ptr-trail" width="1" height="1" aria-hidden="true"><defs><linearGradient id="' + gid + '" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="1" y2="0">' +
        '<stop offset="0" stop-opacity="0.95"/><stop offset="1" stop-opacity="0"/></linearGradient></defs><path fill="url(#' + gid + ')"/></svg><i class="map-ptr-dot"></i>';
      layer.appendChild(el);
      return el;
    }

    // The trail: a ribbon along the dot's recent path, as wide as the dot at the head and narrowing to a point.  `hist` holds where the dot was on screen
    // (newest last).  Returns the path text and the far end, or null when there is nothing worth drawing.
    function ribbon(hist) {
      var pts = [], acc = 0, i, q;
      for (i = hist.length - 1; i >= 0; i--) {
        var h = hist[i];
        if (pts.length) {
          q = pts[pts.length - 1];
          var step = Math.hypot(h.x - q.x, h.y - q.y);
          if (step < 0.6) continue;                                  // a dot at rest piles up points on one spot
          acc += step;
          if (acc > TRAIL_MAX_PX) break;
        }
        pts.push({ x: h.x, y: h.y });
      }
      var n = pts.length;
      if (n < 3 || acc < TRAIL_MIN_PX) return null;
      var sm = [pts[0]];                                             // smooth the corners that the 20-a-second messages leave, the head stays put
      for (i = 1; i < n - 1; i++) sm.push({ x: pts[i - 1].x * 0.25 + pts[i].x * 0.5 + pts[i + 1].x * 0.25, y: pts[i - 1].y * 0.25 + pts[i].y * 0.5 + pts[i + 1].y * 0.25 });
      sm.push(pts[n - 1]);
      var hx = sm[0].x, hy = sm[0].y, left = [], right = [];
      for (i = 0; i < n; i++) {
        var a = sm[Math.max(0, i - 1)], b = sm[Math.min(n - 1, i + 1)];
        var tx = a.x - b.x, ty = a.y - b.y, len = Math.hypot(tx, ty) || 1;
        var nx = -ty / len, ny = tx / len, w = TRAIL_HALF * Math.pow(1 - i / (n - 1), 0.9);
        var cx = sm[i].x - hx, cy = sm[i].y - hy;
        left.push((cx + nx * w).toFixed(1) + ' ' + (cy + ny * w).toFixed(1));
        right.push((cx - nx * w).toFixed(1) + ' ' + (cy - ny * w).toFixed(1));
      }
      return { d: 'M' + left.join('L') + 'L' + right.reverse().join('L') + 'Z', x: sm[n - 1].x - hx, y: sm[n - 1].y - hy };
    }

    function addDot(id, color, mine) {
      if (dots.size >= MAX_DOTS && !mine) return null;
      var d = { id: id, mine: !!mine, el: makeEl(color), trail: null, path: null, fade: null, hist: [], samples: [], x: 0, y: 0, ending: false, at: now(), gone: 0 };
      d.trail = d.el.firstChild; d.path = d.trail.querySelector('path'); d.fade = d.trail.querySelector('linearGradient');
      dots.set(id, d);
      requestAnimationFrame(function () { requestAnimationFrame(function () { if (!d.ending) d.el.classList.add('is-on'); }); });   // two frames so the grow-in has a start state
      return d;
    }

    function removeDot(d) {
      d.ending = true;
      d.el.classList.remove('is-on');
      d.gone = now() + 240;       // the shrink-away runs 160 ms; leave a little margin before the element goes
    }

    // the part of the canvas a dot may be on: the map, and any pictures beside it (host.bounds), else just the map
    function bounds() {
      if (host.bounds) return host.bounds();
      var n = host.natural();
      return { x0: 0, y0: 0, x1: n.w, y1: n.h };
    }

    // ---- where a map position is on screen right now (recomputed every frame, so zoom and pan carry the dots along)
    function frameBox() {
      var sr = stage.getBoundingClientRect(), ir = inner.getBoundingClientRect(), n = host.natural();
      return { sr: sr, ir: ir, w: n.w || 1, h: n.h || 1 };
    }
    function toScreen(box, x, y) {
      return { x: box.ir.left - box.sr.left + (x / box.w) * box.ir.width, y: box.ir.top - box.sr.top + (y / box.h) * box.ir.height };
    }

    function frame() {
      raf = 0;
      var t = now(), dt = Math.min(100, Math.max(1, t - lastFrame)); lastFrame = t;
      var box = frameBox(), busy = false;
      var calm = reduced.matches;

      dots.forEach(function (d, id) {
        if (d.gone && t >= d.gone) { d.el.remove(); dots.delete(id); return; }
        if (!d.ending && !d.mine && t - d.at > STALE_MS) removeDot(d);

        // the position to draw
        var px, py;
        if (d.mine) { px = d.x; py = d.y; }
        else if (d.samples.length) {
          var rt = t - DELAY_MS, s = d.samples, i = 0;
          while (i < s.length - 2 && s[i + 1].t <= rt) i++;
          var a = s[i], b = s[Math.min(i + 1, s.length - 1)];
          var k = b.t > a.t ? Math.max(0, Math.min(1, (rt - a.t) / (b.t - a.t))) : 1;
          px = a.x + (b.x - a.x) * k; py = a.y + (b.y - a.y) * k;
          if (i > 0) d.samples = s.slice(i - 1);                     // keep one older sample, drop the rest
        } else { px = d.x; py = d.y; }
        d.x = px; d.y = py;

        var p = toScreen(box, px, py);
        d.el.style.transform = 'translate3d(' + p.x.toFixed(1) + 'px,' + p.y.toFixed(1) + 'px,0)';

        // the trail follows the path the dot took in the last moments
        d.hist.push({ t: t, x: p.x, y: p.y });
        while (d.hist.length > 2 && t - d.hist[0].t > TRAIL_MS) d.hist.shift();
        var r = calm || d.ending ? null : ribbon(d.hist);
        if (!r) { d.trail.style.opacity = '0'; }
        else {
          d.path.setAttribute('d', r.d);
          d.fade.setAttribute('x2', r.x.toFixed(1)); d.fade.setAttribute('y2', r.y.toFixed(1));
          d.trail.style.opacity = '1';
        }
        busy = true;
      });
      if (busy) schedule();
    }
    function schedule() { if (!raf) { lastFrame = now(); raf = requestAnimationFrame(frame); } }

    // ---- other people's dots
    function receive(from, msg) {
      from = String(from || '');
      if (!from || !msg) return;
      var d = dots.get(from);
      if (msg.end) { if (d && !d.ending) removeDot(d); schedule(); return; }
      var b = bounds();
      var x = Math.max(b.x0, Math.min(b.x1, num(msg.x, NaN))), y = Math.max(b.y0, Math.min(b.y1, num(msg.y, NaN)));
      if (!isFinite(x) || !isFinite(y)) return;
      var t = now();
      if (d && d.ending) { d.el.remove(); dots.delete(from); d = null; }
      if (!d) { d = addDot(from, colorFor(msg.w || from), false); if (!d) return; d.x = x; d.y = y; }   // coloured by the person (msg.w), by the page only as a fallback
      d.at = t;
      if (!d.samples.length) d.samples.push({ t: t - DELAY_MS - 1, x: d.x, y: d.y });   // the first dot appears where it starts, it does not glide in from the corner
      d.samples.push({ t: t, x: x, y: y });
      if (d.samples.length > 40) d.samples = d.samples.slice(-40);
      schedule();
    }

    // ---- this person's own dot: hold the right button on the map
    var holding = null;         // { d, client: {x, y}, timer }
    var ME = '\u0000me';

    function placeOwn(clientX, clientY) {
      var nat = host.toNatural(clientX, clientY), b = bounds();
      var x = Math.max(b.x0, Math.min(b.x1, nat.x)), y = Math.max(b.y0, Math.min(b.y1, nat.y));
      holding.client = { x: clientX, y: clientY };
      holding.d.x = x; holding.d.y = y;
      host.send({ x: x, y: y, end: 0 });
      schedule();
    }
    function begin(e) {
      if (holding) return;
      var d = dots.get(ME);
      if (d) { d.el.remove(); dots.delete(ME); }
      d = addDot(ME, colorFor(host.myId ? host.myId() : 'mine'), true);
      holding = { d: d, client: null, timer: 0 };
      stage.classList.add('is-pointing');
      placeOwn(e.clientX, e.clientY);
      d.hist.length = 0;
      holding.timer = setInterval(function () { if (holding && holding.client) { var c = holding.client; placeOwn(c.x, c.y); } }, KEEPALIVE_MS);
    }
    function end() {
      if (!holding) return;
      clearInterval(holding.timer);
      removeDot(holding.d);
      holding = null;
      stage.classList.remove('is-pointing');
      host.send({ end: 1 });
      schedule();
    }

    stage.addEventListener('mousedown', function (e) {
      if (e.button !== 2) return;
      e.preventDefault(); e.stopPropagation();                      // a right click does nothing else on the map
      begin(e);
    }, true);
    window.addEventListener('mousemove', function (e) { if (holding) placeOwn(e.clientX, e.clientY); });
    window.addEventListener('mouseup', function (e) { if (e.button === 2) end(); }, true);
    window.addEventListener('blur', end);
    document.addEventListener('visibilitychange', function () { if (document.hidden) end(); });
    stage.addEventListener('contextmenu', function (e) { e.preventDefault(); });      // no browser menu over the map

    return { receive: receive, holding: function () { return !!holding; }, dotCount: function () { return dots.size; } };
  }

  window.MapPointer = { create: create };
}());
