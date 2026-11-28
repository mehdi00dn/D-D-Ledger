/* Keyboard and screen reader behaviour shared by every pop-up window built from `.modal-overlay` (the confirm question, the
 * avatar crop, Add to battle, Conditions, the character peek, New map).  Each page script opens and closes its window by
 * toggling the overlay's `hidden` attribute; this file watches that and adds what they had in common missing:
 *   - the panel is announced as a dialog with a name (alertdialog for the confirm question);
 *   - focus moves into the window when it opens (to "No" for the confirm question, so Enter never deletes by accident)
 *     unless the page script already put it somewhere, and goes back to the control that opened it when it closes;
 *   - Tab and Shift+Tab stay inside the topmost open window;
 *   - Escape closes a window that does not handle Escape itself, by pressing its close or cancel button.
 */
(function () {
  'use strict';
  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  var opener = new WeakMap();
  var lastTrigger = null;       // the control last pressed outside a window: the page script may move focus before this file sees the window open
  document.addEventListener('click', function (e) {
    var t = e.target && e.target.closest ? e.target.closest(FOCUSABLE) : null;
    if (t && !t.closest('.modal-overlay')) lastTrigger = t;
  }, true);

  function shown(el) { return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length); }
  function panelOf(overlay) { return overlay.querySelector('.modal-panel') || overlay; }
  function focusables(panel) { return Array.prototype.filter.call(panel.querySelectorAll(FOCUSABLE), shown); }
  function openOverlays() { return Array.prototype.filter.call(document.querySelectorAll('.modal-overlay'), function (o) { return !o.hidden; }); }
  function topmost() { var all = openOverlays(); return all.length ? all[all.length - 1] : null; }

  function nameIt(overlay, panel) {
    panel.setAttribute('role', overlay.id === 'confirm-modal' ? 'alertdialog' : 'dialog');
    panel.setAttribute('aria-modal', 'true');
    if (panel.hasAttribute('aria-label') || panel.hasAttribute('aria-labelledby')) return;
    var heading = overlay.id === 'confirm-modal' ? panel.querySelector('.confirm-modal-message') : panel.querySelector('h1, h2, h3, h4, [id$="-title"]');
    if (!heading) return;
    if (!heading.id) heading.id = overlay.id + '-title';
    panel.setAttribute('aria-labelledby', heading.id);
  }

  function opened(overlay) {
    var panel = panelOf(overlay);
    nameIt(overlay, panel);
    var from = document.activeElement;
    if (!from || from === document.body || overlay.contains(from)) from = lastTrigger;
    if (from) opener.set(overlay, from);
    if (panel.contains(document.activeElement)) return;               // the page script already chose where focus goes
    var target = overlay.id === 'confirm-modal' ? panel.querySelector('#confirm-modal-no') : (panel.querySelector('[autofocus]') || focusables(panel)[0]);
    if (!target) { panel.setAttribute('tabindex', '-1'); target = panel; }
    target.focus();
  }

  function closed(overlay) {
    var back = opener.get(overlay);
    opener.delete(overlay);
    if (back && back.isConnected && shown(back)) back.focus();
  }

  function watch(overlay) {
    if (overlay.__modalWatched) return;
    overlay.__modalWatched = true;
    nameIt(overlay, panelOf(overlay));
    new MutationObserver(function () { if (overlay.hidden) closed(overlay); else opened(overlay); })
      .observe(overlay, { attributes: true, attributeFilter: ['hidden'] });
  }
  Array.prototype.forEach.call(document.querySelectorAll('.modal-overlay'), watch);

  document.addEventListener('keydown', function (e) {
    var overlay = topmost();
    if (!overlay) return;
    var panel = panelOf(overlay);
    if (e.key === 'Tab') {
      var items = focusables(panel);
      if (!items.length) { e.preventDefault(); return; }
      var first = items[0], last = items[items.length - 1], on = document.activeElement;
      if (!panel.contains(on)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && on === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && on === last) { e.preventDefault(); first.focus(); }
      return;
    }
    if (e.key === 'Escape' && !e.defaultPrevented && overlay.id !== 'confirm-modal') {
      var close = panel.querySelector('[id^="close"], [aria-label^="Close"], [aria-label^="Cancel"]') || panel.querySelector('.form-actions .btn-ghost');
      if (close) { e.preventDefault(); close.click(); }
    }
  });
})();
