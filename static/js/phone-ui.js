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
