/* Character form: the Spell slots card (DL-185).  One stepper per spell level: minus, the count, plus.  The count is a real number
 * input (so typing, the arrow keys and the form post all work); the buttons just move it by one, never below 0 or above 9.  The row of
 * small gems under it mirrors the count, and a level with slots gets a stronger frame. */
(function () {
  'use strict';
  var grid = document.querySelector('[data-slot-steps]');
  if (!grid) return;
  var MAX = 9;

  function clamp(n) { return isNaN(n) ? 0 : Math.max(0, Math.min(MAX, n)); }

  function sync(cell) {
    var input = cell.querySelector('.slot-step-val');
    var n = clamp(parseInt(input.value, 10));
    if (String(n) !== input.value && document.activeElement !== input) input.value = String(n);
    cell.classList.toggle('has-slots', n > 0);
    cell.querySelector('[data-step="-1"]').disabled = n <= 0;
    cell.querySelector('[data-step="1"]').disabled = n >= MAX;
    var pips = cell.querySelector('[data-step-pips]');
    while (pips.children.length > n) pips.lastElementChild.remove();
    while (pips.children.length < n) pips.appendChild(document.createElement('i'));
  }

  grid.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-step]');
    if (!b || b.disabled) return;
    var cell = b.closest('[data-slot-step]');
    var input = cell.querySelector('.slot-step-val');
    input.value = String(clamp(clamp(parseInt(input.value, 10)) + parseInt(b.getAttribute('data-step'), 10)));
    sync(cell);
  });
  grid.addEventListener('input', function (ev) { var c = ev.target.closest('[data-slot-step]'); if (c) sync(c); });
  grid.addEventListener('change', function (ev) {
    var c = ev.target.closest('[data-slot-step]');
    if (!c) return;
    var input = c.querySelector('.slot-step-val');
    input.value = String(clamp(parseInt(input.value, 10)));
    sync(c);
  });
  grid.querySelectorAll('[data-slot-step]').forEach(sync);
})();
