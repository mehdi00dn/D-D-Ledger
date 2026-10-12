/* Small phone-only behaviours that need a little script (the layout itself is CSS):
 *  - Characters page: the search box is a button until it is tapped
 *  - Map editor: the toolbar is a "Tools" menu that folds away once a tool is picked
 *  - Phone held sideways: the top and bottom bars hide when you scroll down and come back when you scroll up;
 *    a small dock brings them back by hand and also holds a full-screen button
 */
(function () {
  'use strict';
  var root = document.documentElement;

  // ---- characters page: search that opens on tap ----
  document.querySelectorAll('[data-collapsible-search]').forEach(function (box) {
    var input = box.querySelector('input');
    var wrap = box.closest('.char-tools');
    function sync() {
      var open = document.activeElement === input || input.value !== '';
      box.classList.toggle('is-open', open);
      if (wrap) wrap.classList.toggle('search-open', open);
    }
    box.querySelector('[data-search-open]').addEventListener('click', function () { box.classList.add('is-open'); if (wrap) wrap.classList.add('search-open'); input.focus(); });
    input.addEventListener('focus', sync);
    input.addEventListener('blur', function () { setTimeout(sync, 0); });
    input.addEventListener('input', sync);
    sync();
  });

  // ---- map editor: tools menu ----
  var tb = document.getElementById('map-toolbar');
  var toggle = document.getElementById('map-toolbar-toggle');
  if (tb && toggle) {
    var icon = document.getElementById('map-toolbar-toggle-icon');
    function setOpen(open) {
      tb.classList.toggle('is-open', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    function showActiveTool() {
      var on = tb.querySelector('.map-tool-btn.active[data-tool]');
      if (icon && on) icon.innerHTML = on.innerHTML;
    }
    toggle.addEventListener('click', function () { setOpen(!tb.classList.contains('is-open')); });
    tb.addEventListener('click', function (ev) {
      var pick = ev.target.closest('[data-tool]');
      if (pick && !ev.target.closest('.roster-menu') && tb.classList.contains('is-open')) setTimeout(function () { setOpen(false); }, 140);
    });
    new MutationObserver(showActiveTool).observe(tb, { subtree: true, attributes: true, attributeFilter: ['class'] });
    showActiveTool();
  }

  // ---- "More" sheet: rises from the bottom; a drag down (or a quick flick), a tap outside or Escape closes it ----
  var moreBtn = document.getElementById('nav-more-btn');
  var sheet = document.getElementById('more-sheet');
  var scrim = document.getElementById('more-scrim');
  var grip = document.getElementById('more-grip');
  if (moreBtn && sheet && scrim) {
    var hideTimer = 0;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)');
    var isOpen = function () { return sheet.classList.contains('is-open'); };
    var openMore = function () {
      clearTimeout(hideTimer);
      sheet.hidden = false; scrim.hidden = false;
      sheet.style.transform = '';
      void sheet.offsetHeight;                              // let the closed position paint, so the rise animates
      sheet.classList.add('is-open'); scrim.classList.add('is-open');
      moreBtn.setAttribute('aria-expanded', 'true');
      var first = sheet.querySelector('a, button');
      if (first) try { first.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
    };
    var closeMore = function (restoreFocus) {
      if (!isOpen()) return;
      sheet.classList.remove('is-open'); scrim.classList.remove('is-open');
      sheet.style.transform = '';
      moreBtn.setAttribute('aria-expanded', 'false');
      hideTimer = setTimeout(function () { sheet.hidden = true; scrim.hidden = true; }, reduce && reduce.matches ? 20 : 360);
      if (restoreFocus) moreBtn.focus({ preventScroll: true });
    };
    moreBtn.addEventListener('click', function () { if (isOpen()) closeMore(); else openMore(); });
    scrim.addEventListener('click', function () { closeMore(true); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && isOpen()) closeMore(true); });
    sheet.addEventListener('click', function (e) { if (e.target.closest('a[href]')) closeMore(); });
    // the sheet leaves when the layout stops being the phone one (rotate to a big window)
    var phoneish = window.matchMedia('(max-width: 880px), (max-height: 500px) and (orientation: landscape)');
    var onLayout = function () { if (!phoneish.matches) { sheet.classList.remove('is-open'); scrim.classList.remove('is-open'); sheet.hidden = true; scrim.hidden = true; } };
    if (phoneish.addEventListener) phoneish.addEventListener('change', onLayout);

    // drag the grip: follows the finger 1:1, resists upwards, and a flick closes it even from a short drag
    var drag = null;
    function endDrag(e) {
      if (!drag) return;
      var d = drag; drag = null;
      sheet.classList.remove('is-dragging');
      try { grip.releasePointerCapture(d.id); } catch (err) { /* ignore */ }
      var dt = Math.max(1, performance.now() - d.t0), v = d.dy / dt;      // px per ms, positive = downward
      if (d.dy > sheet.offsetHeight * 0.3 || v > 0.5) closeMore(); else sheet.style.transform = '';
    }
    if (grip) {
      grip.addEventListener('pointerdown', function (e) {
        drag = { id: e.pointerId, y0: e.clientY, t0: performance.now(), dy: 0 };
        sheet.classList.add('is-dragging');
        try { grip.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      });
      grip.addEventListener('pointermove', function (e) {
        if (!drag || e.pointerId !== drag.id) return;
        var raw = e.clientY - drag.y0;
        drag.dy = raw;
        var y = raw >= 0 ? raw : -Math.sqrt(-raw) * 2.2;                  // rubber-band when pulled up
        sheet.style.transform = 'translateY(' + y + 'px)';
      });
      grip.addEventListener('pointerup', endDrag);
      grip.addEventListener('pointercancel', endDrag);
    }
  }

  // ---- full screen ----
  var fsEl = root;
  var canFs = !!(fsEl.requestFullscreen || fsEl.webkitRequestFullscreen);
  function isFs() { return !!(document.fullscreenElement || document.webkitFullscreenElement); }
  function syncFs() {
    root.classList.toggle('is-fullscreen', isFs());
    document.querySelectorAll('.js-fullscreen').forEach(function (b) {
      b.hidden = !canFs;
      b.setAttribute('aria-pressed', isFs() ? 'true' : 'false');
      b.title = isFs() ? 'Leave full screen' : 'Full screen';
    });
  }
  document.querySelectorAll('.js-fullscreen').forEach(function (b) {
    b.addEventListener('click', function () {
      try {
        if (isFs()) { (document.exitFullscreen || document.webkitExitFullscreen).call(document); }
        else { (fsEl.requestFullscreen || fsEl.webkitRequestFullscreen).call(fsEl); }
      } catch (e) { /* not allowed here */ }
    });
  });
  document.addEventListener('fullscreenchange', syncFs);
  document.addEventListener('webkitfullscreenchange', syncFs);
  syncFs();

  // ---- sideways phone: bars that get out of the way ----
  var sideways = window.matchMedia('(orientation: landscape) and (max-height: 500px)');
  var lastY = window.scrollY, ticking = false, lockUntil = 0;
  function setBars(show) {
    if (root.classList.contains('bars-hidden') === !show) return;
    lockUntil = Date.now() + 500;          // hiding the bars moves the page by their height: those scroll events are not the reader's
    root.classList.toggle('bars-hidden', !show);
    document.querySelectorAll('.js-nav-toggle').forEach(function (b) { b.setAttribute('aria-expanded', show ? 'true' : 'false'); });
  }
  function onScroll() {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(function () {
      ticking = false;
      if (!sideways.matches) return;
      var y = window.scrollY, d = y - lastY;
      if (Date.now() < lockUntil) { lastY = y; return; }
      if (y < 24) setBars(true);
      else if (d > 10) setBars(false);
      else if (d < -10) setBars(true);
      if (Math.abs(d) > 10) lastY = y;
    });
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  document.querySelectorAll('.js-nav-toggle').forEach(function (b) {
    b.addEventListener('click', function () { setBars(root.classList.contains('bars-hidden')); });
  });
  function onOrientation() { if (!sideways.matches) root.classList.remove('bars-hidden'); }
  if (sideways.addEventListener) sideways.addEventListener('change', onOrientation); else if (sideways.addListener) sideways.addListener(onOrientation);
})();
