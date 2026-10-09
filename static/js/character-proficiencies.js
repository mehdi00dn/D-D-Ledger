/* Live bonus preview on the character form: shows each save's and skill's bonus as the scores, level and
   proficiency choices change.  Pure display -- the server derives the real numbers from what is saved. */
(function () {
  'use strict';
  var root = document.querySelector('[data-proficiencies]');
  if (!root) return;
  var form = root.closest('form');
  var ABILITIES = ['str', 'dex', 'con', 'int', 'wis', 'cha'];

  function num(id, fallback) {
    var el = document.getElementById(id);
    var v = el ? parseInt(el.value, 10) : NaN;
    return isNaN(v) ? fallback : v;
  }
  function signed(n) { return n >= 0 ? '+' + n : String(n); }
  function mod(score) { return Math.floor((score - 10) / 2); }
  function profBonus(level) { return 2 + Math.floor((Math.max(1, Math.min(level, 20)) - 1) / 4); }

  function refresh() {
    var prof = profBonus(num('level', 1));
    var mods = {};
    ABILITIES.forEach(function (a) {
      mods[a] = mod(num(a + '_score', 10));
      var m = root.querySelector('[data-mod="' + a + '"]');
      if (m) m.textContent = signed(mods[a]);
    });
    root.querySelectorAll('[data-save]').forEach(function (box) {
      var out = root.querySelector('[data-save-bonus="' + box.dataset.save + '"]');
      if (out) out.textContent = signed(mods[box.dataset.save] + (box.checked ? prof : 0));
    });
    var trained = 0;
    document.querySelectorAll('[data-skill-bonus]').forEach(function (out) {
      var pick = document.querySelector('input[data-skill="' + out.dataset.skillBonus + '"]:checked');
      var lvl = pick ? (parseInt(pick.value, 10) || 0) : 0;
      out.textContent = signed(mods[out.dataset.ability] + prof * lvl);
      out.classList.toggle('is-prof', lvl > 0);
      var row = out.closest('.skill-row');
      if (row) row.classList.toggle('is-trained', lvl > 0);
      if (lvl > 0) trained += 1;
    });
    var counter = document.querySelector('[data-skill-count]');
    if (counter) counter.textContent = trained + ' trained';
  }

  form.addEventListener('input', refresh);
  form.addEventListener('change', refresh);
  refresh();
})();
