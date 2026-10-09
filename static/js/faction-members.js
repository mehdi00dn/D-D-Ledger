/* Faction edit form: search the campaign's characters and queue them to join this faction.
   Picks are sent as repeated `add_character_ids` fields when the form is saved. */
(function initFactionMembers() {
  const root = document.getElementById('member-picker');
  if (!root) return;
  root.dataset.ready = '1';
  const input = document.getElementById('member-search');
  const results = document.getElementById('member-results');
  const chips = document.getElementById('member-chips');
  const hint = document.getElementById('member-hint');
  const form = root.closest('form');
  const groupId = parseInt(root.dataset.groupId, 10);
  let candidates = [];
  try { candidates = JSON.parse(document.getElementById('member-candidates').textContent) || []; } catch (e) { /* none */ }
  const picked = new Map(); // id -> candidate
  const MAX_SHOWN = 8;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function renderChips() {
    chips.textContent = '';
    picked.forEach((c) => {
      const chip = el('span', 'member-chip');
      chip.appendChild(el('span', null, c.name));
      const x = el('button', 'member-chip-remove', '×');
      x.type = 'button';
      x.title = 'Undo';
      x.addEventListener('click', () => { picked.delete(c.id); renderChips(); renderResults(); });
      chip.appendChild(x);
      const hid = document.createElement('input');
      hid.type = 'hidden'; hid.name = 'add_character_ids'; hid.value = String(c.id);
      chip.appendChild(hid);
      chips.appendChild(chip);
    });
    hint.hidden = picked.size === 0;
  }

  function renderResults() {
    const q = input.value.trim().toLowerCase();
    results.textContent = '';
    if (!q) { results.hidden = true; return; }
    const matches = candidates.filter((c) => c.name.toLowerCase().includes(q));
    if (!matches.length) {
      results.appendChild(el('div', 'member-result-empty', 'No characters match.'));
      results.hidden = false;
      return;
    }
    matches.slice(0, MAX_SHOWN).forEach((c) => {
      const row = el('div', 'member-result');
      const nm = el('span', 'member-result-name');
      if (window.classIconHtml) nm.innerHTML = window.classIconHtml(c.class_key);
      nm.appendChild(document.createTextNode(c.name));
      row.appendChild(nm);
      row.appendChild(el('span', 'dossier-tag' + (c.is_npc ? ' npc' : ''), c.is_npc ? 'NPC' : 'PC'));
      const inThis = (c.group_ids || []).includes(groupId);
      row.appendChild(el('span', 'member-result-meta',
        inThis ? 'Already in this faction' : ((c.group_names || []).length ? 'Also in ' + c.group_names.join(', ') : 'No faction')));
      const btn = el('button', 'btn btn-ghost btn-sm');
      btn.type = 'button';
      if (inThis) { btn.textContent = 'Member'; btn.disabled = true; }
      else if (picked.has(c.id)) { btn.textContent = 'Added'; btn.disabled = true; }
      else {
        btn.textContent = 'Add';
        btn.addEventListener('click', () => { picked.set(c.id, c); renderChips(); renderResults(); input.focus(); });
      }
      row.appendChild(btn);
      results.appendChild(row);
    });
    if (matches.length > MAX_SHOWN) results.appendChild(el('div', 'member-result-empty', (matches.length - MAX_SHOWN) + ' more — keep typing to narrow it down.'));
    results.hidden = false;
  }

  input.addEventListener('input', renderResults);
  // Enter in the search box must not submit the whole faction form.
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') e.preventDefault(); });
  if (form) form.addEventListener('reset', () => { picked.clear(); renderChips(); renderResults(); });
}());
