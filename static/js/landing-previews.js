// DL-186: the landing page previews.  Small working scenes from the product that fill the frames on the home page.  Demo data only: every control
// changes state in this page and nothing is saved, sent or read.  Without this script a preview still shows its first state (the markup carries it);
// the script only makes it respond.  Pieces are found by data-lp="name" and set up one at a time, so one that is missing from the page, or one that
// breaks, never stops the rest.
(function () {
  'use strict';

  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  // The entrance: a frame that starts below the fold is armed (.lp-reveal hides its pieces) and then released (.is-in) the first time it is in view.
  // A frame that is already in view when the page loads is left alone, so nothing flashes and nothing can stay hidden.
  function reveal(root) {
    if (!('IntersectionObserver' in window)) return;
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    var armed = false;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { root.classList.add('is-in'); io.disconnect(); }
        else if (!armed) { armed = true; root.classList.add('lp-reveal'); }
      });
    }, { threshold: 0.15 });
    io.observe(root);
  }

  // A tick on the meter: --p is the fill, 0 to 1.
  function paintMeter(meter, done, total) {
    if (meter) meter.style.setProperty('--p', total ? (done / total).toFixed(3) : '0');
  }

  var pieces = {};

  // -- 01: the campaign dashboard.  The portraits are a radio group: pick a hero and the card shows them.  The quests tick off and the count and the rule follow. --
  pieces.campaign = function (root) {
    var radios = $$('[role="radio"]', root);
    var cards = $$('[data-hero-card]', root);

    function pick(radio, focus) {
      radios.forEach(function (r) {
        var on = r === radio;
        r.setAttribute('aria-checked', on ? 'true' : 'false');
        r.tabIndex = on ? 0 : -1;
      });
      cards.forEach(function (c) {
        c.classList.toggle('is-off', c.id !== radio.getAttribute('aria-controls'));   // a CSS transition does the rest, so it can be interrupted
      });
      if (focus) radio.focus();
    }
    radios.forEach(function (r, i) {
      r.addEventListener('click', function () { pick(r); });
      r.addEventListener('keydown', function (e) {
        var j = -1;
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') j = (i + 1) % radios.length;
        else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') j = (i - 1 + radios.length) % radios.length;
        else if (e.key === 'Home') j = 0;
        else if (e.key === 'End') j = radios.length - 1;
        if (j >= 0) { e.preventDefault(); pick(radios[j], true); }
      });
    });

    var quests = $$('.lp-quests', root)[0];
    var boxes = $$('.lp-step input[type="checkbox"]', root);
    var count = $$('[data-steps]', root)[0];
    var meter = $$('.lp-rule-fill', root)[0];
    function tally() {
      var done = boxes.filter(function (b) { return b.checked; }).length;
      if (count) count.textContent = done + ' / ' + boxes.length;
      paintMeter(meter, done, boxes.length);
      if (quests) quests.classList.toggle('is-complete', done === boxes.length && boxes.length > 0);
    }
    boxes.forEach(function (b) { b.addEventListener('change', tally); });
    tally();
  };

  $$('[data-lp]').forEach(function (root) {
    var make = pieces[root.getAttribute('data-lp')];
    if (!make) return;
    reveal(root);
    try { make(root); } catch (err) { if (window.console) console.error('landing preview:', err); }
  });
})();
