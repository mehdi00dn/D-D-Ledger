// DL-177: the home page's phone menu.  Without this script the menu simply stays open (the CSS only hides it when the
// "js" class is on <html>), so nobody is ever locked out of the links.
(function () {
  var toggle = document.querySelector('[data-nav-toggle]');
  var menu = document.querySelector('[data-nav-menu]');
  if (!toggle || !menu) return;
  var narrow = window.matchMedia('(max-width: 820px)');

  function set(open) {
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    menu.hidden = !open;
  }
  function sync() { set(!narrow.matches); }       // wide screen: always shown; phone: closed until opened

  toggle.addEventListener('click', function () { set(toggle.getAttribute('aria-expanded') !== 'true'); });
  menu.addEventListener('click', function (e) { if (narrow.matches && e.target.closest('a')) set(false); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && narrow.matches && toggle.getAttribute('aria-expanded') === 'true') { set(false); toggle.focus(); }
  });
  if (narrow.addEventListener) narrow.addEventListener('change', sync); else narrow.addListener(sync);
  sync();
})();
