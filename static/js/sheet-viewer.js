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

  function show(i) {
    index = Math.max(0, Math.min(thumbs.length - 1, i));
    img.src = thumbs[index].dataset.sheetSrc;
    thumbs.forEach(function (t, n) { t.parentNode.classList.toggle('is-active', n === index); });
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
