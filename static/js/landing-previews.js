// DL-186: the landing page previews.  Small working pieces of the product that fill the frames on the home page.  Demo data only: every control
// changes state in this page and nothing is saved, sent or read.  Without this script a preview still shows its first state (the markup carries it);
// the script only makes it respond.  Pieces are found by data-lp="name" and set up one at a time, so one that is missing from the page, or one that
// breaks, never stops the rest.
(function () {
  'use strict';

  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  var pieces = {};

  // -- 01: the campaign overview.  Pick a hero and their line in the campaign notes lights up; tick the quest off. ----------------------------------
  pieces.campaign = function (root) {
    var tiles = $$('[data-pc]', root);
    var list = root.querySelector('[data-notes]');
    var lines = $$('[data-note-for]', root);

    function pick(tile) {
      var on = tile.getAttribute('aria-pressed') !== 'true';           // a second press puts it back
      tiles.forEach(function (t) { t.setAttribute('aria-pressed', 'false'); });
      tile.setAttribute('aria-pressed', on ? 'true' : 'false');
      if (!list) return;
      if (on) list.setAttribute('data-lit', ''); else list.removeAttribute('data-lit');
      lines.forEach(function (li) {
        var lit = on && li.getAttribute('data-note-for') === tile.getAttribute('data-pc');
        li.classList.toggle('is-lit', lit);
        if (lit) li.setAttribute('aria-current', 'true'); else li.removeAttribute('aria-current');
      });
    }
    tiles.forEach(function (t) { t.addEventListener('click', function () { pick(t); }); });
  };

  $$('[data-lp]').forEach(function (root) {
    var make = pieces[root.getAttribute('data-lp')];
    if (!make) return;
    try { make(root); } catch (err) { if (window.console) console.error('landing preview:', err); }
  });
})();
