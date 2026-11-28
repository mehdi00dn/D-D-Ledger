/* The Journal list: filter the rows the page already holds as you type, hiding months that end up empty. */
(function () {
  'use strict';
  var input = document.getElementById('journal-search');
  if (!input) return;
  var rows = Array.prototype.slice.call(document.querySelectorAll('.journal-row'));
  var months = Array.prototype.slice.call(document.querySelectorAll('.journal-month'));
  var count = document.getElementById('journal-count');
  var none = document.getElementById('journal-no-matches');
  var total = rows.length;

  function apply() {
    var q = input.value.trim().toLowerCase(), shown = 0;
    rows.forEach(function (row) {
      var hit = !q || row.getAttribute('data-search').indexOf(q) !== -1;
      row.hidden = !hit;
      if (hit) shown++;
    });
    months.forEach(function (m) { m.hidden = !m.querySelector('.journal-row:not([hidden])'); });
    count.textContent = q ? shown + ' of ' + total + ' sessions' : total + ' sessions';
    none.hidden = shown > 0;
  }
  input.addEventListener('input', apply);
})();
