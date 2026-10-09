// Rules reference page (spells / monsters / items) and the monster search inside "New character".
(function () {
  'use strict';

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function debounce(fn, ms) {
    let t;
    return function () { clearTimeout(t); const a = arguments; t = setTimeout(() => fn.apply(null, a), ms); };
  }

  async function getJson(url) {
    const r = await fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    let data = null;
    try { data = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok) throw new Error((data && data.error) || 'The rules reference is unavailable right now.');
    return data;
  }

  // A result list bound to one kind. onPick(entry) is called with the clicked {id, name, sub}.
  function bindSearch(opts) {
    const { base, input, list, onPick } = opts;
    let kind = opts.kind;
    let seq = 0;

    async function run() {
      const mine = ++seq;
      list.replaceChildren(el('li', 'ref-empty', 'Loading…'));
      try {
        const rows = await getJson(`${base}search?kind=${kind}&q=${encodeURIComponent(input.value.trim())}`);
        if (mine !== seq) return;
        list.replaceChildren();
        if (!rows.length) list.append(el('li', 'ref-empty', 'No matches.'));
        rows.forEach((row) => {
          const li = el('li');
          const b = el('button', 'ref-item');
          b.type = 'button';
          b.dataset.ref = row.id;
          b.append(el('span', 'ref-item-name', row.name));
          if (row.sub) b.append(el('span', 'ref-item-sub', row.sub));
          b.addEventListener('click', () => onPick(row, b));
          li.append(b);
          list.append(li);
        });
      } catch (e) {
        if (mine === seq) list.replaceChildren(el('li', 'ref-empty ref-error', e.message));
      }
    }

    input.addEventListener('input', debounce(run, 180));
    return { setKind(k) { kind = k; run(); }, run };
  }

  function renderDetail(box, d) {
    box.replaceChildren();
    box.append(el('h2', 'ref-title', d.title));
    if (d.subtitle) box.append(el('p', 'ref-subtitle', d.subtitle));
    if (d.facts && d.facts.length) {
      const dl = el('dl', 'ref-facts');
      d.facts.forEach(([k, v]) => { dl.append(el('dt', null, k), el('dd', null, v)); });
      box.append(dl);
    }
    (d.body || '').split(/\n\n+/).filter(Boolean).forEach((p) => box.append(el('p', 'ref-text', p)));
    // Monster notes are server-built html limited to b/ul/li/br and fully escaped; show them as plain structure.
    (d.notes || []).forEach((html) => {
      const div = el('div', 'ref-notes');
      const tpl = document.createElement('template');
      tpl.innerHTML = html;
      tpl.content.querySelectorAll('*').forEach((n) => {
        if (!['B', 'UL', 'LI', 'BR'].includes(n.tagName)) n.replaceWith(document.createTextNode(n.textContent));
      });
      div.append(tpl.content);
      box.append(div);
    });
  }

  // ---- the Reference page ----
  const page = document.querySelector('[data-reference]');
  if (page) {
    const detail = page.querySelector('[data-ref-detail]');
    const tabs = page.querySelectorAll('.ref-tab');
    const input = page.querySelector('[data-ref-query]');
    let kind = 'spells';
    const search = bindSearch({
      base: page.dataset.base, kind, input, list: page.querySelector('[data-ref-list]'),
      onPick: async (row, btn) => {
        page.querySelectorAll('.ref-item.is-active').forEach((n) => n.classList.remove('is-active'));
        btn.classList.add('is-active');
        detail.replaceChildren(el('p', 'hint-text', 'Loading…'));
        try { renderDetail(detail, await getJson(`${page.dataset.base}entry?kind=${kind}&ref=${encodeURIComponent(row.id)}`)); }
        catch (e) { detail.replaceChildren(el('p', 'hint-text ref-error', e.message)); }
      },
    });
    tabs.forEach((t) => t.addEventListener('click', () => {
      tabs.forEach((x) => x.classList.toggle('is-active', x === t));
      kind = t.dataset.kind;
      input.value = '';
      detail.replaceChildren(el('p', 'hint-text', 'Pick an entry to read it.'));
      search.setKind(kind);
    }));
    search.run();
  }

  // ---- "New character": search the monster manual and fill the form from a monster ----
  const picker = document.querySelector('[data-monster-picker]');
  if (picker) {
    const form = picker.closest('form');
    const status = picker.querySelector('[data-monster-status]');

    function setValue(name, value) {
      const f = form.elements[name];
      if (!f) return;
      f.value = value;
      f.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function fill(c, notes, name) {
      ['name', 'level', 'max_hp', 'armor_class', 'speed', 'str_score', 'dex_score', 'con_score', 'int_score', 'wis_score', 'cha_score']
        .forEach((k) => setValue(k, c[k]));
      const npc = form.elements.is_npc;
      if (npc) { npc.checked = true; npc.dispatchEvent(new Event('change', { bubbles: true })); }
      form.querySelectorAll('input[data-save]').forEach((cb) => {
        cb.checked = c.save_prof.includes(cb.dataset.save);
        cb.dispatchEvent(new Event('change', { bubbles: true }));
      });
      form.querySelectorAll('input[type=radio][data-skill]').forEach((r) => {
        const want = String(c.skill_prof[r.dataset.skill] || '');
        if (r.value === want) { r.checked = true; r.dispatchEvent(new Event('change', { bubbles: true })); }
      });
      // Replace the note boxes with the stat block: reuse the existing boxes, add more as needed.
      const blocks = document.getElementById('note-blocks');
      const add = document.getElementById('add-note-btn');
      while (blocks.querySelectorAll('[data-note-block]').length < notes.length) add.click();
      const boxes = blocks.querySelectorAll('[data-note-block]');
      boxes.forEach((b, i) => {
        b.querySelector('.note-editor').innerHTML = notes[i] || '';
        if (i >= notes.length && boxes.length > 1) b.remove();
      });
      status.textContent = `Filled in from ${name}. Adjust anything before saving.`;
      status.classList.remove('ref-error');
    }

    const monsterSearch = bindSearch({
      base: picker.dataset.base, kind: 'monsters',
      input: picker.querySelector('[data-ref-query]'), list: picker.querySelector('[data-ref-list]'),
      onPick: async (row) => {
        status.textContent = 'Loading…';
        try {
          const d = await getJson(`${picker.dataset.base}entry?kind=monsters&ref=${encodeURIComponent(row.id)}`);
          fill(d.prefill, d.notes, d.title);
        } catch (e) { status.textContent = e.message; status.classList.add('ref-error'); }
      },
    });
    picker.querySelector('[data-ref-query]').addEventListener('focus', () => {
      picker.querySelector('[data-ref-list]').hidden = false;
      monsterSearch.run();
    }, { once: true });
  }
}());
