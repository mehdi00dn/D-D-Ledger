// DL-177/DL-179: the home page's behaviour -- the phone menu, the nav's scrolled state, and marking the section you are reading.
// Everything here is polish: without this script the menu simply stays open (the CSS only closes it when the "js" class is on
// <html>), so nobody is ever locked out of the links.
(function () {
  var header = document.getElementById('top');
  var toggle = document.querySelector('[data-nav-toggle]');
  var menu = document.querySelector('[data-nav-menu]');
  var narrow = window.matchMedia('(max-width: 820px)');

  // -- phone menu --------------------------------------------------------------------------------------------------------------
  if (toggle && menu) {
    var set = function (open) {
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) menu.setAttribute('data-open', ''); else menu.removeAttribute('data-open');
    };
    var isOpen = function () { return toggle.getAttribute('aria-expanded') === 'true'; };
    toggle.addEventListener('click', function () { set(!isOpen()); });
    menu.addEventListener('click', function (e) { if (narrow.matches && e.target.closest('a')) set(false); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && narrow.matches && isOpen()) { set(false); toggle.focus(); }
    });
    document.addEventListener('click', function (e) {            // a tap outside the bar closes it
      if (narrow.matches && isOpen() && header && !header.contains(e.target)) set(false);
    });
    var sync = function () { set(false); };                      // crossing the breakpoint either way starts from closed
    if (narrow.addEventListener) narrow.addEventListener('change', sync); else narrow.addListener(sync);
    sync();
  }

  // -- the bar firms up once the page has moved under it, and the link for the section you are reading is marked -----------------
  // "Features" covers everything from its anchor down to the next anchored section ("How it works"), which is several <section>s.
  var links = [].slice.call(document.querySelectorAll('.nav-links a[href^="#"]'));
  var anchors = links.map(function (a) { return { link: a, el: document.getElementById(a.getAttribute('href').slice(1)) }; })
                     .filter(function (x) { return x.el; });
  var ticking = false;
  function paint() {
    ticking = false;
    if (header) header.classList.toggle('is-scrolled', window.scrollY > 8);
    var line = window.innerHeight * 0.4, current = null;
    anchors.forEach(function (x, i) {
      var top = x.el.getBoundingClientRect().top;
      var next = anchors[i + 1];
      var end = next ? next.el.getBoundingClientRect().top : x.el.getBoundingClientRect().bottom;
      if (top <= line && end > line) current = x.link;
    });
    anchors.forEach(function (x) { if (x.link === current) x.link.setAttribute('aria-current', 'true'); else x.link.removeAttribute('aria-current'); });
  }
  function queue() { if (!ticking) { ticking = true; requestAnimationFrame(paint); } }
  window.addEventListener('scroll', queue, { passive: true });
  window.addEventListener('resize', queue);
  paint();
})();
