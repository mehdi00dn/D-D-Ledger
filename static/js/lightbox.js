// Shared image lightbox. Any image can opt in by adding:
//   data-lightbox-src="<full image url>"
//   data-delete-url="<optional POST endpoint to delete this image>"
// Mouse: click to zoom toward the pointer, wheel to zoom, hold and drag to pan.
// Touch (DL-186): pinch to zoom around the fingers, double-tap to zoom in and out, drag to pan a zoomed picture (it stops against
// the edges with a rubber band), swipe sideways to turn the page, swipe up or down to dismiss.  Every gesture follows the finger
// one to one, can be caught mid-flight, and lets go with the finger's own speed.

(function () {
  'use strict';
  const overlay = document.getElementById('image-lightbox');
  if (!overlay) return;

  const img = document.getElementById('lightbox-img');
  const closeBtn = document.getElementById('lightbox-close-btn');
  const deleteBtn = document.getElementById('lightbox-delete-btn');
  const prevBtn = document.getElementById('lightbox-prev-btn');
  const nextBtn = document.getElementById('lightbox-next-btn');
  const countEl = document.getElementById('lightbox-count');
  const stage = overlay.querySelector('.lightbox-stage');

  const ZOOM_SCALE = 2.4;     // what a click or a double-tap zooms to
  const MAX_SCALE = 8;        // how far you can zoom in
  const MIN_SCALE = 0.55;     // a pinch may go this far below 1 before it springs back
  const DRAG_THRESHOLD = 6;   // px of movement before a mouse press counts as a drag, not a click
  const AXIS_LOCK = 10;       // px of finger travel before a swipe picks sideways or up/down
  const TAP_SLOP = 8;         // a finger that moves less than this is still a tap
  const DOUBLE_TAP_MS = 300;
  const DOUBLE_TAP_PX = 40;
  const FLICK = 0.45;         // px per ms: a sideways flick this fast turns the page even if it was short
  const FLICK_DOWN = 0.6;
  const EASE = 'cubic-bezier(0.32, 0.72, 0, 1)';

  let zoomed = false;
  let scale = 1;              // current zoom; `zoomed` is simply scale > 1
  let panX = 0;               // where the picture's centre sits relative to its resting place, in px
  let panY = 0;
  let offScale = 1;           // a little extra shrink while the picture is dragged away to dismiss it
  let dragging = false;       // a finger or the mouse has hold of the picture: no easing while it does
  let dragStart = { x: 0, y: 0 };
  let panStart = { x: 0, y: 0 };
  let movedDistance = 0;
  let currentDeleteUrl = null;
  let currentImages = [];
  let currentIndex = -1;
  let glideTimer = null;

  function render() {
    zoomed = scale > 1.001;
    const moved = panX || panY || scale !== 1 || offScale !== 1;
    img.style.transform = moved ? `translate(${panX}px, ${panY}px) scale(${scale * offScale})` : '';
    img.classList.toggle('zoomed', zoomed);
    stage.classList.toggle('is-dragging', dragging);
  }

  // Where the picture sits, ignoring whatever transform it has right now (offsetLeft and friends do not see transforms).
  function geom() {
    const r = stage.getBoundingClientRect();
    const w = img.offsetWidth, h = img.offsetHeight;
    return {
      cx: r.left + stage.clientLeft + img.offsetLeft + w / 2,
      cy: r.top + stage.clientTop + img.offsetTop + h / 2,
      w, h, vw: stage.clientWidth, vh: stage.clientHeight
    };
  }

  // How far the zoomed picture may be pushed before its edge would leave a gap.
  function maxPan() {
    const g = geom();
    return { x: Math.max(0, (g.w * scale - g.vw) / 2), y: Math.max(0, (g.h * scale - g.vh) / 2) };
  }

  function num(value, lo, hi) { return Math.min(hi, Math.max(lo, value)); }

  function clampPan() {
    const m = maxPan();
    panX = num(panX, -m.x, m.x);
    panY = num(panY, -m.y, m.y);
  }

  // Past an edge the picture keeps following the finger, but with growing resistance (iOS's rubber band).
  function rubber(over, dim) {
    const c = 0.55, a = Math.abs(over);
    return Math.sign(over) * (a * dim * c) / (dim + c * a);
  }
  function soft(value, max, dim) {
    if (value > max) return max + rubber(value - max, dim);
    if (value < -max) return -max + rubber(value + max, dim);
    return value;
  }

  function cancelGlide() {
    clearTimeout(glideTimer);
    glideTimer = null;
    img.style.transitionDuration = '';
  }

  function reset(instant) {
    cancelGlide();
    scale = 1; panX = 0; panY = 0; offScale = 1;
    dragging = false;
    gesture = null;
    pointers.clear();
    overlay.style.removeProperty('--lb-dim');
    if (instant) {
      img.style.transition = 'none';
      render();
      void img.offsetWidth;                  // commit the jump before easing is switched back on
      img.style.transition = '';
    } else {
      render();
    }
  }

  function updateNavigation() {
    const n = currentImages.length;
    const hasPrev = currentIndex > 0;
    const hasNext = currentIndex >= 0 && currentIndex < n - 1;
    prevBtn.classList.toggle('is-off', !hasPrev);
    nextBtn.classList.toggle('is-off', !hasNext);
    overlay.classList.toggle('is-single', n <= 1);
    if (countEl) countEl.textContent = n > 1 ? `${currentIndex + 1} of ${n}` : '';
  }

  // Same guard as the details page viewer: while the next picture loads, the previous one is hidden (spinner after 120 ms), never shown as if it were the new one.
  let loadWait = null;
  function setSource(url, force) {
    clearTimeout(loadWait);
    stage.classList.remove('is-failed');
    if (!force && img.getAttribute('src') === url) { stage.classList.remove('is-loading'); return; }
    if (force) img.removeAttribute('src');
    img.src = url;
    loadWait = setTimeout(() => { if (!img.complete) stage.classList.add('is-loading'); }, 120);
  }
  img.addEventListener('load', () => {
    clearTimeout(loadWait);
    stage.classList.remove('is-loading', 'is-failed');
    [currentIndex - 1, currentIndex + 1].forEach(n => { if (currentImages[n]) (new Image()).src = currentImages[n].src; });
  });
  img.addEventListener('error', () => {
    if (!img.getAttribute('src')) return;                 // close() clears the source: not a failure
    clearTimeout(loadWait);
    stage.classList.remove('is-loading');
    stage.classList.add('is-failed');
  });

  function open(src, deleteUrl, images) {
    currentImages = Array.isArray(images) ? images : [];
    currentIndex = currentImages.findIndex(item => item.src === src);
    if (currentIndex < 0 && src) {
      currentImages = [{ src, deleteUrl: deleteUrl || null }];
      currentIndex = 0;
    }
    setSource(src, true);
    currentDeleteUrl = deleteUrl || null;
    deleteBtn.hidden = !currentDeleteUrl;
    updateNavigation();
    reset(true);
    overlay.hidden = false;
  }

  // A quick page turn: the new picture swings in from its spine edge (left when going forward, right when going back).
  // 220 ms, ease-out, and interruptible: a new press cancels the turn in flight instead of queueing behind it.
  let turn = null;
  const calm = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  function pageTurn(direction) {
    if (turn) { turn.cancel(); turn = null; }
    if (!img.animate) return;
    const forward = direction > 0;
    const from = calm.matches
      ? { opacity: 0.4 }
      : { opacity: 0.35, transform: `perspective(1100px) rotateY(${forward ? -24 : 24}deg) scale(0.985)`, transformOrigin: forward ? 'left center' : 'right center' };
    const to = calm.matches
      ? { opacity: 1 }
      : { opacity: 1, transform: 'perspective(1100px) rotateY(0deg) scale(1)', transformOrigin: forward ? 'left center' : 'right center' };
    turn = img.animate([from, to], { duration: calm.matches ? 120 : 220, easing: 'cubic-bezier(0.32, 0.72, 0, 1)' });
    turn.onfinish = turn.oncancel = () => { turn = null; };
  }

  // A swipe turns the page differently from the arrows: the old picture has already left with the finger, so the new one slides in
  // from the side it is coming from.  Same 240 ms curve, and a new touch cancels it.
  function slideIn(direction) {
    if (turn) { turn.cancel(); turn = null; }
    if (!img.animate) return;
    const from = calm.matches ? { opacity: 0.4 } : { opacity: 0.2, transform: `translateX(${direction * 56}px)` };
    const to = calm.matches ? { opacity: 1 } : { opacity: 1, transform: 'translateX(0)' };
    turn = img.animate([from, to], { duration: calm.matches ? 120 : 240, easing: EASE });
    turn.onfinish = turn.oncancel = () => { turn = null; };
  }

  function navigate(direction, how) {
    const nextIndex = currentIndex + direction;
    if (nextIndex < 0 || nextIndex >= currentImages.length) return;
    const item = currentImages[nextIndex];
    currentIndex = nextIndex;
    setSource(item.src);
    currentDeleteUrl = item.deleteUrl || null;
    deleteBtn.hidden = !currentDeleteUrl;
    updateNavigation();
    reset(how === 'swipe');
    if (how === 'swipe') slideIn(direction); else pageTurn(direction);
  }

  function close() {
    if (turn) { turn.cancel(); turn = null; }
    overlay.hidden = true;
    clearTimeout(loadWait);
    stage.classList.remove('is-loading', 'is-failed');
    img.removeAttribute('src');
    currentDeleteUrl = null;
    currentImages = [];
    currentIndex = -1;
    updateNavigation();
    reset(true);
    overlay.style.transition = '';
    overlay.style.opacity = '';
  }

  // Zoom to `next`, keeping the picture point under (clientX, clientY) where it is.
  function zoomAt(next, clientX, clientY) {
    next = num(next, 1, MAX_SCALE);
    if (next <= 1.001) {
      scale = 1; panX = 0; panY = 0;
      return;
    }
    const g = geom();
    const ratio = next / scale;
    panX = (clientX - g.cx) - ((clientX - g.cx) - panX) * ratio;
    panY = (clientY - g.cy) - ((clientY - g.cy) - panY) * ratio;
    scale = next;
    clampPan();
  }

  // ---- Open triggers: any element with data-lightbox-src ----
  document.addEventListener('click', (e) => {
    const trigger = e.target.closest('[data-lightbox-src]');
    if (!trigger) return;
    if (trigger.closest('a')) { e.preventDefault(); e.stopPropagation(); }   // an avatar inside a link previews instead of following it
    const group = trigger.closest('.sheet-thumbs');
    const images = group
      ? Array.from(group.querySelectorAll('[data-lightbox-src]')).map(el => ({
          src: el.dataset.lightboxSrc,
          deleteUrl: el.dataset.deleteUrl || null
        }))
      : [{ src: trigger.dataset.lightboxSrc, deleteUrl: trigger.dataset.deleteUrl || null }];
    open(trigger.dataset.lightboxSrc, trigger.dataset.deleteUrl, images);
  });

  // An avatar that is a button (role="button") opens with Enter or Space too.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const trigger = e.target.closest && e.target.closest('[data-lightbox-src][role="button"]');
    if (!trigger || e.target !== trigger) return;
    e.preventDefault();
    trigger.click();
  });

  // ---- Mouse: click to zoom, drag to pan ----
  stage.addEventListener('click', () => {
    if (stage.classList.contains('is-failed') && currentImages[currentIndex]) setSource(currentImages[currentIndex].src, true);
  });
  img.addEventListener('mousedown', (e) => {
    if (stage.classList.contains('is-failed')) return;
    dragging = true;
    movedDistance = 0;
    dragStart = { x: e.clientX, y: e.clientY };
    panStart = { x: panX, y: panY };
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragging || gesture) return;
    const dx = e.clientX - dragStart.x;
    const dy = e.clientY - dragStart.y;
    movedDistance = Math.hypot(dx, dy);
    if (zoomed) {
      panX = panStart.x + dx;
      panY = panStart.y + dy;
      clampPan();
      render();
    }
  });

  window.addEventListener('mouseup', (e) => {
    if (!dragging || gesture) return;
    dragging = false;
    if (movedDistance < DRAG_THRESHOLD) {
      // A real click (not a drag): toggle zoom
      if (!zoomed) zoomAt(ZOOM_SCALE, e.clientX, e.clientY);
      else zoomAt(1);
    }
    render();
  });

  // ---- Touch (and pen): pinch, double-tap, pan, swipe ----
  const pointers = new Map();   // pointerId -> { x, y } for every finger down
  let gesture = null;           // the gesture the fingers are making right now
  let lastTap = { t: 0, x: 0, y: 0 };

  function mid(a, b) { return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }
  function gap(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

  // Read where the picture is on screen right now (it may be halfway through an ease) and carry on from exactly there.
  function catchPicture() {
    cancelGlide();
    if (turn) { turn.cancel(); turn = null; }
    const m = new DOMMatrix(getComputedStyle(img).transform);
    scale = Math.hypot(m.a, m.b) || 1;
    panX = m.e; panY = m.f; offScale = 1;
    img.style.transition = 'none';
    dragging = true;
    render();
  }

  // (Re)start the gesture from the fingers that are down: two make a pinch, one makes a pan (zoomed) or a swipe (not).
  function begin(fromPinch) {
    const list = Array.from(pointers.values());
    const geo = geom();
    if (list.length >= 2) {
      gesture = { mode: 'pinch', geo, d0: gap(list[0], list[1]) || 1, s0: scale, mid0: mid(list[0], list[1]), pan0: { x: panX, y: panY } };
    } else if (fromPinch && scale <= 1.001) {
      gesture = { mode: 'ignore' };          // a pinch-out ended with one finger still down: let the picture settle, ignore that finger
      settle();
    } else {
      const a = list[0];
      gesture = {
        mode: zoomed ? 'pan' : 'idle', geo, x0: a.x, y0: a.y, pan0: { x: panX, y: panY }, t0: performance.now(),
        axis: null, tap: !fromPinch, samples: [{ t: performance.now(), x: a.x, y: a.y }]
      };
    }
  }

  function velocity(g) {
    const s = g.samples, last = s[s.length - 1];
    let first = s[0];
    for (let i = s.length - 1; i >= 0; i--) { if (last.t - s[i].t >= 60) { first = s[i]; break; } }
    const dt = last.t - first.t;
    return dt > 0 ? { x: (last.x - first.x) / dt, y: (last.y - first.y) / dt } : { x: 0, y: 0 };
  }

  // Bring the picture back into a legal place (zoom between 1 and the maximum, no gaps at the edges) with the usual ease.
  function settle() {
    dragging = false;
    img.style.transition = '';
    scale = Math.min(MAX_SCALE, scale);
    if (scale < 1.02) { scale = 1; panX = 0; panY = 0; } else clampPan();
    offScale = 1;
    overlay.style.removeProperty('--lb-dim');
    render();
  }

  // Ease to a place, then do `done` (turning the page, closing): the finger let go, so nothing is under it any more.
  function glideTo(x, y, s, ms, done) {
    dragging = false;
    img.style.transition = '';
    img.style.transitionDuration = ms + 'ms';
    panX = x; panY = y; scale = s; offScale = 1;
    render();
    glideTimer = setTimeout(() => { glideTimer = null; img.style.transitionDuration = ''; done(); }, ms);
  }

  function tapAt(x, y, now) {
    const twice = now - lastTap.t < DOUBLE_TAP_MS && Math.hypot(x - lastTap.x, y - lastTap.y) < DOUBLE_TAP_PX;
    if (twice) {
      lastTap = { t: 0, x: 0, y: 0 };
      if (zoomed) zoomAt(1); else zoomAt(ZOOM_SCALE, x, y);
    } else {
      lastTap = { t: now, x, y };
    }
    settle();
  }

  stage.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse' || stage.classList.contains('is-failed') || !img.getAttribute('src')) return;
    e.preventDefault();                         // no compatibility mouse events (and no double zoom) after a touch
    try { stage.setPointerCapture(e.pointerId); } catch (err) { /* the finger is already gone */ }
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    catchPicture();
    begin(false);
  });

  stage.addEventListener('pointermove', (e) => {
    if (!pointers.has(e.pointerId) || !gesture) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const g = gesture;
    if (g.mode === 'ignore') return;
    if (g.mode === 'pinch') {
      const list = Array.from(pointers.values());
      const m = mid(list[0], list[1]);
      const next = num(g.s0 * gap(list[0], list[1]) / g.d0, MIN_SCALE, MAX_SCALE * 1.25);
      const ratio = next / g.s0;
      scale = next;
      panX = (m.x - g.geo.cx) - ((g.mid0.x - g.geo.cx) - g.pan0.x) * ratio;
      panY = (m.y - g.geo.cy) - ((g.mid0.y - g.geo.cy) - g.pan0.y) * ratio;
      render();
      return;
    }
    const dx = e.clientX - g.x0, dy = e.clientY - g.y0;
    g.samples.push({ t: performance.now(), x: e.clientX, y: e.clientY });
    if (g.samples.length > 8) g.samples.shift();
    if (Math.hypot(dx, dy) > TAP_SLOP) g.tap = false;
    if (g.mode === 'pan') {
      const m = maxPan();
      panX = soft(g.pan0.x + dx, m.x, g.geo.vw);
      panY = soft(g.pan0.y + dy, m.y, g.geo.vh);
      render();
      return;
    }
    if (!g.axis && Math.hypot(dx, dy) > AXIS_LOCK) g.axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
    if (g.axis === 'x') {
      const toward = dx < 0 ? 1 : -1;           // dragging left shows the next picture
      panX = currentImages[currentIndex + toward] ? dx : rubber(dx, g.geo.vw);
      panY = 0;
      render();
    } else if (g.axis === 'y') {
      panY = dy; panX = 0;
      offScale = 1 - Math.min(0.18, Math.abs(dy) / 1400);
      overlay.style.setProperty('--lb-dim', String(Math.max(0, 1 - Math.abs(dy) / 320)));
      render();
    }
  });

  function lift(e) {
    if (!pointers.has(e.pointerId)) return;
    pointers.delete(e.pointerId);
    try { stage.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
    const g = gesture;
    if (!g) return;
    if (pointers.size > 0) { catchPicture(); begin(g.mode === 'pinch'); return; }   // one finger is still down: carry on from here
    gesture = null;
    if (e.type === 'pointercancel' || g.mode === 'pinch' || g.mode === 'ignore') { settle(); return; }
    if (g.tap) { tapAt(e.clientX, e.clientY, e.timeStamp); return; }          // a tap, zoomed or not (two make a double-tap)
    const v = velocity(g);
    if (g.mode === 'pan') {                      // let go of a zoomed picture: it coasts a little, then rests inside its edges
      dragging = false;
      img.style.transition = '';
      panX += v.x * 220; panY += v.y * 220;
      clampPan();
      render();
      return;
    }
    if (g.axis === 'x') {
      const fast = Math.abs(v.x) > FLICK;
      const direction = (fast ? v.x < 0 : panX < 0) ? 1 : -1;
      const far = Math.abs(panX) > g.geo.vw * 0.25;
      if ((far || fast) && currentImages[currentIndex + direction]) {
        glideTo(-direction * (g.geo.vw + 40), 0, 1, 160, () => navigate(direction, 'swipe'));
      } else settle();
      return;
    }
    if (g.axis === 'y') {
      const fast = Math.abs(v.y) > FLICK_DOWN;
      if (Math.abs(panY) > 110 || fast) {
        const sign = (fast ? v.y : panY) > 0 ? 1 : -1;
        overlay.style.transition = 'opacity 200ms ease-out';
        overlay.style.opacity = '0';
        glideTo(0, sign * window.innerHeight * 0.5, 0.8, 200, close);
      } else settle();
      return;
    }
    settle();
  }
  stage.addEventListener('pointerup', lift);
  stage.addEventListener('pointercancel', lift);
  stage.addEventListener('lostpointercapture', lift);

  // ---- Mouse wheel zooms the picture (and never scrolls the page behind) ----
  let wheelTimer = null;
  overlay.addEventListener('wheel', (e) => {
    e.preventDefault();
    if (!img.getAttribute('src')) return;
    const g = geom();
    const overImage = e.clientX >= g.cx - g.w / 2 && e.clientX <= g.cx + g.w / 2 && e.clientY >= g.cy - g.h / 2 && e.clientY <= g.cy + g.h / 2;
    const px = overImage ? e.clientX : g.cx;
    const py = overImage ? e.clientY : g.cy;
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;   // lines / pages -> pixels
    zoomAt(scale * Math.exp(-e.deltaY * unit * 0.0015), px, py);
    stage.classList.add('is-wheeling');                                  // no easing while the wheel turns
    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(() => stage.classList.remove('is-wheeling'), 150);
    render();
  }, { passive: false });

  // The arrows answer a touch when the finger lifts, not when the browser decides the touch was a click: right after a swipe
  // some browsers swallow that click, and a tap that does nothing feels broken.  The click that follows is then ignored.
  function wireArrow(btn, direction) {
    let downAt = 0, handledAt = 0;
    btn.addEventListener('pointerdown', (e) => { if (e.pointerType !== 'mouse') downAt = performance.now(); });
    btn.addEventListener('pointerup', (e) => {
      if (e.pointerType === 'mouse' || !downAt || performance.now() - downAt > 600) return;
      const r = btn.getBoundingClientRect();
      downAt = 0;
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) return;
      handledAt = performance.now();
      navigate(direction);
    });
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (e.detail > 0 && performance.now() - handledAt < 700) return;   // already handled on pointerup
      navigate(direction);
    });
  }
  wireArrow(prevBtn, -1);
  wireArrow(nextBtn, 1);

  // ---- Delete ----
  deleteBtn.addEventListener('click', async () => {
    if (!currentDeleteUrl) return;
    const ok = window.confirmAction
      ? await window.confirmAction('Remove this image? This cannot be undone.')
      : confirm('Remove this image? This cannot be undone.');
    if (!ok) return;
    try {
      await fetch(currentDeleteUrl, { method: 'POST' });
    } finally {
      close();
      window.location.reload();
    }
  });

  // ---- Close ----
  closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) close();
  });
  document.addEventListener('keydown', (e) => {
    if (overlay.hidden) return;
    if (e.key === 'Escape') close();
    else if (e.key === 'ArrowLeft') navigate(-1);
    else if (e.key === 'ArrowRight') navigate(1);
  });

  window.openLightbox = open;
})();
