// Collapsible left sidebar. The initial collapsed/expanded state is already
// applied by an inline script in <head> (to avoid a flash of the wrong
// state); this file just wires up the toggle button and persists the choice.

(function () {
  const toggleBtn = document.getElementById('sidebar-toggle-btn');
  if (!toggleBtn) return;

  function isCollapsed() {
    return document.documentElement.getAttribute('data-sidebar') === 'collapsed';
  }

  function setCollapsed(collapsed) {
    if (collapsed) {
      document.documentElement.setAttribute('data-sidebar', 'collapsed');
    } else {
      document.documentElement.removeAttribute('data-sidebar');
    }
    localStorage.setItem('ledger-sidebar-collapsed', collapsed ? '1' : '0');
    toggleBtn.title = collapsed ? 'Expand menu' : 'Collapse menu';
  }

  // Sync the button's title with whatever state the pre-paint script applied.
  toggleBtn.title = isCollapsed() ? 'Expand menu' : 'Collapse menu';

  toggleBtn.addEventListener('click', () => {
    setCollapsed(!isCollapsed());
  });
})();
