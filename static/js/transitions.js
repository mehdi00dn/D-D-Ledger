// Smooth page-to-page transition when switching between menus in the left
// sidebar. The incoming page fades/slides in on its own (see the
// content-fade-in animation in style.css); this just adds a matching brief
// fade-out on the way out, so the switch reads as one continuous motion
// instead of an abrupt reload.

(function () {
  const FADE_OUT_MS = 150;

  document.querySelectorAll('.sidebar-nav .nav-tab').forEach((tab) => {
    tab.addEventListener('click', (e) => {
      if (tab.classList.contains('active') || tab.classList.contains('disabled')) return;
      // Let modifier-clicks, middle-clicks, and target=_blank behave normally
      // (open in new tab, etc.) instead of hijacking the navigation.
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      if (tab.target === '_blank') return;

      const href = tab.getAttribute('href');
      if (!href) return;

      e.preventDefault();
      document.body.classList.add('page-leaving');
      setTimeout(() => { window.location.href = href; }, FADE_OUT_MS);
    });
  });
})();
