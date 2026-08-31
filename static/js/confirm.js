// Reusable styled confirmation modal. Usage:
//   const ok = await window.confirmAction('Remove this thing?');
//   if (!ok) return;

(function () {
  const overlay = document.getElementById('confirm-modal');
  if (!overlay) return;

  const messageEl = document.getElementById('confirm-modal-message');
  const yesBtn = document.getElementById('confirm-modal-yes');
  const noBtn = document.getElementById('confirm-modal-no');
  let pendingResolve = null;

  function resolveAndClose(result) {
    overlay.hidden = true;
    if (pendingResolve) {
      pendingResolve(result);
      pendingResolve = null;
    }
  }

  yesBtn.addEventListener('click', () => resolveAndClose(true));
  noBtn.addEventListener('click', () => resolveAndClose(false));
  overlay.addEventListener('click', (e) => { if (e.target === overlay) resolveAndClose(false); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !overlay.hidden) resolveAndClose(false);
  });

  window.confirmAction = function (message) {
    messageEl.textContent = message;
    overlay.hidden = false;
    return new Promise((resolve) => { pendingResolve = resolve; });
  };
})();
