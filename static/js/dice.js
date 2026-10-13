(function () {
  window.DICE_PAGE = true;      // own rolls are already shown big on this page, so no toast for them
  const tray = document.getElementById('dice-tray');
  const resultsRoot = document.getElementById('dice-results');
  const emptyTemplate = document.getElementById('empty-results-template');
  const rollBtn = document.getElementById('roll-btn');
  const clearTrayBtn = document.getElementById('clear-tray-btn');
  const modifierInput = document.getElementById('modifier');

  const Art = window.LedgerDiceArt;
  const EASE_OUT = 'cubic-bezier(0.23, 1, 0.32, 1)';
  const calm = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  const formulaEl = document.getElementById('dice-formula');
  const queue = {}; // { 4: count, 6: count, ... }
  [4, 6, 8, 10, 12, 20, 100].forEach((d) => { queue[d] = 0; });

  // Draw the dice in the tray (same outlined set as the corner widget) and in the empty state.
  document.querySelectorAll('[data-art]').forEach((slot) => { slot.appendChild(Art.svg(parseInt(slot.dataset.art, 10))); });

  function formulaText() {
    const parts = [4, 6, 8, 10, 12, 20, 100].filter((d) => queue[d] > 0).map((d) => `${queue[d]}d${d}`);
    if (!parts.length) return 'Tap a die to add it';
    const mod = parseInt(modifierInput.value, 10) || 0;
    return parts.join(' + ') + (mod ? ` ${mod > 0 ? '+' : '\u2212'} ${Math.abs(mod)}` : '');
  }
  function syncControls() {
    if (formulaEl) formulaEl.textContent = formulaText();
    rollBtn.disabled = totalQueued() === 0;
    clearTrayBtn.hidden = totalQueued() === 0;
  }
  // a quick press on the die and a pop on the count, so a tap is felt on the frame it lands
  function pop(node, from) {
    if (!node || !node.animate || calm.matches) return;
    node.animate([{ transform: `scale(${from})` }, { transform: 'scale(1.12)', offset: 0.6 }, { transform: 'scale(1)' }], { duration: 220, easing: EASE_OUT });
  }

  function updateBadge(die) {
    const selector = tray.querySelector(`.die-selector[data-die="${die}"]`);
    const badge = selector.querySelector('.die-count-badge');
    const count = queue[die];
    const wasHidden = badge.hidden;
    if (count > 0) {
      badge.hidden = false;
      badge.textContent = count;
      pop(badge, wasHidden ? 0.5 : 0.8);
    } else {
      badge.hidden = true;
    }
    selector.classList.toggle('has-count', count > 0);
    syncControls();
  }

  tray.addEventListener('click', (e) => {
    const selector = e.target.closest('.die-selector');
    if (!selector) return;
    const die = selector.dataset.die;
    const badge = e.target.closest('.die-count-badge');
    if (badge && queue[die] > 0) {
      queue[die] -= 1; // clicking the badge removes one
    } else {
      queue[die] += 1; // clicking the shape adds one
      pop(selector.querySelector('.die-art'), 0.9);
    }
    updateBadge(die);
  });

  clearTrayBtn.addEventListener('click', () => {
    Object.keys(queue).forEach((d) => { queue[d] = 0; updateBadge(d); });
  });

  function rollOne(sides) {
    return 1 + Math.floor(Math.random() * sides);
  }

  function renderEmpty() {
    resultsRoot.innerHTML = '';
    resultsRoot.appendChild(emptyTemplate.content.cloneNode(true));
    resultsRoot.querySelectorAll('[data-art]').forEach((slot) => { slot.appendChild(Art.svg(parseInt(slot.dataset.art, 10))); });
  }

  function totalQueued() {
    return Object.values(queue).reduce((a, b) => a + b, 0);
  }

  const labelInput = document.getElementById('roll-label');
  const privateBox = document.getElementById('roll-private');
  const modeRadios = Array.from(document.querySelectorAll('input[name="roll-mode"]'));
  const logRoot = document.getElementById('roll-log-list');
  const errorEl = document.getElementById('roll-error');

  function selectedMode() {
    const on = modeRadios.find((r) => r.checked);
    return on ? on.value : 'normal';
  }

  // Advantage / disadvantage only make sense for a single d20.
  function syncModes() {
    const single = totalQueued() === 1 && queue[20] === 1;
    modeRadios.forEach((r) => { r.disabled = !single && r.value !== 'normal'; });
    if (!single) { const n = modeRadios.find((r) => r.value === 'normal'); if (n) n.checked = true; }
  }
  document.getElementById('mod-dec').addEventListener('click', () => { modifierInput.value = Math.max(-99, (parseInt(modifierInput.value, 10) || 0) - 1); syncControls(); });
  document.getElementById('mod-inc').addEventListener('click', () => { modifierInput.value = Math.min(99, (parseInt(modifierInput.value, 10) || 0) + 1); syncControls(); });
  modifierInput.addEventListener('input', syncControls);
  [labelInput, modifierInput].forEach((input) => input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !rollBtn.disabled) rollBtn.click(); }));
  tray.addEventListener('click', syncModes);
  clearTrayBtn.addEventListener('click', syncModes);
  syncModes();
  syncControls();

  rollBtn.addEventListener('click', async () => {
    if (totalQueued() === 0) return;
    const spec = {};
    Object.keys(queue).forEach((d) => { if (queue[d] > 0) spec[d] = queue[d]; });
    errorEl.textContent = '';
    rollBtn.disabled = true;
    let roll;
    try {
      roll = await LedgerDice.roll({
        dice: spec,
        modifier: parseInt(modifierInput.value, 10) || 0,
        label: labelInput.value,
        mode: selectedMode(),
        private: !!(privateBox && privateBox.checked),
      });
    } catch (err) {
      errorEl.textContent = err.message;
      return;
    } finally {
      syncControls();
    }

    // The server rolled; this page only shows it.
    const plan = roll.dice.map((d) => ({ die: d.sides, final: d.value, dropped: d.dropped }));
    const modifier = roll.modifier;
    const grandTotal = roll.total;

    // group for subtotal display (a dropped die is shown but not counted)
    const groups = {};
    plan.filter((p) => !p.dropped).forEach((p) => {
      if (!groups[p.die]) groups[p.die] = [];
      groups[p.die].push(p.final);
    });
    const groupLines = Object.keys(groups)
      .sort((a, b) => a - b)
      .map((die) => `${groups[die].length}d${die}: ${groups[die].join(' + ')}`)
      .join('  &middot;  ');
    const dropped = plan.find((p) => p.dropped);
    const note = dropped ? ` &middot; ${roll.mode} (dropped ${dropped.final})` : '';

    resultsRoot.innerHTML = `
      <div class="roll-summary">
        <div class="roll-total" id="roll-total-display">0</div>
        <div class="roll-breakdown">${groupLines}${modifier ? ` &middot; modifier ${modifier >= 0 ? '+' : ''}${modifier}` : ''}${note}</div>
      </div>
      <div class="dice-result-grid">
        ${plan.map((p) => `
          <div class="die-tile-wrap${p.dropped ? ' is-dropped' : ''}">
            <div class="die-tile rolling" data-final="${p.final}" data-sides="${p.die}"></div>
            <span class="die-tile-label">d${p.die}${p.dropped ? ' · dropped' : ''}</span>
          </div>
        `).join('')}
      </div>
    `;

    // On a phone the result sits below the Roll button: bring it into view.
    const stage = resultsRoot.closest('.dice-stage');
    if (stage && window.matchMedia('(max-width: 880px)').matches) stage.scrollIntoView({ block: 'nearest', behavior: calm.matches ? 'auto' : 'smooth' });

    // Each die drops in turning, shows flickering numbers while it lands, then settles on the real value.
    // 40 ms stagger, 560 ms throw (ease-out, so it decelerates into place); reduced motion = a plain fade.
    const tiles = Array.from(resultsRoot.querySelectorAll('.die-tile'));
    let landed = 0;
    tiles.forEach((tile, i) => {
      const sides = parseInt(tile.dataset.sides, 10);
      const finalVal = parseInt(tile.dataset.final, 10);
      const art = Art.svg(sides, { text: '?' });
      tile.appendChild(art);
      const spin = (i % 2 ? -1 : 1) * (200 + Math.random() * 140);
      const throwAnim = tile.animate && (calm.matches
        ? [{ opacity: 0 }, { opacity: 1 }]
        : [
            { opacity: 0, transform: `translateY(-34px) rotate(${-spin}deg) scale(0.7)` },
            { opacity: 1, transform: `translateY(4px) rotate(${spin * 0.08}deg) scale(1.04)`, offset: 0.7 },
            { opacity: 1, transform: 'translateY(0) rotate(0deg) scale(1)' },
          ]);
      if (throwAnim) tile.animate(throwAnim, { duration: calm.matches ? 140 : 560, delay: i * 40, easing: EASE_OUT, fill: 'backwards' });
      const flickerDuration = calm.matches ? 0 : 520 + Math.random() * 220;
      const start = performance.now() + i * 40;
      function flicker(now) {
        if (now - start < flickerDuration) {
          if (now >= start) Art.setNumber(art, rollOne(sides));
          requestAnimationFrame(flicker);
          return;
        }
        if (sides === 6) Art.showPips(art, finalVal); else Art.setNumber(art, finalVal);
        tile.classList.remove('rolling');
        if (finalVal === sides) tile.classList.add('die-crit-high');
        if (finalVal === 1) tile.classList.add('die-crit-low');
        if (tile.animate && !calm.matches) tile.animate([{ transform: 'scale(1.16)' }, { transform: 'scale(0.97)', offset: 0.6 }, { transform: 'scale(1)' }], { duration: 300, easing: EASE_OUT });
        landed += 1;
        if (landed === tiles.length) countUpTotal();
      }
      requestAnimationFrame(flicker);
    });

    // The total counts up once the last die has landed.
    const totalEl = document.getElementById('roll-total-display');
    function countUpTotal() {
      if (!totalEl.isConnected) return;
      const start = performance.now();
      const duration = calm.matches ? 1 : 420;
      function countUp(now) {
        const t = Math.min(1, (now - start) / duration);
        totalEl.textContent = Math.round(grandTotal * t);
        if (t < 1) requestAnimationFrame(countUp);
        else {
          totalEl.textContent = grandTotal;
          if (totalEl.animate && !calm.matches) totalEl.animate([{ transform: 'scale(1.1)' }, { transform: 'scale(1)' }], { duration: 260, easing: EASE_OUT });
        }
      }
      requestAnimationFrame(countUp);
    }
  });

  // ---- Recent rolls: the campaign's roll log (everyone's rolls, newest first) ----
  const seenIds = new Set();
  function addLogRow(roll, prepend) {
    if (seenIds.has(roll.id) || !logRoot) return;
    seenIds.add(roll.id);
    const li = document.createElement('li');
    li.className = 'roll-log-item' + (roll.private ? ' is-private' : '');
    const who = document.createElement('span');
    who.className = 'roll-log-who';
    who.textContent = (roll.character || roll.who) + (roll.label ? ' \u00b7 ' + roll.label : '');
    const total = document.createElement('strong');
    total.className = 'roll-log-total';
    total.textContent = roll.total;
    li.append(who, LedgerDice.chips(roll), total);
    if (prepend) logRoot.prepend(li); else logRoot.append(li);
    while (logRoot.children.length > 30) logRoot.lastElementChild.remove();
    const empty = document.getElementById('roll-log-empty');
    if (empty) empty.hidden = true;
  }
  LedgerDice.on((roll) => addLogRow(roll, true));
  LedgerDice.recent().then((rolls) => rolls.slice().reverse().forEach((r) => addLogRow(r, false))).catch(() => {});

  renderEmpty();
})();
