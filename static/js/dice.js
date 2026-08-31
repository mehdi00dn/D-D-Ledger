(function () {
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

  rollBtn.addEventListener('click', () => {
    if (totalQueued() === 0) return;

    // Build the roll plan: list of {die, final} in queue order (grouped by die type)
    const plan = [];
    [4, 6, 8, 10, 12, 20, 100].forEach((die) => {
      for (let i = 0; i < queue[die]; i++) {
        plan.push({ die, final: rollOne(die) });
      }
    });

    const modifier = parseInt(modifierInput.value, 10) || 0;
    const diceTotal = plan.reduce((sum, p) => sum + p.final, 0);
    const grandTotal = diceTotal + modifier;

    // group for subtotal display
    const groups = {};
    plan.forEach((p) => {
      if (!groups[p.die]) groups[p.die] = [];
      groups[p.die].push(p.final);
    });
    const groupLines = Object.keys(groups)
      .sort((a, b) => a - b)
      .map((die) => `${groups[die].length}d${die}: ${groups[die].join(' + ')}`)
      .join('  &middot;  ');

    resultsRoot.innerHTML = `
      <div class="roll-summary">
        <div class="roll-total" id="roll-total-display">0</div>
        <div class="roll-breakdown">${groupLines}${modifier ? ` &middot; modifier ${modifier >= 0 ? '+' : ''}${modifier}` : ''}</div>
      </div>
      <div class="dice-result-grid">
        ${plan.map((p, i) => `
          <div class="die-tile-wrap">
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

  renderEmpty();
})();
