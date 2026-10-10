(function () {
  window.DICE_PAGE = true;      // own rolls are already shown big on this page, so no toast for them
  const tray = document.getElementById('dice-tray');
  const resultsRoot = document.getElementById('dice-results');
  const emptyTemplate = document.getElementById('empty-results-template');
  const rollBtn = document.getElementById('roll-btn');
  const clearTrayBtn = document.getElementById('clear-tray-btn');
  const modifierInput = document.getElementById('modifier');

  const queue = {}; // { 4: count, 6: count, ... }
  [4, 6, 8, 10, 12, 20, 100].forEach((d) => { queue[d] = 0; });

  // Classic 6-sided die pip layouts, numbered 1-9 across a 3x3 grid
  // (1=top-left, 5=center, 9=bottom-right, etc.)
  const PIP_POSITIONS = {
    1: [5],
    2: [1, 9],
    3: [1, 5, 9],
    4: [1, 3, 7, 9],
    5: [1, 3, 5, 7, 9],
    6: [1, 3, 4, 6, 7, 9],
  };

  function pipsMarkup(value) {
    const active = new Set(PIP_POSITIONS[value] || []);
    let html = '<div class="d6-pips">';
    for (let i = 1; i <= 9; i++) {
      html += `<span class="pip ${active.has(i) ? 'active' : ''}"></span>`;
    }
    html += '</div>';
    return html;
  }

  function updateBadge(die) {
    const selector = tray.querySelector(`.die-selector[data-die="${die}"]`);
    const badge = selector.querySelector('.die-count-badge');
    const count = queue[die];
    if (count > 0) {
      badge.hidden = false;
      badge.textContent = count;
    } else {
      badge.hidden = true;
    }
    selector.classList.toggle('has-count', count > 0);
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
  tray.addEventListener('click', syncModes);
  clearTrayBtn.addEventListener('click', syncModes);
  syncModes();

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
      rollBtn.disabled = false;
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
        ${plan.map((p, i) => `
          <div class="die-tile-wrap${p.dropped ? ' is-dropped' : ''}">
            <div class="die-shape d${p.die} die-tile rolling" data-final="${p.final}" data-sides="${p.die}" style="animation-delay:${i * 40}ms;">
              <span class="die-tile-number">?</span>
            </div>
            <span class="die-tile-label">d${p.die}</span>
          </div>
        `).join('')}
      </div>
    `;

    // Animate: flicker random numbers, then settle
    const tiles = resultsRoot.querySelectorAll('.die-tile');
    tiles.forEach((tile) => {
      const sides = parseInt(tile.dataset.sides, 10);
      const finalVal = parseInt(tile.dataset.final, 10);
      const numEl = tile.querySelector('.die-tile-number');
      const flickerDuration = 500 + Math.random() * 250;
      const start = performance.now();

      function flicker(now) {
        const elapsed = now - start;
        if (elapsed < flickerDuration) {
          numEl.textContent = rollOne(sides);
          requestAnimationFrame(flicker);
        } else {
          if (sides === 6) {
            tile.innerHTML = pipsMarkup(finalVal);
          } else {
            numEl.textContent = finalVal;
          }
          tile.classList.remove('rolling');
          if (finalVal === sides) tile.classList.add('die-crit-high');
          if (finalVal === 1) tile.classList.add('die-crit-low');
          tile.classList.add('die-settle');
        }
      }
      requestAnimationFrame(flicker);
    });

    // Animate total count-up after dice settle
    const totalEl = document.getElementById('roll-total-display');
    const settleDelay = 900;
    setTimeout(() => {
      const start = performance.now();
      const duration = 450;
      function countUp(now) {
        const t = Math.min(1, (now - start) / duration);
        totalEl.textContent = Math.round(grandTotal * t);
        if (t < 1) requestAnimationFrame(countUp);
        else totalEl.textContent = grandTotal;
      }
      requestAnimationFrame(countUp);
    }, settleDelay);
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
