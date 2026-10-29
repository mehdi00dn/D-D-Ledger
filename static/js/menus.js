// Small shared pieces for the character pages: the "more" dropdown menus and the pick-from-your-characters dialog.
(function () {
  'use strict';

  // ---- dropdown menus (<details class="menu">) close on an outside click, Escape, or choosing an item ----
  const menus = () => Array.from(document.querySelectorAll('details.menu[open]'));
  document.addEventListener('click', (ev) => {
    menus().forEach((m) => { if (!m.contains(ev.target) || ev.target.closest('a.menu-item, button.menu-item')) m.open = false; });
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    menus().forEach((m) => { m.open = false; const s = m.querySelector('summary'); if (s) s.focus(); });
  });

  // ---- dialogs opened by a button: <button data-open-dialog="id"> ----
  document.addEventListener('click', (ev) => {
    const opener = ev.target.closest('[data-open-dialog]');
    if (opener) {
      const dlg = document.getElementById(opener.dataset.openDialog);
      if (dlg && typeof dlg.showModal === 'function') { ev.preventDefault(); dlg.showModal(); const f = dlg.querySelector('[data-pick-search], input[type=checkbox], a, button'); if (f) f.focus(); }
    }
    const closer = ev.target.closest('[data-close-dialog]');
    if (closer) { const dlg = closer.closest('dialog'); if (dlg) dlg.close(); }
    if (ev.target.matches('dialog.pick-dialog')) ev.target.close();          // a click on the backdrop
  });

  // ---- the picker: filter, select all, and a button that says how many will be added ----
  document.querySelectorAll('[data-pick]').forEach((root) => {
    const rows = Array.from(root.querySelectorAll('[data-pick-row]'));
    const boxes = rows.map((r) => r.querySelector('input[type=checkbox]'));
    const submit = root.querySelector('[data-pick-submit]');
    const count = root.querySelector('[data-pick-count]');
    const search = root.querySelector('[data-pick-search]');
    const all = root.querySelector('[data-pick-all]');
    const none = root.querySelector('[data-pick-none]');
    const visible = () => rows.filter((r) => !r.hidden);
    function sync() {
      const n = boxes.filter((b) => b.checked).length;
      if (submit) submit.disabled = n === 0;
      if (count) count.textContent = n ? ' ' + n : '';
      rows.forEach((r, i) => r.classList.toggle('is-picked', boxes[i].checked));
    }
    root.addEventListener('change', sync);
    if (search) search.addEventListener('input', () => {
      const q = search.value.trim().toLowerCase();
      rows.forEach((r) => { r.hidden = !!q && !r.dataset.name.includes(q); });
    });
    if (all) all.addEventListener('click', () => { visible().forEach((r) => { r.querySelector('input').checked = true; }); sync(); });
    if (none) none.addEventListener('click', () => { boxes.forEach((b) => { b.checked = false; }); sync(); });
    sync();
  });
})();
