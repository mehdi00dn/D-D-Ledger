// Treasury: add, spend, share, remove, filter and page through entries without reloading the page.
// Every form and link still works without this script (plain post and redirect); the script only asks the server for the
// same thing in the background and swaps the purses and entries in place.
(function () {
  'use strict';
  const live = document.getElementById('treasury-live');
  const status = document.getElementById('treasury-status');
  if (!live || !window.fetch) return;
  let hideTimer = null;

  function say(message) {
    if (!status) return;
    status.textContent = message; status.hidden = false;
    clearTimeout(hideTimer); hideTimer = setTimeout(() => { status.hidden = true; }, 6000);
  }

  async function refresh(search, keepScroll) {
    const y = window.scrollY;
    live.setAttribute('aria-busy', 'true');
    try {
      const res = await fetch(live.dataset.panelsUrl + (search || ''), { headers: { 'X-Requested-With': 'fetch' }, credentials: 'same-origin' });
      if (!res.ok) throw new Error(String(res.status));
      const html = await res.text();
      live.querySelectorAll('dialog[open]').forEach((d) => d.close());
      live.innerHTML = html;
      if (keepScroll) window.scrollTo(0, y);
      return true;
    } catch (err) {
      say('Could not refresh the treasury. Reload the page to see the latest.');
      return false;
    } finally {
      live.removeAttribute('aria-busy');
    }
  }

  function showError(form, text) {
    const box = form.querySelector('.treasury-error');
    if (box) { box.textContent = text; box.hidden = false; } else { say(text); }
  }

  async function send(form, button) {
    if (button) button.disabled = true;
    try {
      const res = await fetch(form.action, { method: 'POST', body: new FormData(form), credentials: 'same-origin',
                                              headers: { 'X-Requested-With': 'fetch', 'Accept': 'application/json' } });
      let data = {};
      try { data = await res.json(); } catch (e) { /* not JSON */ }
      if (!res.ok || data.error) {
        showError(form, data.error || (res.status === 403 ? 'You cannot do that here.' : 'That did not work. Try again.'));
        return null;
      }
      return data;
    } catch (err) {
      showError(form, 'No connection. Try again.');
      return null;
    } finally {
      if (button) button.disabled = false;
    }
  }

  document.addEventListener('submit', async (e) => {
    const form = e.target;
    if (e.defaultPrevented || !(form instanceof HTMLFormElement) || !live.contains(form)) return;
    const isEntry = form.hasAttribute('data-treasury-form'), isDelete = form.hasAttribute('data-treasury-delete');
    if (!isEntry && !isDelete) return;
    e.preventDefault();
    const box = form.querySelector('.treasury-error'); if (box) box.hidden = true;
    const data = await send(form, form.querySelector('button[type=submit]'));
    if (!data) { delete form.dataset.confirmed; return; }
    // A new entry goes to the top of "everyone"; a removal keeps the view you were on.
    const search = isEntry ? '' : window.location.search;
    if (isEntry) history.replaceState(null, '', window.location.pathname);
    await refresh(search, isDelete);
    say(data.message || 'Done.');
  });

  document.addEventListener('click', (e) => {
    const nav = e.target.closest('a[data-treasury-nav]');
    if (nav && live.contains(nav) && !e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0) {
      e.preventDefault();
      const url = new URL(nav.href, window.location.href);
      refresh(url.search).then((ok) => {
        if (!ok) return;
        history.pushState(null, '', url.pathname + url.search);
        const entries = document.getElementById('entries');
        if (entries && url.hash) entries.scrollIntoView({ block: 'start', behavior: 'smooth' });
      });
      return;
    }
    const opener = e.target.closest('[data-open-dialog]');
    if (opener) {
      const dlg = document.getElementById(opener.dataset.openDialog);
      if (!dlg) return;
      const box = dlg.querySelector('.treasury-error'); if (box) box.hidden = true;
      if (opener.hasAttribute('data-holder')) { const sel = dlg.querySelector('[name=character_id]'); if (sel) sel.value = opener.dataset.holder; }
      if (opener.hasAttribute('data-source')) { const sel = dlg.querySelector('[name=source]'); if (sel) sel.value = opener.dataset.source; }
    }
  });

  window.addEventListener('popstate', () => { refresh(window.location.search); });

  // Arriving from a character's page with ?holder=: open the entry form for that purse.
  if (/^\d+$/.test(live.dataset.holder || '')) {
    const dlg = document.getElementById('t-add');
    if (dlg && typeof dlg.showModal === 'function') dlg.showModal();
  }
})();
