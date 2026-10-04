/* The loading ring shown over avatar / map-image previews while an upload is in flight.
   A re-creation of the supplied "Insider loading circle" Lottie (same shapes, timing and easing),
   drawn as plain SVG so no player library is needed:
     two arcs chase each other round a circle; each one grows from its leading edge, then
     shrinks into its tail while the whole circle turns once.  79 frames at 25 fps = 3.16 s, looping.
   Colour comes from CSS (currentColor).  Rings that are hidden do no work. */
(function () {
  var NS = 'http://www.w3.org/2000/svg';
  var FPS = 25, LOOP = 79;

  // cubic-bezier(0.333, 0, 0.667, 1): the Lottie keyframe easing
  var X1 = 0.333, Y1 = 0, X2 = 0.667, Y2 = 1;
  function bez(a, b, t) { return 3 * a * t * (1 - t) * (1 - t) + 3 * b * t * t * (1 - t) + t * t * t; }
  function ease(x) {
    var lo = 0, hi = 1, t = x;
    for (var i = 0; i < 24; i++) {
      t = (lo + hi) / 2;
      if (bez(X1, X2, t) < x) lo = t; else hi = t;
    }
    return bez(Y1, Y2, t);
  }
  // value of a keyframed property: holds `a` before t0 and `b` after t1
  function seg(f, t0, t1, a, b) {
    if (f <= t0) return a;
    if (f >= t1) return b;
    return a + (b - a) * ease((f - t0) / (t1 - t0));
  }

  // The two layers of the original: frame offset, and trim-end / trim-start / rotation keyframes (relative to the offset).
  var LAYERS = [
    { at: 0,  end: [0, 25], start: [12, 40], rot: [0, 40] },
    { at: 39, end: [0, 25], start: [12, 40], rot: [0, 40] }
  ];

  function build(el) {
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '15 15 76 76');            // the circle (r 28, stroke 10) centred at 53,53
    svg.setAttribute('aria-hidden', 'true');
    el._arcs = LAYERS.map(function () {
      var c = document.createElementNS(NS, 'circle');
      c.setAttribute('cx', '53'); c.setAttribute('cy', '53'); c.setAttribute('r', '28');
      c.setAttribute('pathLength', '100');
      c.setAttribute('fill', 'none'); c.setAttribute('stroke', 'currentColor');
      c.setAttribute('stroke-width', '10'); c.setAttribute('stroke-linecap', 'round');
      svg.appendChild(c);
      return c;
    });
    el.appendChild(svg);
  }

  function draw(el, ms) {
    var frame = ((ms / 1000) * FPS) % LOOP;
    LAYERS.forEach(function (L, i) {
      var arc = el._arcs[i], f = frame - L.at;
      if (f < 0 || f > 40) { arc.style.display = 'none'; return; }
      var s = seg(f, L.start[0], L.start[1], 0, 100);
      var e = seg(f, L.end[0], L.end[1], 0, 100);
      var rot = seg(f, L.rot[0], L.rot[1], -90, 270);
      var len = e - s;
      if (len < 0.05) { arc.style.display = 'none'; return; }
      arc.style.display = '';
      arc.setAttribute('stroke-dasharray', len + ' ' + (200 - len));
      arc.setAttribute('stroke-dashoffset', String(-s));
      arc.setAttribute('transform', 'rotate(' + rot + ' 53 53)');
    });
  }

  var rings = [].slice.call(document.querySelectorAll('.upload-progress-circle'));
  if (!rings.length) return;
  rings.forEach(build);

  function tick(now) {
    rings.forEach(function (el) {
      if (el.hidden) { el._t0 = null; return; }              // restart from the first frame each time it appears
      if (el._t0 == null) el._t0 = now;
      draw(el, now - el._t0);
    });
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}());
