// Encounter builder (DL-147): pick monsters from the manual, compare their XP with the party's 2024 budget, save, run.
(function () {
  'use strict';
  const root = document.querySelector('[data-encounter]');
  if (!root) return;

  const $ = (sel) => root.querySelector(sel);
  const table = JSON.parse(root.dataset.budget);          // level -> [low, moderate, high] XP per character
  const party = JSON.parse(root.dataset.party);
  const countMax = parseInt(root.dataset.countMax, 10) || 30;
  let lines = JSON.parse(root.dataset.lines);             // [{ref, name, cr, xp, count}]
  let dirty = false;

  const saveBtn = $('[data-enc-save]'), runBtn = $('[data-enc-run]'), statusEl = $('[data-enc-status]');
  const nameIn = $('[data-enc-name]'), linesEl = $('[data-enc-lines]'), emptyEl = $('[data-enc-empty]');
  const base = `/campaigns/${window.CAMPAIGN_ID}`;
  const TIERS = ['low', 'moderate', 'high'];
  const TIER_NAMES = { none: 'No monsters', low: 'Low', moderate: 'Moderate', high: 'High', over: 'Over High' };

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  const fmt = (n) => Number(n).toLocaleString('en-US');
  function say(text, isError) { statusEl.textContent = text || ''; statusEl.classList.toggle('is-error', !!isError); }
  function markDirty() { dirty = true; saveBtn.disabled = false; }

  async function call(url, method, body) {
    const r = await fetch(url, {
      method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok) throw new Error((data && data.error) || 'That did not work. Try again.');
    return data;
  }

  // ---- the numbers ----
  function levels() {
    if (party.length) {
      const on = new Set(Array.from(root.querySelectorAll('[data-enc-pc]:checked')).map((c) => parseInt(c.value, 10)));
      return party.filter((p) => on.has(p.id)).map((p) => p.level);
    }
    const n = Math.max(1, Math.min(12, parseInt($('[data-enc-manual-n]').value, 10) || 1));
    const l = Math.max(1, Math.min(20, parseInt($('[data-enc-manual-l]').value, 10) || 1));
    return new Array(n).fill(l);
  }
  function budget() {
    const out = { low: 0, moderate: 0, high: 0 };
    levels().forEach((lv) => {
      const row = table[Math.max(1, Math.min(20, lv || 1))];
      TIERS.forEach((t, i) => { out[t] += row[i]; });
    });
    return out;
  }
  function tierOf(total, b) {
    if (total <= 0) return 'none';
    for (const t of TIERS) if (total <= b[t]) return t;
    return 'over';
  }

  // ---- drawing ----
  function renderLines() {
    linesEl.textContent = '';
    emptyEl.hidden = lines.length > 0;
    lines.forEach((l, i) => {
      const li = el('li', 'enc-line');
      const main = el('div', 'enc-line-main');
      main.appendChild(el('span', 'enc-line-name', l.name));
      main.appendChild(el('span', 'enc-line-meta', `CR ${l.cr} · ${fmt(l.xp)} XP each`));
      const stepper = el('div', 'enc-step');
      const minus = el('button', 'btn btn-ghost btn-sm', '−'); minus.type = 'button'; minus.setAttribute('aria-label', `One fewer ${l.name}`);
      const num = el('span', 'enc-count', String(l.count)); num.setAttribute('aria-live', 'polite');
      const plus = el('button', 'btn btn-ghost btn-sm', '+'); plus.type = 'button'; plus.setAttribute('aria-label', `One more ${l.name}`);
      plus.disabled = l.count >= countMax;
      minus.addEventListener('click', () => { l.count -= 1; if (l.count < 1) lines.splice(i, 1); markDirty(); renderAll(); });
      plus.addEventListener('click', () => { if (l.count < countMax) { l.count += 1; markDirty(); renderAll(); } });
      stepper.append(minus, num, plus);
      const rm = el('button', 'btn btn-ghost btn-sm quest-delete', '✕'); rm.type = 'button'; rm.setAttribute('aria-label', `Remove ${l.name}`);
      rm.addEventListener('click', () => { lines.splice(i, 1); markDirty(); renderAll(); });
      li.append(main, stepper, rm);
      linesEl.appendChild(li);
    });
  }

  function renderSummary() {
    const b = budget();
    const total = lines.reduce((s, l) => s + l.xp * l.count, 0);
    const tier = tierOf(total, b);
    $('[data-enc-creatures]').textContent = String(lines.reduce((s, l) => s + l.count, 0));
    const tierEl = $('[data-enc-tier]');
    tierEl.textContent = TIER_NAMES[tier];
    tierEl.className = `enc-tier enc-tier-${tier}`;
    $('[data-enc-total]').textContent = `${fmt(total)} XP`;
    TIERS.forEach((t) => { $(`[data-enc-b="${t}"]`).textContent = fmt(b[t]); });
    const scale = Math.max(b.high * 1.25, total, 1);
    $('[data-enc-fill]').style.setProperty('--w', `${Math.min(100, (total / scale) * 100)}%`);
    $('[data-enc-fill]').dataset.tier = tier;
    TIERS.forEach((t) => $(`[data-enc-mark="${t}"]`).style.setProperty('--at', `${(b[t] / scale) * 100}%`));
    $('[data-enc-meter]').setAttribute('aria-label', `${fmt(total)} XP. ${TIER_NAMES[tier]} difficulty. Budget: low ${fmt(b.low)}, moderate ${fmt(b.moderate)}, high ${fmt(b.high)}.`);
    runBtn.disabled = lines.length === 0;
  }
  function renderAll() { renderLines(); renderSummary(); }

  // ---- the monster manual ----
  const resultsEl = $('[data-enc-results]'), queryIn = $('[data-enc-query]');
  let searchSeq = 0, timer = null;
  async function search() {
    const seq = ++searchSeq;
    const q = queryIn.value.trim();
    resultsEl.textContent = '';
    if (!q) return;
    try {
      const rows = await call(`${base}/api/reference/search?kind=monsters&q=${encodeURIComponent(q)}`, 'GET');
      if (seq !== searchSeq) return;
      if (!rows.length) { resultsEl.appendChild(el('li', 'hint-text', 'No monster by that name.')); return; }
      rows.forEach((row) => {
        const li = el('li', 'enc-result');
        li.appendChild(el('span', 'enc-result-name', row.name));
        const add = el('button', 'btn btn-ghost btn-sm', 'Add'); add.type = 'button'; add.setAttribute('aria-label', `Add ${row.name}`);
        add.addEventListener('click', () => addMonster(row, add));
        li.appendChild(add);
        resultsEl.appendChild(li);
      });
    } catch (e) { if (seq === searchSeq) resultsEl.appendChild(el('li', 'hint-text is-error', e.message)); }
  }
  async function addMonster(row, btn) {
    const have = lines.find((l) => l.ref === row.id);
    if (have) { if (have.count < countMax) { have.count += 1; markDirty(); renderAll(); } return; }
    btn.disabled = true;
    try {
      const d = await call(`${base}/api/reference/entry?kind=monsters&ref=${encodeURIComponent(row.id)}`, 'GET');
      if (!lines.find((l) => l.ref === row.id)) lines.push({ ref: row.id, name: d.title, cr: d.cr, xp: d.xp, count: 1 });
      else lines.find((l) => l.ref === row.id).count += 1;
      markDirty(); renderAll(); say('');
    } catch (e) { say(e.message, true); }
    btn.disabled = false;
  }

  // ---- saving and running ----
  async function save() {
    say('Saving…');
    const data = await call(root.dataset.saveUrl, 'POST', { name: nameIn.value, monsters: lines.map((l) => ({ ref: l.ref, count: l.count })) });
    lines = data.monsters; nameIn.value = data.name; dirty = false; saveBtn.disabled = true;
    renderAll(); say('Saved.');
  }
  saveBtn.addEventListener('click', () => save().catch((e) => say(e.message, true)));
  runBtn.addEventListener('click', async () => {
    runBtn.disabled = true;
    try {
      await save();
      const r = await call(root.dataset.runUrl, 'POST', { hp: $('[data-enc-hp]').value });
      window.location.href = r.url;
    } catch (e) { say(e.message, true); runBtn.disabled = false; }
  });
  nameIn.addEventListener('input', markDirty);
  queryIn.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search, 250); });
  root.addEventListener('change', (ev) => { if (ev.target.matches('[data-enc-pc], [data-enc-manual-n], [data-enc-manual-l]')) renderSummary(); });
  window.addEventListener('beforeunload', (ev) => { if (dirty) { ev.preventDefault(); ev.returnValue = ''; } });

  renderAll();
})();
