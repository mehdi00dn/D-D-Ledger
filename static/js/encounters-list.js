// Encounters list (DL-147 follow-up): rename an encounter in place and run it in the battle without opening it.
(function () {
  'use strict';
  const list = document.querySelector('[data-enc-list]');
  if (!list) return;
  const statusEl = document.querySelector('[data-enc-list-status]');
  function say(text, isError) { statusEl.textContent = text || ''; statusEl.classList.toggle('is-error', !!isError); }

  async function post(url, body) {
    const r = await fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try { data = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok) throw new Error((data && data.error) || 'That did not work. Try again.');
    return data;
  }

  async function rename(input) {
    const name = input.value.replace(/\s+/g, ' ').trim();
    if (name === input.dataset.saved) { input.value = name; return; }
    if (!name) { input.value = input.dataset.saved; say('An encounter needs a name.', true); return; }
    try {
      const data = await post(input.dataset.url, { name });
      input.value = input.dataset.saved = data.name;
      say('Renamed to ' + data.name + '.');
    } catch (e) { input.value = input.dataset.saved; say(e.message, true); }
  }

  list.addEventListener('change', (ev) => { if (ev.target.matches('[data-enc-rename]')) rename(ev.target); });
  list.addEventListener('keydown', (ev) => {
    if (!ev.target.matches('[data-enc-rename]')) return;
    if (ev.key === 'Enter') { ev.preventDefault(); ev.target.blur(); }
    if (ev.key === 'Escape') { ev.target.value = ev.target.dataset.saved; ev.target.blur(); }
  });
  // Deleting asks whether the monsters should also leave the character list.
  const dialog = document.querySelector('[data-enc-dialog]');
  let pending = null;
  function finish(remove) {
    const form = pending; pending = null;
    if (dialog.open) dialog.close();
    if (!form) return;
    form.querySelector('[name=remove_characters]').value = remove ? '1' : '0';
    form.submit();
  }
  list.addEventListener('submit', (ev) => {
    const form = ev.target.closest('[data-enc-delete]');
    if (!form || !dialog || typeof dialog.showModal !== 'function') return;
    ev.preventDefault();
    pending = form;
    dialog.querySelector('[data-enc-dialog-title]').textContent = 'Delete \u201c' + form.dataset.name + '\u201d?';
    dialog.showModal();
  });
  if (dialog) {
    dialog.querySelector('[data-enc-dialog-cancel]').addEventListener('click', () => { pending = null; dialog.close(); });
    dialog.querySelector('[data-enc-dialog-keep]').addEventListener('click', () => finish(false));
    dialog.querySelector('[data-enc-dialog-remove]').addEventListener('click', () => finish(true));
    dialog.addEventListener('cancel', () => { pending = null; });
  }
  list.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-enc-run]');
    if (!btn) return;
    const hp = btn.closest('[data-enc-card]').querySelector('[data-enc-hp]').value;
    btn.disabled = true; say('Sending the monsters to the battle…');
    try {
      const r = await post(btn.dataset.url, { hp });
      window.location.href = r.url;
    } catch (e) { say(e.message, true); btn.disabled = false; }
  });
})();
