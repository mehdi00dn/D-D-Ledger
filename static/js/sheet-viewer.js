/* Character details page: the thumbnails in the card pick which sheet the big viewer on the right shows.
   Clicking the viewer opens the shared lightbox on the same sheet, with every sheet one arrow away. */
(function () {
  'use strict';
  var panel = document.querySelector('[data-sheet-viewer]');
  var img = document.getElementById('sheet-viewer-img');
  var thumbs = Array.prototype.slice.call(document.querySelectorAll('[data-sheet-thumbs] img[data-sheet-src]'));
  if (!panel || !img || !thumbs.length) return;
  var count = panel.querySelector('[data-sheet-count]');
  var prev = panel.querySelector('[data-sheet-prev]');
  var next = panel.querySelector('[data-sheet-next]');
  var index = 0;

  // The same page turn as the lightbox (220 ms, ease-out, interruptible; reduced motion = a short fade), hinged on the side it came from.
  var turn = null;
  var calm = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  function pageTurn(direction) {
    if (turn) { turn.cancel(); turn = null; }
    if (!img.animate || !direction) return;
    var forward = direction > 0, origin = forward ? 'left center' : 'right center';
    var from = calm.matches ? { opacity: 0.4 } : { opacity: 0.35, transform: 'perspective(1100px) rotateY(' + (forward ? -24 : 24) + 'deg) scale(0.985)', transformOrigin: origin };
    var to = calm.matches ? { opacity: 1 } : { opacity: 1, transform: 'perspective(1100px) rotateY(0deg) scale(1)', transformOrigin: origin };
    turn = img.animate([from, to], { duration: calm.matches ? 120 : 220, easing: 'cubic-bezier(0.32, 0.72, 0, 1)' });
    turn.onfinish = turn.oncancel = function () { turn = null; };
  }

  function show(i) {
    var before = index;
    index = Math.max(0, Math.min(thumbs.length - 1, i));
    var changed = index !== before;
    img.src = thumbs[index].dataset.sheetSrc;
    if (changed) pageTurn(index > before ? 1 : -1);
    thumbs.forEach(function (t, n) { t.parentNode.classList.toggle('is-active', n === index); });
    img.alt = 'Character sheet ' + (index + 1) + ' of ' + thumbs.length;
    if (count) count.textContent = (index + 1) + ' / ' + thumbs.length;
    if (prev) prev.disabled = index === 0;
    if (next) next.disabled = index === thumbs.length - 1;
  }

  thumbs.forEach(function (t, n) {
    t.addEventListener('click', function () { show(n); });
    t.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); show(n); }
    });
  });
  if (prev) prev.addEventListener('click', function () { show(index - 1); });
  if (next) next.addEventListener('click', function () { show(index + 1); });

  img.addEventListener('click', function () {
    if (!window.openLightbox) return;
    var items = thumbs.map(function (t) { return { src: t.dataset.sheetSrc, deleteUrl: t.dataset.viewerDeleteUrl || null }; });
    window.openLightbox(items[index].src, items[index].deleteUrl, items);
  });
  show(0);
})();
