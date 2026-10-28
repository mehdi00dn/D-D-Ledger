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
      return `<img src="/static/icons/pins/${escapeHtml(p.familiar_icon_key || 'paw')}.svg" alt="${escapeHtml(p.char_name)}" class="familiar-avatar-icon">`;
    }
    if (p.avatar_path) {
      return `<img src="/uploads/${escapeHtml(p.avatar_path)}" alt="${escapeHtml(p.char_name)}">`;
    }
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="3.5"/><path d="M4.5 20c1.2-4 4-6 7.5-6s6.3 2 7.5 6"/></svg>`;
  }

  // Escapes text for BOTH element content and quoted attribute values (the old textContent/innerHTML trick left " and '
  // alone, so a name like  " onmouseover="...  could break out of an attribute).
  function escapeHtml(str) {
    return (str == null ? '' : String(str)).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }

  // Colours end up inside style="..." attributes: only hex colours and var(--token) get through.
  function safeColor(value, fallback) {
    return typeof value === 'string' && (/^#[0-9a-fA-F]{3,8}$/.test(value) || /^var\(--[a-z-]+\)$/.test(value)) ? value : fallback;
  }

  // ---- Conditions (DL-41) ----
  const CONDITIONS = JSON.parse((document.getElementById('conditions-data') || { textContent: '[]' }).textContent);
  const CONDITION_LABEL = Object.fromEntries(CONDITIONS.map((c) => [c.key, c.label]));
  const MAX_CUSTOM = 3;          // keep in step with MAX_CUSTOM_CONDITIONS / CUSTOM_CONDITION_MAX_LEN in app.py
  const CUSTOM_MAX_LEN = 24;
  const condCustomHint = document.getElementById('custom-cond-hint');

  const condIcon = (key, cls) => `<svg class="cond-icon ${escapeHtml(cls || '')}" aria-hidden="true"><use href="/static/icons/conditions.svg#${escapeHtml(key)}"></use></svg>`;

  // Chips only animate in when they are genuinely new: the list re-renders on every refresh, so we remember what was
  // already on screen (condSeen) and flag the difference. The first render seeds the set without animating.
  let condSeen = null;
  let condNow = new Set();

  // Hover (or focus / tap) a standard condition (a chip on the row, or a tile in the picker) to read its 2024 rule.  The text comes from the rules reference
  // (fetched once per condition, then kept); custom conditions have no rule.
  const ruleCache = new Map();
  let ruleTip = null, ruleTimer = null, ruleFor = null;
  function hideRule() {
    clearTimeout(ruleTimer); ruleFor = null;
    if (ruleTip) { ruleTip.hidden = true; }
    document.querySelectorAll('[aria-describedby="cond-rule-tip"]').forEach((n) => n.removeAttribute('aria-describedby'));
  }
  async function ruleText(key) {
    if (ruleCache.has(key)) return ruleCache.get(key);
    const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/reference/entry?kind=conditions&ref=${encodeURIComponent('conditions/' + key)}`, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    if (!res.ok) throw new Error('unavailable');
    const d = await res.json();
    ruleCache.set(key, d);
    return d;
  }
  async function showRule(chip) {
    const key = chip.dataset.rule;
    ruleFor = chip;
    let d;
    try { d = await ruleText(key); } catch (e) { return; }      // no rule shown when the reference is down; the chip still works
    if (ruleFor !== chip || !chip.isConnected) return;
    if (!ruleTip) {
      ruleTip = document.createElement('div');
      ruleTip.id = 'cond-rule-tip'; ruleTip.className = 'cond-rule-tip'; ruleTip.setAttribute('role', 'tooltip');
      document.body.append(ruleTip);
    }
    ruleTip.replaceChildren();
    const h = document.createElement('strong'); h.textContent = d.title;
    ruleTip.append(h);
    String(d.body || '').split(/\n+/).filter(Boolean).forEach((line) => { const p = document.createElement('p'); p.textContent = line; ruleTip.append(p); });
    ruleTip.hidden = false;
    const r = chip.getBoundingClientRect(), w = ruleTip.offsetWidth, ht = ruleTip.offsetHeight;
    const left = Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2));
    const top = r.top - ht - 8 >= 8 ? r.top - ht - 8 : r.bottom + 8;
    ruleTip.style.left = `${left}px`; ruleTip.style.top = `${top}px`;
    chip.setAttribute('aria-describedby', 'cond-rule-tip');
  }
  function ruleTarget(e) { return e.target.closest ? e.target.closest('[data-rule]') : null; }
  document.addEventListener('mouseover', (e) => {
    const chip = ruleTarget(e);
    if (!chip || chip === ruleFor) return;
    hideRule(); ruleTimer = setTimeout(() => showRule(chip), 250);
  });
  document.addEventListener('mouseout', (e) => { const chip = ruleTarget(e); if (chip && !chip.contains(e.relatedTarget)) hideRule(); });
  document.addEventListener('focusin', (e) => { const chip = ruleTarget(e); if (chip) { hideRule(); showRule(chip); } });
  document.addEventListener('focusout', (e) => { if (ruleTarget(e)) hideRule(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideRule(); });
  document.addEventListener('pointerdown', (e) => { if (!ruleTarget(e)) hideRule(); });
  window.addEventListener('scroll', hideRule, true);

  function conditionChips(p) {
    const list = (p.conditions || []).filter((k) => CONDITION_LABEL[k]);
    const customs = p.custom_conditions || [];
    const chip = (id, cls, icon, name, removeAttrs) => {
      condNow.add(id);
      const isNew = condSeen && !condSeen.has(id);
      // The icon and the DM's "x" share one slot, so hovering swaps them in place and the chip never changes size.
      if (icon === 'custom') {   // custom conditions carry no icon: the DM's "x" sits in reserved right padding instead
        const x = isDM ? `<button type="button" class="cond-x cond-x-end" ${removeAttrs} title="Remove ${escapeHtml(name)}" aria-label="Remove ${escapeHtml(name)}"></button>` : '';
        return `<span class="cond-chip ${cls}${isDM ? ' has-x' : ''}${isNew ? ' is-new' : ''}"><span class="cond-name">${escapeHtml(name)}</span>${x}</span>`;
      }
      const slot = isDM
        ? `<span class="cond-slot">${condIcon(icon)}<button type="button" class="cond-x" ${removeAttrs} title="Remove ${escapeHtml(name)}" aria-label="Remove ${escapeHtml(name)}"></button></span>`
        : `<span class="cond-slot">${condIcon(icon)}</span>`;
      return `<span class="cond-chip ${cls}${isNew ? ' is-new' : ''}" data-rule="${escapeHtml(icon)}" tabindex="0">${slot}<span class="cond-name">${escapeHtml(name)}</span></span>`;
    };
    const chips = list.map((k) => chip(`${p.id}:s:${k}`, `cond-${escapeHtml(k)}`, k, CONDITION_LABEL[k], `data-action="cond-remove" data-cond="${escapeHtml(k)}"`)).join('')
      + customs.map((n, i) => chip(`${p.id}:c:${n}`, 'cond-custom', 'custom', n, `data-action="cond-remove" data-chip-custom="${i}"`)).join('');
    const addBtn = isDM
      ? `<button type="button" class="cond-add" data-action="conditions" title="Conditions" aria-label="Conditions for ${escapeHtml(p.display_name)}">${ICONS.heart}</button>`
      : '';
    return (chips || addBtn) ? `<div class="cond-row">${chips}${addBtn}</div>` : '';
  }

  // Death saves (DL-145).  Only a downed player character has them.  Everyone sees "Stable"; the 3-and-3 tally and the Roll
  // button come only to the DM and the player who made the character (the server sends null tallies to everyone else).
  const lastSave = {};                                              // participant id -> what the latest roll was, shown for a few seconds
  const SAVE_SHOWN_MS = 5000;
  function saveResultText(r) {
    if (r.outcome === 'revived') return 'Natural 20: back on 1 HP';
    const verdict = { success: 'success', failure: r.roll === 1 ? 'two failures' : 'failure', stable: 'stable', dead: 'dead' }[r.outcome] || '';
    return `Rolled ${r.roll}: ${verdict}`;
  }
  function deathSavesMarkup(p) {
    if (p.is_npc || p.is_dead || !p.is_downed) return '';
    if (p.is_stable) {
      return `<div class="death-saves is-stable" role="group" aria-label="${escapeHtml(p.display_name)} is stable"><span class="ds-stable">Stable</span><span class="ds-note">Unconscious at 0 HP; no more death saves.</span></div>`;
    }
    if (p.death_successes === null || p.death_successes === undefined) return '';
    const pips = (n, kind) => `<span class="ds-pips ds-pips--${kind}" role="img" aria-label="${n} of 3 ${kind === 'ok' ? 'successes' : 'failures'}">`
      + [0, 1, 2].map((i) => `<i class="ds-pip${i < n ? ' is-on' : ''}"></i>`).join('') + '</span>';
    const shown = lastSave[p.id];
    const result = shown && Date.now() < shown.until
      ? `<span class="ds-result is-${escapeHtml(shown.outcome)}" role="status">${escapeHtml(saveResultText(shown))}</span>` : '';
    const roll = p.can_death_save
      ? `<button type="button" class="btn btn-ghost btn-sm ds-roll" data-action="death-save" aria-label="Roll a death save for ${escapeHtml(p.display_name)}">Roll death save</button>` : '';
    return `<div class="death-saves" role="group" aria-label="Death saves for ${escapeHtml(p.display_name)}">`
      + `<span class="ds-title">Death saves</span>`
      + `<span class="ds-tag">Pass</span>${pips(p.death_successes, 'ok')}`
      + `<span class="ds-tag">Fail</span>${pips(p.death_failures, 'bad')}`
      + `${result}${roll}</div>`;
  }

  function rowMarkup(p, rank) {
    const hidden = !!p.hidden_stats; // enemy stats redacted server-side for Players
    const pct = hidden ? 0 : hpPercent(p);
    const dead = !!p.is_dead;
    const showDiedBtn = isDM && !hidden && p.current_hp <= 0 && !dead;
    const groupDot = p.group_color ? `<span class="group-dot" style="background:${safeColor(p.group_color, '#c9a24b')}"></span>` : '';
    const tempHp = p.temp_hp || 0;
    const tempPct = p.char_max_hp ? (tempHp / p.char_max_hp) * 100 : 0;
    const tempBarWidth = Math.max(0, Math.min(100 - pct, tempPct));
    // Everyone can open a dossier; for an NPC a Player gets notes only
    // (the server withholds the numbers).
    const canViewDetails = true;

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
      // Blurred placeholder INIT/AC/HP (no real numbers ever reach the client).
      statsArea = `
      <div class="stats-fog-wrap battle-stats" title="The DM hasn't revealed this creature's stats" aria-label="Stats hidden">
        <div class="stat-chip stats-fogged" aria-hidden="true"><label>INIT</label><input type="number" class="stat-chip-input" value="00" disabled tabindex="-1"></div>
        <div class="stat-chip stats-fogged" aria-hidden="true"><label>AC</label><input type="number" class="stat-chip-input" value="00" disabled tabindex="-1"></div>
        <div class="hp-block stats-fogged" aria-hidden="true">
          <div class="hp-bar-track hp-bar-track-static"><div class="hp-bar-fill ${hpColorClass(70)}" style="--p:0.7"></div></div>
          <span class="hp-readout">00 / 00</span>
        </div>
      </div>`;
    } else {
      statsArea = `
      <div class="battle-stats">
      <div class="stat-chip">
        <label>INIT</label>
        <input type="number" class="stat-chip-input" data-action="initiative" aria-label="Initiative for ${escapeHtml(p.char_name)}" value="${p.initiative}" ${dead || !isDM ? 'disabled' : ''}>
      </div>
      <div class="stat-chip">
        <label>AC</label>
        <input type="number" class="stat-chip-input" data-action="ac" aria-label="Armor class for ${escapeHtml(p.char_name)}" value="${p.effective_ac}" ${dead || !isDM ? 'disabled' : ''}>
      </div>
      <div class="hp-block">
        <div class="hp-bar-track${isDM ? '' : ' hp-bar-track-static'}" ${isDM ? `data-hp-scrub="${p.id}"` : ''} data-max="${p.char_max_hp}" title="${isDM ? 'Hold and drag to set HP' : ''}">
          <div class="hp-bar-fill ${hpColorClass(pct)}" style="--p:${pct / 100}"></div>
          ${tempBarWidth > 0 ? `<div class="hp-bar-temp" style="left:${pct}%; width:${tempBarWidth}%;"></div>` : ''}
        </div>
        <span class="hp-readout">${p.current_hp} / ${p.char_max_hp}${tempHp > 0 ? ` <span class="temp-hp-badge">+${tempHp}</span>` : ''}</span>
      </div>
      </div>`;
    }

    const hpControls = (isDM && !hidden) ? `
      <div class="hp-controls">
        <input type="number" class="hp-amount-input" aria-label="Hit points to heal or damage ${escapeHtml(p.char_name)}" min="0" placeholder="1" value="" ${dead ? 'disabled' : ''}>
        <button type="button" class="hp-btn heal-btn" data-action="heal" title="Heal" ${dead ? 'disabled' : ''}>${ICONS.heart}<span class="hp-btn-label">Heal</span></button>
        <button type="button" class="hp-btn damage-btn" data-action="damage" title="Damage" ${dead ? 'disabled' : ''}>${ICONS.swords}<span class="hp-btn-label">Damage</span></button>
        <button type="button" class="hp-btn temphp-btn" data-action="temphp" title="Temporary HP" ${dead ? 'disabled' : ''}>${ICONS.shieldPlus}<span class="hp-btn-label">Temp</span></button>
      </div>` : '';

    let detailControl = '';
    if (p.is_temp_familiar) {
      if (isDM) {
        detailControl = `<div class="stat-chip"><label>MAX HP</label><input type="number" class="stat-chip-input" data-action="max-hp" aria-label="Max HP for ${escapeHtml(p.char_name)}" min="1" value="${p.char_max_hp}" ${dead ? 'disabled' : ''}></div>`;
      }
    }
    // The picture and the name are the details button (a familiar has no sheet to open).
    const openAttrs = (canViewDetails && !p.is_temp_familiar)
      ? ' data-action="view" role="button" tabindex="0" title="View details"' : '';

    return `
    <div class="battle-row ${dead ? 'is-dead' : ''} ${p.is_active ? 'is-active' : ''}" data-pid="${p.id}"${hidden ? '' : ` data-hp="${p.current_hp}"`}${p.is_active ? ' aria-current="true"' : ''}>
      ${rank ? `<span class="battle-rank" aria-hidden="true">${rank}</span>` : ''}
      <div class="battle-avatar${openAttrs ? ' is-link' : ''}"${openAttrs} aria-label="${escapeHtml(p.display_name)}: details" style="border-color:${safeColor(p.group_color, 'var(--brass)')};">${avatarMarkup(p)}</div>

      <div class="battle-name-block">
        <div class="battle-name${openAttrs ? ' is-link' : ''}"${openAttrs}>${classIconHtml(p.class_key)}${escapeHtml(p.display_name)}</div>
        <div class="battle-sub">
          <div class="battle-meta">${groupDot}${p.group_name ? escapeHtml(p.group_name) : 'No Faction'} &middot; ${p.is_npc ? 'NPC' : 'PC'}</div>
          ${conditionChips(p)}
        </div>
      </div>

      ${statsArea}
      ${hpControls}
      ${detailControl}
      ${deathSavesMarkup(p)}

      ${isDM ? `<div class="battle-end">${showDiedBtn ? `<button type="button" class="btn-icon battle-die" data-action="die" title="Mark as died" aria-label="Mark ${escapeHtml(p.display_name)} as died">${ICONS.skull}</button>` : ''}${dead ? `<button type="button" class="btn btn-ghost btn-sm" data-action="revive">Revive</button>` : ''}<button type="button" class="btn-icon battle-remove" data-action="remove" title="Remove from battle" aria-label="Remove ${escapeHtml(p.display_name)} from battle">${ICONS.x}</button></div>` : ''}
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
      if (!groups.has(key)) groups.set(key, { name: p.group_name || 'No Faction', color: p.group_color, items: [] });
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
    updateTurnBar();
    if (participants.length === 0) {
      root.innerHTML = '';
      root.appendChild(emptyTemplate.content.cloneNode(true));
      return;
    }
    const sections = sortedParticipants();
    let html = '';
    sections.forEach((section) => {
      if (section.heading) {
        html += `<div class="group-heading"><span class="group-dot" style="background:${safeColor(section.heading.color, '#c9a24b')}"></span>${escapeHtml(section.heading.name)}</div>`;
      }
      html += '<div class="battle-list">' + section.items.map((p, i) => rowMarkup(p, isDM && sortMode === 'initiative' ? i + 1 : 0)).join('') + '</div>';
    });
    const before = snapshotRows();
    root.innerHTML = html;
    condSeen = condNow; condNow = new Set();
    animateRows(before);
  }

  // ---- Motion ----
  // The list is re-rendered from scratch on every change, so motion is rebuilt from a before/after comparison:
  // rows that moved slide from where they were (FLIP), HP bars glide from their old fill, a hit or a heal flashes the
  // row and floats the amount up, newcomers fade in, and the row whose turn it is pulses once.  transform and opacity
  // only; skipped entirely for reduced motion.
  const EASE_OUT = 'cubic-bezier(0.23, 1, 0.32, 1)';
  const reduceMotion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  let firstPaint = true;
  let activePid = null;

  function snapshotRows() {
    const snap = new Map();
    root.querySelectorAll('.battle-row').forEach((row) => {
      const fill = row.querySelector('.hp-bar-fill');
      snap.set(row.dataset.pid, {
        top: row.getBoundingClientRect().top,
        pct: fill ? parseFloat(fill.style.getPropertyValue('--p')) : null,
        hp: row.dataset.hp === undefined ? null : Number(row.dataset.hp),
      });
    });
    return snap;
  }

  function animateRows(before) {
    const rows = [...root.querySelectorAll('.battle-row')];
    const activeRow = root.querySelector('.battle-row.is-active');
    const nowActive = activeRow ? activeRow.dataset.pid : null;
    const turnChanged = nowActive && activePid !== null && nowActive !== activePid;
    activePid = nowActive;
    if (reduceMotion.matches || !rows[0] || !rows[0].animate) { firstPaint = false; return; }
    rows.forEach((row, i) => {
      const was = before.get(row.dataset.pid);
      if (!was) {                                   // new on the field (or the very first paint): fade up, staggered on first paint
        row.animate([{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'translateY(0)' }],
          { duration: 240, delay: firstPaint ? Math.min(i, 8) * 40 : 0, easing: EASE_OUT, fill: 'backwards' });
        return;
      }
      const dy = was.top - row.getBoundingClientRect().top;
      if (Math.abs(dy) > 1) {
        row.animate([{ transform: `translateY(${dy}px)` }, { transform: 'translateY(0)' }], { duration: 260, easing: EASE_OUT });
      }
      const fill = row.querySelector('.hp-bar-fill');
      if (fill && was.pct !== null) {
        const now = parseFloat(fill.style.getPropertyValue('--p'));
        if (Math.abs(now - was.pct) > 0.001) {
          fill.animate([{ transform: `scaleX(${was.pct})` }, { transform: `scaleX(${now})` }], { duration: 360, easing: EASE_OUT });
        }
      }
      const hp = row.dataset.hp === undefined ? null : Number(row.dataset.hp);
      if (hp !== null && was.hp !== null && hp !== was.hp) hpFeedback(row, hp - was.hp);
      if (turnChanged && row.dataset.pid === nowActive) {
        row.classList.add('turn-pulse');
        row.addEventListener('animationend', () => row.classList.remove('turn-pulse'), { once: true });
      }
    });
    firstPaint = false;
  }

  function hpFeedback(row, delta) {
    const kind = delta < 0 ? 'hit' : 'heal';
    row.classList.add('flash-' + kind);
    row.addEventListener('animationend', () => row.classList.remove('flash-' + kind), { once: true });
    const host = row.querySelector('.hp-block');
    if (!host) return;
    const tag = document.createElement('span');
    tag.className = 'hp-delta hp-delta-' + kind;
    tag.textContent = (delta < 0 ? '\u2212' : '+') + Math.abs(delta);
    tag.setAttribute('aria-hidden', 'true');
    host.appendChild(tag);
    tag.animate([{ opacity: 0, transform: 'translateY(4px) scale(0.9)' }, { opacity: 1, transform: 'translateY(-6px) scale(1)', offset: 0.25 }, { opacity: 0, transform: 'translateY(-20px) scale(1)' }],
      { duration: 720, easing: EASE_OUT }).onfinish = () => tag.remove();
  }

  // Round counter + the DM's Next turn button (hidden while nobody is on the field).
  function updateTurnBar() {
    const bar = document.getElementById('turn-bar');
    if (!bar) return;
    bar.hidden = participants.length === 0;
    const round = document.getElementById('round-num');
    if (round && participants.length) round.textContent = participants[0].battle_round || 1;
    const started = participants.some((p) => p.is_active);
    const now = document.getElementById('turn-now');
    if (now) {
      const cur = participants.find((p) => p.is_active);
      now.textContent = cur ? cur.display_name : '';
      now.hidden = !cur;
    }
    const label = document.getElementById('next-turn-label');
    if (label) label.textContent = started ? 'Next turn' : 'Start combat';
    // Back / Restart only mean something once the fight has started.
    for (const id of ['prev-turn-btn', 'restart-turns-btn']) {
      const b = document.getElementById(id);
      if (b) b.disabled = !started;
    }
  }

  // ---- Battle log: a ledger strip pinned to the bottom-left of the page; opened, a small card of the last lines rises out of it ----
  const logBox = document.getElementById('battle-log');
  const logBar = document.getElementById('battle-log-toggle');
  const logSheet = document.getElementById('battle-log-sheet');
  const logBackdrop = document.getElementById('battle-log-backdrop');
  const logList = document.getElementById('battle-log-list');
  const logLatest = document.getElementById('battle-log-latest');
  let logSig = '', logTimer = null, logHideTimer = null;
  const clock = (iso) => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };

  function setLogOpen(open) {
    if (!logBox) return;
    clearTimeout(logHideTimer);
    logBar.setAttribute('aria-expanded', String(open));
    if (open) {
      logSheet.hidden = false;
      void logSheet.offsetWidth;                                   // let the closed state paint once, so the card eases in
      logBox.classList.add('is-open');
      logList.scrollTop = logList.scrollHeight;                    // newest at the bottom, and that is where it opens
    } else {
      logBox.classList.remove('is-open');
      logHideTimer = setTimeout(() => { logSheet.hidden = true; }, 160);   // after the quick fade out
    }
  }
  if (logBar) {
    logBar.addEventListener('click', () => setLogOpen(!logBox.classList.contains('is-open')));
    logBackdrop.addEventListener('click', () => setLogOpen(false));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && logBox.classList.contains('is-open')) { setLogOpen(false); logBar.focus(); }
    });
  }
  // The words that follow a name ("Mira took 9 damage", "Grub is now Hexed"), so the name, the verb and the numbers can each be styled.
  const VERBS = [' took ', ' healed ', ' is down', ' died', ' is stable', ' rolled ', ' was revived', ' was hurt', ' was healed', ' is back on', ' has ', ' lost their',
                 ' is now ', ' is no longer ', ' joined the', ' left the', "'s HP set"];
  function span(cls, text) { const el = document.createElement('span'); el.className = cls; el.textContent = text; return el; }
  function withNumbers(text, cls) {                                // digits get their own bold, tabular style
    const out = document.createDocumentFragment();
    text.split(/(\d+)/).forEach((part, i) => { if (part) out.append(i % 2 ? span('bl-n', part) : span(cls, part)); });
    return out;
  }
  function styledText(e) {
    const text = String(e.text), box = document.createElement('span');
    box.className = 'bl-text'; box.title = text;
    if (e.kind === 'round' || e.kind === 'initiative' || (e.kind === 'left' && !/ left the/.test(text))) { box.append(withNumbers(text, 'bl-v')); return box; }
    let at = -1;
    VERBS.forEach((v) => { const i = text.indexOf(v); if (i > 0 && (at < 0 || i < at)) at = i; });
    if (at < 0) { box.append(withNumbers(text, 'bl-v')); return box; }
    box.append(span('bl-who', text.slice(0, at)));
    let rest = text.slice(at);
    if (e.kind === 'condition') {                                  // " is now " / " is no longer " + the condition, which is the point of the line
      const m = rest.match(/^( is now | is no longer )(.*)$/);
      if (m) { box.append(span('bl-v', m[1]), span('bl-who bl-cond', m[2])); return box; }
    }
    box.append(withNumbers(rest, 'bl-v'));
    return box;
  }
  function renderLog(entries) {
    if (!logBox) return;
    logBox.hidden = entries.length === 0;
    if (entries.length === 0) setLogOpen(false);
    const sig = entries.map((e) => e.id).join(',');
    if (sig === logSig) return;
    const hadAny = logSig !== '';
    logSig = sig;
    const oldestFirst = entries.slice().reverse();                 // the API sends newest first; the card reads like a ledger, newest at the bottom
    const nearBottom = logList.scrollHeight - logList.scrollTop - logList.clientHeight < 40;
    logList.replaceChildren(...oldestFirst.map((e) => {
      const li = document.createElement('li');
      li.className = 'bl-item bl-' + String(e.kind).replace(/[^a-z]/g, '');
      const t = document.createElement('time'); t.dateTime = e.at; t.textContent = clock(e.at);
      li.append(t, styledText(e));
      return li;
    }));
    if (nearBottom || !logBox.classList.contains('is-open')) logList.scrollTop = logList.scrollHeight;
    const newest = entries[0];
    logBox.dataset.kind = String(newest.kind).replace(/[^a-z]/g, '');
    if (logLatest) logLatest.textContent = newest.text;
    if (hadAny && !logBox.classList.contains('is-open')) {         // a quick wash on the closed strip when something new lands
      logBox.classList.remove('has-new'); void logBox.offsetWidth; logBox.classList.add('has-new');
    }
  }
  async function loadLog() {
    try {
      const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/battle/log`, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
      if (res.ok) renderLog(await res.json());
    } catch (e) { /* the log is a nicety: a failed fetch just leaves the old one on screen */ }
  }
  function refreshLogSoon() { clearTimeout(logTimer); logTimer = setTimeout(loadLog, 120); }
  loadLog();

  async function apiCall(path, body) {
    const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) {
      const message = await LedgerApi.message(res, 'That did not work. Please try again.');
      LedgerApi.notify(message);                 // say why (no permission, session ended, ...) instead of failing silently
      throw new LedgerApi.Error(message, res.status);
    }
    refreshLogSoon();                              // whatever just changed may have a line in the log
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

  // Characters made elsewhere on the page (the monster manual in the Add menu) hand over the fresh battle.
  document.addEventListener('battle:participants', (e) => { participants = e.detail; render(); });

  // ---- Event delegation for the battle list ----
  // The picture / name act as buttons: Enter or Space opens the details too.
  root.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const t = e.target.closest && e.target.closest('[data-action="view"][role="button"]');
    if (!t) return;
    e.preventDefault();
    t.click();
  });
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
    } else if (action === 'death-save') {
      btn.disabled = true;                                          // one roll per click; the row is redrawn from the answer
      try {
        const out = await apiCall(`/api/battle/${pid}/death-save`, {});
        lastSave[pid] = { roll: out.roll, outcome: out.outcome, until: Date.now() + SAVE_SHOWN_MS };
        participants = out.rows;
        render();
      } catch (err) {
        btn.disabled = false;                                       // apiCall has already said why
      }
    } else if (action === 'remove') {
      const p = participants.find((x) => String(x.id) === pid);
      const label = p ? p.display_name : 'this character';
      const ok = await window.confirmAction(`Remove ${label} from the battle?`);
      if (!ok) return;
      participants = await apiCall(`/api/battle/${pid}/remove`, {});
      render();
    } else if (action === 'view') {
      const p = participants.find((x) => String(x.id) === pid);
      if (p) openDetailModal(p.character_id, p.id);
    } else if (action === 'conditions') {
      openConditions(pid);
    } else if (action === 'cond-remove') {
      const chipEl = btn.closest('.cond-chip');
      if (chipEl) { chipEl.classList.add('is-leaving'); await new Promise((r) => setTimeout(r, 170)); }
      const key = btn.dataset.cond;
      const index = btn.dataset.chipCustom;
      mutateConditions((cur) => ({
        standard: key ? (cur.conditions || []).filter((k) => k !== key) : (cur.conditions || []),
        customs: key ? (cur.custom_conditions || []) : (cur.custom_conditions || []).filter((_, i) => String(i) !== index),
      }), pid);
    }
  });

  // DM turn controls: one handler, each button names the endpoint it calls.
  const TURN_BUTTONS = {
    'next-turn-btn': { path: '/api/battle/next-turn' },
    'prev-turn-btn': { path: '/api/battle/prev-turn' },
    'restart-turns-btn': { path: '/api/battle/restart-turns', confirm: 'Restart from round 1 at the top of the initiative order?' },
  };
  for (const [id, cfg] of Object.entries(TURN_BUTTONS)) {
    const btn = document.getElementById(id);
    if (!btn) continue;
    btn.addEventListener('click', async () => {
      if (cfg.confirm && !(await window.confirmAction(cfg.confirm))) return;
      btn.disabled = true;
      try {
        participants = await apiCall(cfg.path, {});
        render();
      } finally {
        btn.disabled = false;
        updateTurnBar();                       // re-applies the right enabled / disabled state
      }
    });
  }

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

  // ---- Long rest (DM-only) ----
  const longRestBtn = document.getElementById('long-rest-btn');
  if (longRestBtn) {
    longRestBtn.addEventListener('click', async () => {
      if (!(await window.confirmAction('Long rest: every player character goes back to full HP, with no temporary HP and no death saves?'))) return;
      participants = await apiCall('/api/battle/long-rest', {});
      render();
    });
  }

  // ---- Short rest (DM-only): type the HP each player character regained ----
  const shortRestBtn = document.getElementById('short-rest-btn');
  const shortRestForm = document.getElementById('short-rest-form');
  if (shortRestBtn && shortRestForm) {
    const list = document.getElementById('short-rest-list');
    const empty = document.getElementById('short-rest-empty');
    const go = document.getElementById('short-rest-go');
    shortRestBtn.addEventListener('click', () => {
      const pcs = participants.filter((p) => !p.is_npc && !p.is_dead);
      list.innerHTML = pcs.map((p) => `<li class="short-rest-row"><label for="sr-${p.id}" class="short-rest-name"><b>${escapeHtml(p.char_name)}</b><small>${p.current_hp} / ${p.char_max_hp} HP</small></label>` +
        `<input type="number" id="sr-${p.id}" data-pid="${p.id}" min="0" max="${Math.max(0, p.char_max_hp - p.current_hp)}" inputmode="numeric" placeholder="0"></li>`).join('');
      empty.hidden = pcs.length > 0;
      go.disabled = pcs.length === 0;
    });
    shortRestForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const heals = {};
      list.querySelectorAll('input[data-pid]').forEach((i) => { if (i.value) heals[i.dataset.pid] = parseInt(i.value, 10) || 0; });
      go.disabled = true;
      try {
        participants = await apiCall('/api/battle/short-rest', { heals });
        render();
        shortRestForm.closest('dialog').close();
      } catch (err) { /* the message was already shown */ } finally { go.disabled = false; }
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
      (c.factions || []).forEach((f) => { if (!map.has(f.id)) map.set(f.id, { id: f.id, name: f.name, color: f.color }); });
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
      <p class="hint-text" style="margin:0 0 8px;">Add a whole faction at once</p>
      <div class="add-modal-groups-row">
        ${groups.map((g) => `
          <button type="button" class="chip add-whole-group-chip" data-add-group="${g.id}" style="border-color:${safeColor(g.color, '#c9a24b')};">
            <span class="group-dot" style="background:${safeColor(g.color, '#c9a24b')}"></span>${escapeHtml(g.name)}
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
        <div class="battle-avatar" style="width:36px;height:36px;border-color:${safeColor(c.group_color, 'var(--brass)')};">${c.avatar_path ? `<img src="/uploads/${escapeHtml(c.avatar_path)}" alt="">` : ICONS.user}</div>
        <div class="battle-name-block">
          <div class="battle-name" style="font-size:14px;">${classIconHtml(c.class_key)}${escapeHtml(c.name)}</div>
          <div class="battle-meta">${(c.factions || []).length ? escapeHtml(c.factions.map((f) => f.name).join(', ')) : 'No Faction'}</div>
        </div>
        <button type="button" class="btn btn-primary btn-sm" data-add-char="${c.id}">Add</button>
      </div>
    `).join('');
  }

  const rollInitBtn = document.getElementById('roll-initiative-btn');
  if (rollInitBtn) {
    const menu = document.getElementById('roll-menu');
    const scopeLabel = { all: 'everyone', pc: 'the player characters', npc: 'the NPCs and monsters' };
    const inScope = (p, scope) => !p.is_dead && (scope === 'all' || (scope === 'npc') === !!p.is_npc);
    const refreshMenu = () => {
      menu.querySelectorAll('[data-roll-scope]').forEach((b) => {
        const n = participants.filter((p) => inScope(p, b.dataset.rollScope)).length;
        b.disabled = n === 0;
        b.querySelector('small').textContent = String(n);
      });
    };
    const setOpen = (open) => { menu.classList.toggle('is-open', open); rollInitBtn.setAttribute('aria-expanded', open ? 'true' : 'false'); if (open) refreshMenu(); };
    const rollFor = async (scope) => {
      const standing = participants.filter((p) => inScope(p, scope));
      if (!standing.length) return;
      if (standing.some((p) => p.initiative) && !(await window.confirmAction('Roll initiative for ' + scopeLabel[scope] + ' again? The numbers already entered are replaced.'))) return;
      rollInitBtn.disabled = true;
      try { participants = await apiCall('/api/battle/roll-initiative', { scope }); render(); }
      catch (e) { if (window.LedgerDice) LedgerDice.announceError('Could not roll initiative.'); }
      rollInitBtn.disabled = false;
    };
    // A mouse hovers to see the choices and a click still rolls for everyone; a touch screen has no hover, so the first tap opens them.
    const touchOnly = window.matchMedia && window.matchMedia('(hover: none)').matches;
    rollInitBtn.addEventListener('click', () => {
      if (touchOnly) { setOpen(!menu.classList.contains('is-open')); return; }
      rollFor('all');
    });
    menu.addEventListener('pointerenter', refreshMenu);
    menu.addEventListener('focusin', refreshMenu);
    menu.addEventListener('click', (e) => {
      const item = e.target.closest('[data-roll-scope]');
      if (!item) return;
      setOpen(false);
      menu.querySelector('.roll-menu-list').classList.add('is-closing');          // keeps it shut under the pointer until it leaves
      rollFor(item.dataset.rollScope);
    });
    menu.addEventListener('pointerleave', () => { menu.querySelector('.roll-menu-list').classList.remove('is-closing'); if (!touchOnly) setOpen(false); });
    document.addEventListener('click', (e) => { if (touchOnly && !menu.contains(e.target)) setOpen(false); });
    menu.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { setOpen(false); rollInitBtn.focus(); menu.querySelector('.roll-menu-list').classList.add('is-closing'); }
    });
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

  // ---- Conditions modal (DM-only).  A modal rather than an inline menu, so the live refresh
  // that redraws the rows can never close it halfway through a pick. ----
  const condModal = document.getElementById('conditions-modal');
  const condPicker = document.getElementById('conditions-picker');
  let condPid = null;

  function renderConditionPicker() {
    const p = participants.find((x) => String(x.id) === String(condPid));
    if (!p) { condModal.hidden = true; return; }
    hideRule();                                   // the tiles below are rebuilt, so a tip pointing at an old one would be stale
    document.getElementById('conditions-modal-title').textContent = `Conditions — ${p.display_name}`;
    const on = new Set(p.conditions || []);
    const customs = p.custom_conditions || [];
    condPicker.innerHTML = CONDITIONS.map((c) => `
      <button type="button" class="cond-tile ${on.has(c.key) ? 'active' : ''}" data-cond="${escapeHtml(c.key)}" data-rule="${escapeHtml(c.key)}" aria-pressed="${on.has(c.key)}">${condIcon(c.key, 'cond-icon-lg')}<span>${escapeHtml(c.label)}</span></button>`).join('')
      + customs.map((n, i) => `
      <button type="button" class="cond-tile cond-tile-custom active" data-custom-index="${i}" aria-pressed="true" title="Click to remove"><span>${escapeHtml(n)}</span></button>`).join('')
      + (customs.length < MAX_CUSTOM ? `
      <button type="button" class="cond-tile cond-tile-new" data-custom-new title="Add a custom condition">${condIcon('plus', 'cond-icon-lg')}<span>Custom</span></button>` : '');
    if (condCustomHint) condCustomHint.textContent = customs.length >= MAX_CUSTOM
      ? `Custom limit reached (${MAX_CUSTOM}). Click a custom condition to remove it.`
      : `Custom conditions: type your own, up to ${MAX_CUSTOM} per character. Click one to remove it.`;
  }

  // Every change goes through one queue and is computed from the latest state, so two quick clicks (or a click while
  // a custom name is being committed) can never overwrite each other with stale lists.
  let condQueue = Promise.resolve();
  function mutateConditions(change, pid = condPid) {
    condQueue = condQueue.then(async () => {
      const p = participants.find((x) => String(x.id) === String(pid));
      if (!p) return;
      const next = change(p);
      participants = await apiCall(`/api/battle/${p.id}/conditions`, { conditions: next.standard, custom_conditions: next.customs });
      render();
      renderConditionPicker();
    }).catch(() => { renderConditionPicker(); });
    return condQueue;
  }

  // The "+ Custom" tile turns into a text box in place; Enter (or leaving the box with text in it) adds the condition.
  function startCustomEntry(tile) {
    if (!participants.some((x) => String(x.id) === String(condPid))) return;
    const box = document.createElement('div');
    box.className = 'cond-tile cond-tile-new is-editing';
    box.innerHTML = `<input type="text" class="cond-custom-input" maxlength="${CUSTOM_MAX_LEN}" placeholder="Name it" aria-label="Custom condition name" autocomplete="off"><button type="button" class="cond-custom-add" data-custom-add>Add</button>`;
    tile.replaceWith(box);
    const input = box.querySelector('input');
    let done = false;
    const finish = async (commit) => {
      if (done) return;
      done = true;
      const name = input.value.trim().replace(/\s+/g, ' ');
      if (!commit || !name) { renderConditionPicker(); return; }
      await mutateConditions((cur) => ({ standard: cur.conditions || [], customs: [...(cur.custom_conditions || []), name] }));
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
    });
    const addBtn = box.querySelector('[data-custom-add]');
    addBtn.addEventListener('click', () => finish(true));
    // Moving focus onto the Add button is not "leaving the box"; the button's own click commits.
    input.addEventListener('blur', (e) => { if (e.relatedTarget !== addBtn) finish(true); });
    input.focus();
  }

  function openConditions(pid) {
    if (!condModal) return;
    condPid = pid;
    renderConditionPicker();
    condModal.hidden = false;
  }

  if (condModal) {
    document.getElementById('close-conditions-modal').addEventListener('click', () => { condModal.hidden = true; });
    condModal.addEventListener('click', (e) => { if (e.target === condModal) condModal.hidden = true; });
    condPicker.addEventListener('click', async (e) => {
      const newTile = e.target.closest('[data-custom-new]');
      if (newTile) { startCustomEntry(newTile); return; }
      const customTile = e.target.closest('[data-custom-index]');
      if (customTile) {
        const index = customTile.dataset.customIndex;
        mutateConditions((cur) => ({ standard: cur.conditions || [], customs: (cur.custom_conditions || []).filter((_, i) => String(i) !== index) }));
        return;
      }
      const btn = e.target.closest('[data-cond]');
      if (!btn) return;
      const key = btn.dataset.cond;
      mutateConditions((cur) => {
        const next = new Set(cur.conditions || []);
        if (next.has(key)) next.delete(key); else next.add(key);
        return { standard: [...next], customs: cur.custom_conditions || [] };
      });
    });
  }

  // ---- Detail modal ----
  const detailModal = document.getElementById('detail-modal');

  function openDetailModal(characterId, participantId) { return window.CharPeek.open(characterId, participantId); }

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
    scrubbing.fill.style.setProperty('--p', pct / 100);
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
  // Live refresh runs through the shared LedgerPoll scheduler: it pauses while this tab is
  // hidden, backs off while nothing changes, and snaps back to full speed on any change.
  // The server snapshot is compared with what is ON SCREEN (not just the previous
  // snapshot), so an optimistic local edit that never reached the server is still corrected.
  let lastBattleSig = null;
  const battlePoll = LedgerPoll.every(async () => {
    if (scrubbing) return 'skip';
    const active = document.activeElement;
    if (active && active.tagName === 'INPUT' && (root.contains(active) || (addModal && addModal.contains(active)))) return 'skip';
    const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/battle`);
    if (!res.ok) return false;
    const rows = await res.json();
    const sig = JSON.stringify(rows);
    const serverChanged = sig !== lastBattleSig;
    lastBattleSig = sig;
    if (sig !== JSON.stringify(participants)) {
      participants = rows;
      render();
    }
    if (serverChanged) loadLog();
    return serverChanged;
  }, { base: 3000, max: 12000 });

  // Real-time: a ping from the server means "something changed" -> refresh right now.  Polling above stays as the safety net.
  if (window.LEDGER_REALTIME && window.LedgerRealtime) {
    LedgerRealtime.connect(window.LEDGER_REALTIME, (p) => { if (p.s !== 'map' && p.s !== 'dice') battlePoll.poke(); });
  }
})();
