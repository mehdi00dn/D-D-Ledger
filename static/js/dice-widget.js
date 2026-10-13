/* Quick-roll dice widget: a small d20 button in the corner of every campaign page (except the Dice page itself).
 * It opens a little tray: pick how many, a modifier, advantage / disadvantage, then tap a die.  Rolls go through
 * LedgerDice, so they are made on the server and everyone in the campaign sees them, exactly like the Dice page.
 * The roller sees the result right in the tray, so their own roll is not also toasted.
 */
(function () {
  'use strict';
  if (!window.LedgerDice || !window.LedgerDiceArt || window.DICE_PAGE) return;

  var SIDES = LedgerDiceArt.SIDES;
  var EASE_OUT = 'cubic-bezier(0.23, 1, 0.32, 1)';
  var reduce = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function dieSvg(sides) { return LedgerDiceArt.svg(sides); }
  function signed(n) { return (n >= 0 ? '+' : '−') + Math.abs(n); }

  // ---- markup ----
  var fab = el('button', 'dice-fab');
  fab.type = 'button';
  fab.setAttribute('aria-label', 'Quick dice');
  fab.setAttribute('aria-expanded', 'false');
  fab.setAttribute('aria-controls', 'dice-widget-panel');
  var fabIcon = el('span', 'dice-fab-icon dice-fab-icon--die');
  fabIcon.appendChild(dieSvg(20, true));
  var fabClose = el('span', 'dice-fab-icon dice-fab-icon--close');
  fabClose.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  fab.append(fabIcon, fabClose);

  var panel = el('div', 'dice-widget');
  panel.id = 'dice-widget-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Quick dice');
  panel.hidden = true;

  var result = el('div', 'dw-result');
  var total = el('div', 'dw-total', '');
  var detail = el('div', 'dw-detail', 'Tap a die to roll it');
  var live = el('div', 'dw-live');
  live.setAttribute('role', 'status');
  live.setAttribute('aria-live', 'polite');
  result.append(total, detail, live);

  var grid = el('div', 'dw-dice');
  var dieBtns = {};
  SIDES.forEach(function (s) {
    var b = el('button', 'dw-die');
    b.type = 'button';
    b.dataset.sides = String(s);
    b.setAttribute('aria-label', 'Roll d' + s);
    b.appendChild(dieSvg(s, true));
    b.appendChild(el('span', 'dw-die-name', 'd' + s));
    grid.appendChild(b);
    dieBtns[s] = b;
  });

  function stepper(label, onStep) {
    var wrap = el('div', 'dw-step');
    var dec = el('button', 'dw-step-btn', '−'); dec.type = 'button'; dec.setAttribute('aria-label', 'Fewer ' + label);
    var val = el('output', 'dw-step-val'); val.setAttribute('aria-label', label);
    var inc = el('button', 'dw-step-btn', '+'); inc.type = 'button'; inc.setAttribute('aria-label', 'More ' + label);
    dec.addEventListener('click', function () { onStep(-1); });
    inc.addEventListener('click', function () { onStep(1); });
    wrap.append(dec, val, inc);
    return { wrap: wrap, val: val };
  }
  var count = 1, modifier = 0, mode = 'normal', privateRoll = false, busy = false;
  var countStep = stepper('dice', function (d) { count = Math.max(1, Math.min(10, count + d)); sync(); });
  var modStep = stepper('modifier', function (d) { modifier = Math.max(-20, Math.min(20, modifier + d)); sync(); });
  var rowCount = el('label', 'dw-field'); rowCount.append(el('span', 'dw-field-name', 'Dice'), countStep.wrap);
  var rowMod = el('label', 'dw-field'); rowMod.append(el('span', 'dw-field-name', 'Modifier'), modStep.wrap);
  var fields = el('div', 'dw-fields'); fields.append(rowCount, rowMod);

  var modes = el('div', 'dw-modes');
  modes.setAttribute('role', 'group'); modes.setAttribute('aria-label', 'Roll mode (one d20)');
  var advBtn = el('button', 'dw-chip', 'Advantage'); advBtn.type = 'button'; advBtn.setAttribute('aria-pressed', 'false');
  var disBtn = el('button', 'dw-chip', 'Disadvantage'); disBtn.type = 'button'; disBtn.setAttribute('aria-pressed', 'false');
  modes.append(advBtn, disBtn);
  var privBtn = null;
  if (window.IS_DM) {
    privBtn = el('button', 'dw-chip', 'DM only'); privBtn.type = 'button'; privBtn.setAttribute('aria-pressed', 'false');
    modes.appendChild(privBtn);
  }
  advBtn.addEventListener('click', function () { mode = mode === 'advantage' ? 'normal' : 'advantage'; sync(); });
  disBtn.addEventListener('click', function () { mode = mode === 'disadvantage' ? 'normal' : 'disadvantage'; sync(); });
  if (privBtn) privBtn.addEventListener('click', function () { privateRoll = !privateRoll; sync(); });

  panel.append(result, grid, fields, modes);
  document.body.append(panel, fab);
  document.body.classList.add('has-dice-fab');
  if (document.getElementById('map-toolbar')) document.body.classList.add('dice-fab-above-dock');

  function sync() {
    countStep.val.textContent = '×' + count;
    modStep.val.textContent = signed(modifier);
    advBtn.setAttribute('aria-pressed', mode === 'advantage' ? 'true' : 'false');
    disBtn.setAttribute('aria-pressed', mode === 'disadvantage' ? 'true' : 'false');
    if (privBtn) privBtn.setAttribute('aria-pressed', privateRoll ? 'true' : 'false');
    modes.classList.toggle('is-idle', count !== 1);          // advantage only ever applies to a single d20
    panel.classList.toggle('is-busy', busy);
  }

  // ---- open / close ----
  var open = false, hideTimer = 0;
  function setOpen(next, restoreFocus) {
    if (open === next) return;
    open = next;
    clearTimeout(hideTimer);
    fab.setAttribute('aria-expanded', next ? 'true' : 'false');
    fab.classList.toggle('is-open', next);
    if (next) {
      panel.hidden = false;
      void panel.offsetHeight;                               // let the closed state paint so the open animates
      panel.classList.add('is-open');
    } else {
      panel.classList.remove('is-open');
      hideTimer = setTimeout(function () { panel.hidden = true; }, reduce.matches ? 20 : 150);
      if (restoreFocus) fab.focus({ preventScroll: true });
    }
  }
  fab.addEventListener('click', function () { setOpen(!open); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && open) setOpen(false, true); });
  document.addEventListener('pointerdown', function (e) {
    if (open && !panel.contains(e.target) && !fab.contains(e.target)) setOpen(false);
  });

  // ---- rolling ----
  var settleAnim = null, flickerRaf = 0;
  function showTotal(value, finalRoll) {
    total.classList.remove('is-crit', 'is-fumble');
    total.textContent = String(value);
    if (finalRoll) {
      var kept = finalRoll.dice.filter(function (d) { return !d.dropped; });
      if (kept.length === 1 && kept[0].sides === 20) {
        if (kept[0].value === 20) total.classList.add('is-crit');
        else if (kept[0].value === 1) total.classList.add('is-fumble');
      }
    }
  }
  function describe(r) {
    var by = {};
    r.dice.filter(function (d) { return !d.dropped; }).forEach(function (d) { (by[d.sides] = by[d.sides] || []).push(d.value); });
    var parts = Object.keys(by).sort(function (a, b) { return a - b; }).map(function (s) { return by[s].length + 'd' + s + ' ' + by[s].join(' + '); });
    var text = parts.join(' · ');
    if (r.modifier) text += ' ' + signed(r.modifier);
    var dropped = r.dice.filter(function (d) { return d.dropped; })[0];
    if (dropped) text += ' · ' + r.mode + ' (dropped ' + dropped.value + ')';
    return text;
  }

  function tumble(btn) {
    var icon = btn.querySelector('svg');
    if (!icon || !icon.animate || reduce.matches) return null;
    return icon.animate([
      { transform: 'rotate(0deg) scale(1)' },
      { transform: 'rotate(200deg) scale(1.18)', offset: 0.45 },
      { transform: 'rotate(380deg) scale(1.06)', offset: 0.8 },
      { transform: 'rotate(360deg) scale(1)' }
    ], { duration: 560, easing: EASE_OUT });
  }

  function roll(sides) {
    if (busy) return;
    busy = true; sync();
    detail.classList.remove('is-error');
    var btn = dieBtns[sides];
    var useMode = (sides === 20 && count === 1) ? mode : 'normal';
    var spec = {}; spec[String(sides)] = count;
    var t0 = performance.now();
    var spin = tumble(btn);
    cancelAnimationFrame(flickerRaf);
    if (settleAnim) settleAnim.cancel();
    total.classList.remove('is-crit', 'is-fumble');
    total.classList.add('is-rolling');
    detail.textContent = count + 'd' + sides + (modifier ? ' ' + signed(modifier) : '') + '…';
    window.DICE_QUIET = true;                                // this roll is shown here, not toasted
    var request = LedgerDice.roll({ dice: spec, modifier: modifier, mode: useMode, private: privateRoll, label: '' });
    function flicker() {
      if (!busy) return;
      showTotal(1 + Math.floor(Math.random() * (sides === 100 ? 100 : sides) * Math.max(1, count)));
      total.classList.add('is-rolling');
      flickerRaf = requestAnimationFrame(function () { setTimeout(flicker, 55); });
    }
    if (!reduce.matches) flicker();
    request.then(function (r) {
      // let the tumble play out (about 0.45s) so a fast server never makes the number appear before the die lands
      var wait = reduce.matches ? 0 : Math.max(0, 450 - (performance.now() - t0));
      return new Promise(function (res) { setTimeout(function () { res(r); }, wait); });
    }).then(function (r) {
      busy = false; cancelAnimationFrame(flickerRaf);
      total.classList.remove('is-rolling');
      showTotal(r.total, r);
      detail.textContent = describe(r);
      live.textContent = 'Rolled ' + r.total + '. ' + describe(r);
      if (total.animate) {
        settleAnim = total.animate(reduce.matches
          ? [{ opacity: 0.4 }, { opacity: 1 }]
          : [{ transform: 'scale(0.86)', opacity: 0.6 }, { transform: 'scale(1.14)', opacity: 1, offset: 0.55 }, { transform: 'scale(1)' }],
        { duration: reduce.matches ? 140 : 300, easing: EASE_OUT });
      }
      sync();
    }).catch(function (err) {
      busy = false; cancelAnimationFrame(flickerRaf);
      total.classList.remove('is-rolling'); total.textContent = '!';
      detail.textContent = err && err.message ? err.message : 'Could not roll. Try again.';
      detail.classList.add('is-error');
      if (spin) spin.cancel();
      sync();
    }).then(function () { window.DICE_QUIET = false; });
  }

  grid.addEventListener('click', function (e) {
    var b = e.target.closest('.dw-die');
    if (b) roll(parseInt(b.dataset.sides, 10));
  });
  sync();
})();
