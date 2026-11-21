/* Map pointer: hold the right mouse button on the map and your cursor turns into a small dot that everyone looking at the map sees, live.
 *
 * Your own dot follows the mouse exactly.  Other people's dots arrive about ten times a second over the live channel (map-live.js, kind "ptr");
 * they are played back about 120 ms behind real time, interpolated between the last two positions, so the path is smooth instead of stepping.
 * A short soft trail behind the dot, as long as the dot is fast, gives the motion blur.  Nothing here is saved: when the button is released (or
 * the messages stop for a few seconds) the dot shrinks away.
 *
 * Positions travel in map coordinates (the same ones pins use), so the dot lands on the same spot of the map for everyone whatever their zoom or pan.
 * The dot itself keeps its size on screen.  Only transform and opacity are animated; with reduced motion there is no trail and no grow/shrink.
 *
 *   var pointer = MapPointer.create({ stage, inner, natural: () => ({ w, h }), toNatural: (clientX, clientY) => ({ x, y }), send: (msg) => {}, myId: () => liveUid });
 *   pointer.receive(senderId, { x, y, end })
 */
(function () {
  'use strict';

  var DELAY_MS = 120;         // how far behind real time other people's dots are drawn (a little over one message interval)
  var STALE_MS = 3500;        // a dot that has gone quiet this long is taken away (a held button keeps sending a heartbeat)
  var KEEPALIVE_MS = 1000;
  var MAX_DOTS = 40;
  var SPEED_FOR_TRAIL = 90;   // px per second below which there is no trail
  var TRAIL_PX = 96;          // length of the trail element at full speed (the trail is scaled from this; keep in step with .map-ptr-trail in style.css)
  var COLORS = ['#f0b64d', '#6fc7dd', '#ef8470', '#9fd67a', '#c9a1f5', '#f292c3'];

  function num(v, fallback) { v = Number(v); return isFinite(v) ? v : fallback; }
  function now() { return (window.performance && performance.now) ? performance.now() : Date.now(); }
  function colorFor(id) {
    var h = 0, s = String(id);
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
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
    var raf = 0, lastFrame = 0;

    function makeEl(color) {
      var el = document.createElement('div');
      el.className = 'map-ptr';
      el.style.setProperty('--ptr', color);
      el.innerHTML = '<i class="map-ptr-trail"></i><i class="map-ptr-dot"></i>';
      layer.appendChild(el);
      return el;
    }

    function addDot(id, color, mine) {
      if (dots.size >= MAX_DOTS && !mine) return null;
      var d = { id: id, mine: !!mine, el: makeEl(color), trail: null, samples: [], x: 0, y: 0, sx: 0, sy: 0, vx: 0, vy: 0, placed: false, ending: false, at: now(), gone: 0 };
      d.trail = d.el.firstChild;
      dots.set(id, d);
      requestAnimationFrame(function () { requestAnimationFrame(function () { if (!d.ending) d.el.classList.add('is-on'); }); });   // two frames so the grow-in has a start state
      return d;
    }

    function removeDot(d) {
      d.ending = true;
      d.el.classList.remove('is-on');
      d.gone = now() + 240;       // the shrink-away runs 160 ms; leave a little margin before the element goes
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
        // velocity on screen (px per second), smoothed so the trail does not flicker
        if (d.placed) {
          var ix = (p.x - d.sx) / dt * 1000, iy = (p.y - d.sy) / dt * 1000, f = 1 - Math.exp(-dt / 60);
          d.vx += (ix - d.vx) * f; d.vy += (iy - d.vy) * f;
        }
        d.sx = p.x; d.sy = p.y; d.placed = true;
        d.el.style.transform = 'translate3d(' + p.x.toFixed(1) + 'px,' + p.y.toFixed(1) + 'px,0)';

        var speed = Math.sqrt(d.vx * d.vx + d.vy * d.vy);
        if (calm || speed < SPEED_FOR_TRAIL) { d.trail.style.opacity = '0'; }
        else {
          var len = Math.min(1, speed / 1400);                       // full trail at about 1400 px/s
          d.trail.style.opacity = String(Math.min(0.85, 0.25 + len)).slice(0, 5);
          d.trail.style.transform = 'rotate(' + Math.atan2(d.vy, d.vx).toFixed(3) + 'rad) scaleX(' + (0.25 + len * 0.75).toFixed(3) + ')';
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
      var n = host.natural();
      var x = Math.max(0, Math.min(n.w, num(msg.x, NaN))), y = Math.max(0, Math.min(n.h, num(msg.y, NaN)));
      if (!isFinite(x) || !isFinite(y)) return;
      var t = now();
      if (d && d.ending) { d.el.remove(); dots.delete(from); d = null; }
      if (!d) { d = addDot(from, colorFor(from), false); if (!d) return; d.x = x; d.y = y; }
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
      var nat = host.toNatural(clientX, clientY), n = host.natural();
      var x = Math.max(0, Math.min(n.w, nat.x)), y = Math.max(0, Math.min(n.h, nat.y));
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
      d.placed = false;
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
