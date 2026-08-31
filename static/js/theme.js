// Light/dark theme toggle. The initial theme is already applied by an
// inline script in <head> (to avoid a flash of the wrong theme); this file
// just wires up the toggle button and persists the choice.

(function () {
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;

  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  }

  function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('ledger-theme', theme);
  }

  btn.addEventListener('click', () => {
    // Brief pulse so the click always feels acknowledged, even though the
    // real transition is driven by the CSS custom-property animation.
    btn.classList.add('theme-toggle-pulse');
    setTimeout(() => btn.classList.remove('theme-toggle-pulse'), 400);
    setTheme(currentTheme() === 'light' ? 'dark' : 'light');
  });
})();
