/* Showcase only (DL-180): theme toggle for static/dev/oaken-materials.html.  Uses the app's own storage key. */
(function () {
  var root = document.documentElement, btn = document.getElementById('sc-theme');
  function apply(t) {
    root.setAttribute('data-theme', t);
    btn.textContent = t === 'light' ? 'Switch to dark' : 'Switch to light';
    btn.setAttribute('aria-pressed', t === 'light' ? 'true' : 'false');
    try { localStorage.setItem('oaken-theme', t); } catch (e) {}
  }
  var saved = null;
  try { saved = localStorage.getItem('oaken-theme'); } catch (e) {}
  var q = /[?&]theme=(light|dark)/.exec(location.search);
  apply(q ? q[1] : (saved === 'light' ? 'light' : 'dark'));
  btn.addEventListener('click', function () { apply(root.getAttribute('data-theme') === 'light' ? 'dark' : 'light'); });
})();
