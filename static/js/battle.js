// Battle screen: fully client-rendered from JSON state, single source of truth.

(function () {
  const root = document.getElementById('battle-root');
  const allCharacters = JSON.parse(document.getElementById('characters-data').textContent || '[]');
  const isDM = !!window.IS_DM;
  let participants = [];
  // Players can't sort by initiative -- they can't see the numbers anyway,
  // and the DM-only toggle for it isn't even rendered for them.
  let sortMode = isDM ? 'initiative' : 'group';

  const emptyTemplate = document.getElementById('empty-battle-template');

  function hpPercent(p) {
    if (!p.char_max_hp) return 0;
    return Math.max(0, Math.min(100, Math.round((p.current_hp / p.char_max_hp) * 100)));
  }

  function hpColorClass(pct) {
    if (pct <= 25) return 'hp-critical';
    if (pct <= 60) return 'hp-wounded';
    return 'hp-healthy';
  }

  function avatarMarkup(p) {
    if (p.is_temp_familiar) {
      return `<img src="/static/icons/pins/${p.familiar_icon_key || 'paw'}.svg" alt="${escapeHtml(p.char_name)}" class="familiar-avatar-icon">`;
    }
    if (p.avatar_path) {
      return `<img src="/uploads/${p.avatar_path}" alt="${escapeHtml(p.char_name)}">`;
    }
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="3.5"/><path d="M4.5 20c1.2-4 4-6 7.5-6s6.3 2 7.5 6"/></svg>`;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }

  function rowMarkup(p) {
    const hidden = !!p.hidden_stats; // enemy stats redacted server-side for Players
    const pct = hidden ? 0 : hpPercent(p);
    const dead = !!p.is_dead;
    const showDiedBtn = isDM && !hidden && p.current_hp <= 0 && !dead;
    const groupDot = p.group_color ? `<span class="group-dot" style="background:${p.group_color}"></span>` : '';
    const tempHp = p.temp_hp || 0;
    const tempPct = p.char_max_hp ? (tempHp / p.char_max_hp) * 100 : 0;
    const tempBarWidth = Math.max(0, Math.min(100 - pct, tempPct));
    // A Member can view their own party's dossier but not an enemy's.
    const canViewDetails = isDM || !p.is_npc;

    // A DM-concealed NPC gets one compact badge in place of the INIT/AC/HP
    // controls -- not a trio of individually-disabled-looking fields. It
    // sits in the same part of the row so the layout doesn't jump when the
    // DM later reveals the creature. Downed/dead still comes through even
    // while hidden -- a Player should see an enemy drop without learning
    // its exact numbers.
    const downed = !!p.is_downed;
    let statsArea;
    if (hidden && dead) {
      statsArea = `
      <div class="stats-hidden-badge stats-hidden-badge-dead" title="This creature has died">
        ${ICONS.skull}<span>Dead</span>
      </div>`;
    } else if (hidden && downed) {
      statsArea = `
      <div class="stats-hidden-badge stats-hidden-badge-downed" title="This creature is down">
        ${ICONS.skull}<span>Downed</span>
      </div>`;
    } else if (hidden) {
      statsArea = `
      <div class="stats-hidden-badge" title="The DM hasn't revealed this creature's stats">
        ${ICONS.lock}<span>Stats Hidden</span>
      </div>`;
    } else {
      statsArea = `
      <div class="stat-chip">
        <label>INIT</label>
        <input type="number" class="stat-chip-input" data-action="initiative" value="${p.initiative}" ${dead || !isDM ? 'disabled' : ''}>
      </div>
      <div class="stat-chip">
        <label>AC</label>
        <input type="number" class="stat-chip-input" data-action="ac" value="${p.effective_ac}" ${dead || !isDM ? 'disabled' : ''}>
      </div>
      <div class="hp-block">
        <div class="hp-bar-track${isDM ? '' : ' hp-bar-track-static'}" ${isDM ? `data-hp-scrub="${p.id}"` : ''} data-max="${p.char_max_hp}" title="${isDM ? 'Hold and drag to set HP' : ''}">
          <div class="hp-bar-fill ${hpColorClass(pct)}" style="width:${pct}%"></div>
          ${tempBarWidth > 0 ? `<div class="hp-bar-temp" style="left:${pct}%; width:${tempBarWidth}%;"></div>` : ''}
        </div>
        <span class="hp-readout">${p.current_hp} / ${p.char_max_hp}${tempHp > 0 ? ` <span class="temp-hp-badge">+${tempHp}</span>` : ''}</span>
      </div>`;
    }

    const hpControls = (isDM && !hidden) ? `
      <div class="hp-controls">
        <input type="number" class="hp-amount-input" min="0" placeholder="1" value="" ${dead ? 'disabled' : ''}>
        <button type="button" class="hp-btn heal-btn" data-action="heal" title="Heal" ${dead ? 'disabled' : ''}>${ICONS.heart}</button>
        <button type="button" class="hp-btn damage-btn" data-action="damage" title="Damage" ${dead ? 'disabled' : ''}>${ICONS.swords}</button>
        <button type="button" class="hp-btn temphp-btn" data-action="temphp" title="Temporary HP" ${dead ? 'disabled' : ''}>${ICONS.shieldPlus}</button>
      </div>` : '';

    let detailControl = '';
    if (p.is_temp_familiar) {
      if (isDM) {
        detailControl = `<div class="stat-chip"><label>MAX HP</label><input type="number" class="stat-chip-input" data-action="max-hp" min="1" value="${p.char_max_hp}" ${dead ? 'disabled' : ''}></div>`;
      }
    } else if (canViewDetails) {
      detailControl = `<button type="button" class="btn-icon" data-action="view" title="View details">${ICONS.images}</button>`;
    }

    return `
    <div class="battle-row ${dead ? 'is-dead' : ''}" data-pid="${p.id}">
      <div class="battle-avatar" style="border-color:${p.group_color || 'var(--brass)'};">${avatarMarkup(p)}</div>

      <div class="battle-name-block">
        <div class="battle-name">${escapeHtml(p.display_name)}</div>
        <div class="battle-meta">${groupDot}${p.group_name ? escapeHtml(p.group_name) : 'Ungrouped'} &middot; ${p.is_npc ? 'NPC' : 'PC'}</div>
      </div>

      ${statsArea}
      ${hpControls}
      ${detailControl}

      ${showDiedBtn ? `<button type="button" class="btn btn-danger btn-sm" data-action="die">${ICONS.skull} Died?</button>` : ''}
      ${isDM && dead ? `<button type="button" class="btn btn-ghost btn-sm" data-action="revive">Revive</button>` : ''}

      ${isDM ? `<button type="button" class="btn-icon battle-remove" data-action="remove" title="Remove from battle">${ICONS.x}</button>` : ''}
    </div>`;
  }

  const ICONS = {
    heart: '<svg viewBox="0 0 32 32"><path fill="currentColor" d="M28 12h-8V4c0-2.21-1.79-4-4-4s-4 1.79-4 4v8H4c-2.21 0-4 1.79-4 4s1.79 4 4 4h8v8c0 2.21 1.79 4 4 4s4-1.79 4-4v-8h8c2.21 0 4-1.79 4-4s-1.79-4-4-4z"/></svg>',
    swords: '<svg viewBox="0 0 256 256" fill="currentColor"><path d="M224.48535,31.51465A11.9987 11.9987,0,0,0,216,28h-.03809l-63.79882.20117a11.99773 11.99773,0,0,0-8.84082,3.92774l-71.53418,78.6875-1.64649-1.64649a20.02681,20.02681,0,0,0-28.28418.002L25.17285,125.85645a19.9986 19.9986,0,0,0-.001,28.28515l14.05859,14.05957L20.11719,187.31348a20.02339 20.02339,0,0,0,0,28.28466l20.28515,20.2837a19.9992 19.9992,0,0,0,28.28418.00048l19.11231-19.11279,14.05957,14.06055a20.02636 20.026,0,0,0,28.28418-.002l16.68457-16.68457a19.9986 19.9986,0,0,0,.001-28.28515l-1.645-1.64551,78.688-71.53418a12.00066 12.00066,0,0,0,3.92774-8.8418L228,40.03809A12.00167 12.00167,0,0,0,224.48535,31.51465ZM116,211.02979,101.94141,196.9707a19.9986 19.9986,0,0,0-28.28418,0L54.544,216.083,39.916,201.45605,59.0293,182.34277a20.02222 20.02222,0,0,0,0-28.28369L44.9707,140,56,128.97021l35.51221,35.5127.00244.00244.00244.00244L127.0293,200Zm87.81543-112.5542-75.62207,68.74707L116.9707,156l51.51465-51.51465a12.0001 12.0001,0,0,0-16.9707-16.9707L100,139.0293,88.77734,127.80615l68.74707-75.62158,46.4375-.14648Z"/></svg>',
    images: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="5" width="14" height="12" rx="1.5"/><circle cx="10.5" cy="9" r="1.2"/><path d="M6 14l3-3 2.5 2.5L15 10l5 5"/><path d="M4 9v9a2 2 0 0 0 2 2h11"/></svg>',
    skull: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 1A10 10 0 0 0 2 10.889a9.79 9.79 0 0 0 3.375 7.4v1.488A3.24 3.24 0 0 0 8.625 23h6.75a3.24 3.24 0 0 0 3.25-3.223V18.29A9.79 9.79 0 0 0 22 10.889 10 10 0 0 0 12 1Zm5 16.037a1 1 0 0 0-.379.784v1.956A1.24 1.24 0 0 1 15.375 21H15v-3a1 1 0 0 0-2 0v3h-2v-3a1 1 0 0 0-2 0v3h-.375a1.24 1.24 0 0 1-1.25-1.223v-1.956A1 1 0 0 0 7 17.037a7.81 7.81 0 0 1-3-6.148 8 8 0 0 1 16 0 7.81 7.81 0 0 1-3 6.148ZM11 13a2 2 0 1 1-4 0 2 2 0 0 1 4 0Zm6 0a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>',
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="3.5"/><path d="M4.5 20c1.2-4 4-6 7.5-6s6.3 2 7.5 6"/></svg>',
    shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/></svg>',
    shieldPlus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/><path d="M12 8.5v5M9.5 11h5"/></svg>',
    lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="9" rx="1.75"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/></svg>'
  };

  function sortedParticipants() {
    const arr = [...participants];
    if (sortMode === 'initiative') {
      arr.sort((a, b) => b.initiative - a.initiative || a.sort_order - b.sort_order);
      return [{ heading: null, items: arr }];
    }
    // group mode
    const groups = new Map();
    arr.forEach((p) => {
      const key = p.gid || 'none';
      if (!groups.has(key)) groups.set(key, { name: p.group_name || 'Ungrouped', color: p.group_color, items: [] });
      groups.get(key).items.push(p);
    });
    groups.forEach((g) => g.items.sort((a, b) => b.initiative - a.initiative || a.sort_order - b.sort_order));
    const sections = [...groups.entries()]
      .sort((a, b) => {
        if (a[0] === 'none') return 1;
        if (b[0] === 'none') return -1;
        return a[1].name.localeCompare(b[1].name);
      })
      .map(([, g]) => ({ heading: g, items: g.items }));
    return sections;
  }

  function render() {
    if (participants.length === 0) {
      root.innerHTML = '';
      root.appendChild(emptyTemplate.content.cloneNode(true));
      return;
    }
    const sections = sortedParticipants();
    let html = '';
    sections.forEach((section) => {
      if (section.heading) {
        html += `<div class="group-heading"><span class="group-dot" style="background:${section.heading.color || '#c9a24b'}"></span>${escapeHtml(section.heading.name)}</div>`;
      }
      html += '<div class="battle-list">' + section.items.map(rowMarkup).join('') + '</div>';
    });
    root.innerHTML = html;
  }

  async function apiCall(path, body) {
    const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) throw new Error('Request failed');
    return res.json();
  }

  async function loadBattle() {
    const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/battle`);
    participants = await res.json();
    render();
  }

  async function addCharacter(characterId) {
    participants = await apiCall('/api/battle/add', { character_id: characterId });
    render();
  }

  // ---- Event delegation for the battle list ----
  root.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const row = btn.closest('.battle-row');
    if (!row) return;
    const pid = row.dataset.pid;
    const action = btn.dataset.action;

    if (action === 'heal' || action === 'damage' || action === 'temphp') {
      const input = row.querySelector('.hp-amount-input');
      const amount = parseInt(input.value, 10) || 1;
      const payload = action === 'temphp' ? { value: amount } : { amount };
      participants = await apiCall(`/api/battle/${pid}/${action}`, payload);
      render();
    } else if (action === 'die') {
      participants = await apiCall(`/api/battle/${pid}/die`, {});
      render();
    } else if (action === 'revive') {
      participants = await apiCall(`/api/battle/${pid}/revive`, {});
      render();
    } else if (action === 'remove') {
      const p = participants.find((x) => String(x.id) === pid);
      const label = p ? p.display_name : 'this character';
      const ok = await window.confirmAction(`Remove ${label} from the battle?`);
      if (!ok) return;
      participants = await apiCall(`/api/battle/${pid}/remove`, {});
      render();
    } else if (action === 'view') {
      const p = participants.find((x) => String(x.id) === pid);
      if (p) openDetailModal(p.character_id);
    }
  });

  root.addEventListener('change', async (e) => {
    const input = e.target.closest('[data-action="initiative"], [data-action="ac"], [data-action="max-hp"]');
    if (!input) return;
    const row = input.closest('.battle-row');
    const pid = row.dataset.pid;
    const action = input.dataset.action;
    const value = parseInt(input.value, 10) || 0;
    participants = await apiCall(`/api/battle/${pid}/${action}`, { value });
    render();
  });

  // ---- Sort toggle (DM-only -- the toggle isn't even rendered for Players) ----
  const sortToggleEl = document.getElementById('sort-toggle');
  if (sortToggleEl) {
    sortToggleEl.addEventListener('click', (e) => {
      const btn = e.target.closest('.sort-option');
      if (!btn) return;
      document.querySelectorAll('.sort-option').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      sortMode = btn.dataset.sort;
      render();
    });
  }

  // ---- Clear battle (DM-only) ----
  const clearBattleBtn = document.getElementById('clear-battle-btn');
  if (clearBattleBtn) {
    clearBattleBtn.addEventListener('click', async () => {
      const confirmed = await window.confirmAction('Clear the entire battle? This removes everyone from the field.');
      if (!confirmed) return;
      participants = await apiCall('/api/battle/clear', {});
      render();
    });
  }

  // ---- Add Character modal (DM-only) ----
  const addModal = document.getElementById('add-modal');
  const addModalList = document.getElementById('add-modal-list');
  const addModalGroups = document.getElementById('add-modal-groups');
  const addModalSearch = document.getElementById('add-modal-search');

  function distinctGroups() {
    const map = new Map();
    allCharacters.forEach((c) => {
      if (c.group_id && !map.has(c.group_id)) {
        map.set(c.group_id, { id: c.group_id, name: c.group_name, color: c.group_color });
      }
    });
    return [...map.values()];
  }

  function renderAddModalGroups() {
    const groups = distinctGroups();
    if (groups.length === 0) {
      addModalGroups.innerHTML = '';
      return;
    }
    addModalGroups.innerHTML = `
      <p class="hint-text" style="margin:0 0 8px;">Add a whole group at once</p>
      <div class="add-modal-groups-row">
        ${groups.map((g) => `
          <button type="button" class="chip add-whole-group-chip" data-add-group="${g.id}" style="border-color:${g.color};">
            <span class="group-dot" style="background:${g.color}"></span>${escapeHtml(g.name)}
          </button>
        `).join('')}
      </div>
      <div class="add-modal-divider"></div>
    `;
  }

  function renderAddModalList(filterText) {
    const q = (filterText || '').trim().toLowerCase();
    const filtered = q
      ? allCharacters.filter((c) => c.name.toLowerCase().includes(q))
      : allCharacters;

    if (allCharacters.length === 0) {
      addModalList.innerHTML = `<p class="hint-text">No characters exist yet. Create one first.</p>`;
      return;
    }
    if (filtered.length === 0) {
      addModalList.innerHTML = `<p class="hint-text">No characters match "${escapeHtml(filterText)}".</p>`;
      return;
    }
    addModalList.innerHTML = filtered.map((c) => `
      <div class="add-character-item">
        <div class="battle-avatar" style="width:36px;height:36px;border-color:${c.group_color || 'var(--brass)'};">${c.avatar_path ? `<img src="/uploads/${c.avatar_path}" alt="">` : ICONS.user}</div>
        <div class="battle-name-block">
          <div class="battle-name" style="font-size:14px;">${escapeHtml(c.name)}</div>
          <div class="battle-meta">${c.group_name ? escapeHtml(c.group_name) : 'Ungrouped'}</div>
        </div>
        <button type="button" class="btn btn-primary btn-sm" data-add-char="${c.id}">Add</button>
      </div>
    `).join('');
  }

  const openAddModalBtn = document.getElementById('open-add-modal');
  if (openAddModalBtn) {
    openAddModalBtn.addEventListener('click', () => {
      addModalSearch.value = '';
      renderAddModalGroups();
      renderAddModalList('');
      addModal.hidden = false;
      addModalSearch.focus();
    });
  }
  document.getElementById('close-add-modal').addEventListener('click', () => { addModal.hidden = true; });
  addModal.addEventListener('click', (e) => { if (e.target === addModal) addModal.hidden = true; });

  addModalSearch.addEventListener('input', () => renderAddModalList(addModalSearch.value));

  addModalList.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-add-char]');
    if (!btn) return;
    await addCharacter(parseInt(btn.dataset.addChar, 10));
  });

  addModalGroups.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-add-group]');
    if (!btn) return;
    participants = await apiCall('/api/battle/add-group', { group_id: parseInt(btn.dataset.addGroup, 10) });
    render();
  });

  // ---- Detail modal ----
  const detailModal = document.getElementById('detail-modal');
  const detailTitle = document.getElementById('detail-modal-title');
  const detailBody = document.getElementById('detail-modal-body');

  async function openDetailModal(characterId) {
    const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/characters/${characterId}/detail`);
    const c = await res.json();
    detailTitle.textContent = c.name;
    const canDeleteSheets = window.IS_DM || c.created_by === window.USER_ID;
    const sheetsHtml = c.sheets.length
      ? `<div class="sheet-thumbs">${c.sheets.map((s) => `<div class="sheet-thumb"><img src="/uploads/${s.image_path}" data-lightbox-src="/uploads/${s.image_path}"${canDeleteSheets ? ` data-delete-url="/campaigns/${window.CAMPAIGN_ID}/characters/${c.id}/sheets/${s.id}/delete"` : ''}></div>`).join('')}</div>`
      : `<p class="hint-text">No sheet images attached.</p>`;
    detailBody.innerHTML = `
      <div class="detail-header">
        <div class="avatar-frame" style="width:72px;height:72px;">${c.avatar_path ? `<img src="/uploads/${c.avatar_path}">` : ICONS.user}</div>
        <div>
          <div class="dossier-name">${escapeHtml(c.name)}</div>
          <div class="dossier-meta">Lvl ${c.level} &middot; ${c.group_name || 'Ungrouped'}</div>
        </div>
      </div>
      <div class="dossier-stats" style="margin-top:16px;">
        <div class="stat-pill"><span class="val">${c.str_score}</span><span class="lbl">STR</span></div>
        <div class="stat-pill"><span class="val">${c.dex_score}</span><span class="lbl">DEX</span></div>
        <div class="stat-pill"><span class="val">${c.con_score}</span><span class="lbl">CON</span></div>
        <div class="stat-pill"><span class="val">${c.int_score}</span><span class="lbl">INT</span></div>
        <div class="stat-pill"><span class="val">${c.wis_score}</span><span class="lbl">WIS</span></div>
        <div class="stat-pill"><span class="val">${c.cha_score}</span><span class="lbl">CHA</span></div>
      </div>
      <div class="dossier-hp-ac" style="margin-top:12px;">
        <span class="badge-hp">${ICONS.heart} ${c.max_hp} HP</span>
        <span class="badge-ac">${ICONS.shield} AC ${c.armor_class}</span>
      </div>
      <div class="detail-notes">${c.notes_html || '<em>No notes recorded.</em>'}</div>
      <div class="form-section-title" style="margin-top:18px;">Sheets</div>
      ${sheetsHtml}
    `;
    detailModal.hidden = false;
  }

  document.getElementById('close-detail-modal').addEventListener('click', () => { detailModal.hidden = true; });
  detailModal.addEventListener('click', (e) => { if (e.target === detailModal) detailModal.hidden = true; });

  // ---- HP bar drag-to-scrub: hold and drag anywhere on the bar to set HP directly ----
  let scrubbing = null; // { pid, max, track, fill, readout }

  function valueFromPointer(clientX, track, max) {
    const rect = track.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return Math.round(frac * max);
  }

  function updateScrubVisual(value, max) {
    const pct = max ? Math.max(0, Math.min(100, Math.round((value / max) * 100))) : 0;
    scrubbing.fill.style.width = `${pct}%`;
    scrubbing.fill.className = `hp-bar-fill ${hpColorClass(pct)}`;
    scrubbing.readout.textContent = `${value} / ${max}`;
    scrubbing.value = value;
  }

  root.addEventListener('mousedown', (e) => {
    const track = e.target.closest('[data-hp-scrub]');
    if (!track) return;
    const row = track.closest('.battle-row');
    if (row.classList.contains('is-dead')) return;
    const pid = track.dataset.hpScrub;
    const max = parseInt(track.dataset.max, 10) || 0;
    scrubbing = {
      pid,
      max,
      track,
      fill: track.querySelector('.hp-bar-fill'),
      readout: row.querySelector('.hp-readout'),
      value: 0,
    };
    track.classList.add('is-scrubbing');
    updateScrubVisual(valueFromPointer(e.clientX, track, max), max);
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (!scrubbing) return;
    updateScrubVisual(valueFromPointer(e.clientX, scrubbing.track, scrubbing.max), scrubbing.max);
  });

  window.addEventListener('mouseup', async () => {
    if (!scrubbing) return;
    const { pid, value, track } = scrubbing;
    track.classList.remove('is-scrubbing');
    scrubbing = null;
    participants = await apiCall(`/api/battle/${pid}/set_hp`, { value });
    render();
  });

  // Touch equivalent (single-finger drag)
  root.addEventListener('touchstart', (e) => {
    const track = e.target.closest('[data-hp-scrub]');
    if (!track) return;
    const row = track.closest('.battle-row');
    if (row.classList.contains('is-dead')) return;
    const pid = track.dataset.hpScrub;
    const max = parseInt(track.dataset.max, 10) || 0;
    scrubbing = {
      pid,
      max,
      track,
      fill: track.querySelector('.hp-bar-fill'),
      readout: row.querySelector('.hp-readout'),
      value: 0,
    };
    track.classList.add('is-scrubbing');
    updateScrubVisual(valueFromPointer(e.touches[0].clientX, track, max), max);
  }, { passive: true });

  root.addEventListener('touchmove', (e) => {
    if (!scrubbing) return;
    updateScrubVisual(valueFromPointer(e.touches[0].clientX, scrubbing.track, scrubbing.max), scrubbing.max);
  }, { passive: true });

  window.addEventListener('touchend', async () => {
    if (!scrubbing) return;
    const { pid, value, track } = scrubbing;
    track.classList.remove('is-scrubbing');
    scrubbing = null;
    participants = await apiCall(`/api/battle/${pid}/set_hp`, { value });
    render();
  });

  // ---- Init ----
  (async function init() {
    await loadBattle();
    const addedId = root.dataset.addedId;
    if (addedId) {
      await addCharacter(parseInt(addedId, 10));
      const url = new URL(window.location);
      url.searchParams.delete('added');
      window.history.replaceState({}, '', url);
    }
  })();

  // Without this, anyone who isn't the one making changes (a Player
  // watching the DM run the fight, or the DM's own second tab) only sees
  // heals/damage/initiative/new arrivals after a manual reload. Skipped
  // mid-HP-scrub or while an amount/search input has focus so a poll can't
  // wipe out something half-typed.
  setInterval(async () => {
    if (scrubbing) return;
    const active = document.activeElement;
    if (active && active.tagName === 'INPUT' && (root.contains(active) || (addModal && addModal.contains(active)))) return;
    try {
      const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/battle`);
      if (!res.ok) return;
      participants = await res.json();
      render();
    } catch (e) { /* transient network hiccup -- try again next tick */ }
  }, 3000);
})();
