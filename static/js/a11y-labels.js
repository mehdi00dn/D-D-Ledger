/* Safety net for icon-only controls: a button or link that shows only an icon and carries a `title` gets that title as
 * its accessible name (aria-label), so screen readers announce "Delete map" instead of "button".  It follows later
 * title changes, and also covers buttons the other scripts build after the page loads.
 */
(function () {
  'use strict';
  function needsName(el) {
    if (el.hasAttribute('aria-label') && !el.hasAttribute('data-auto-label')) return false;
    if (el.hasAttribute('aria-labelledby')) return false;
    if ((el.textContent || '').trim()) return false;
    var img = el.querySelector('img[alt]:not([alt=""])');
    return !img;
  }
  function label(el) {
    var title = el.getAttribute('title');
    if (!title) return;
    if (el.hasAttribute('data-auto-label')) { el.setAttribute('aria-label', title); return; }
    if (needsName(el)) { el.setAttribute('aria-label', title); el.setAttribute('data-auto-label', ''); }
  }
  function sweep(root) {
    if (root.nodeType !== 1) return;
    if (root.matches && root.matches('button[title], a[title]')) label(root);
    var found = root.querySelectorAll ? root.querySelectorAll('button[title], a[title]') : [];
    for (var i = 0; i < found.length; i++) label(found[i]);
  }
  sweep(document.documentElement);
  if (!window.MutationObserver) return;
  var queued = false, pending = [];
  new MutationObserver(function (records) {
    records.forEach(function (r) {
      if (r.type === 'attributes') pending.push(r.target);
      else r.addedNodes.forEach(function (n) { pending.push(n); });
    });
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () {
      queued = false;
      var batch = pending; pending = [];
      batch.forEach(function (n) { if (n.isConnected) sweep(n); });
    });
  }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['title'] });
})();
