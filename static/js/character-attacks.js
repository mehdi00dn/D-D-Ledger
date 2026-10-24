/* Character form: add and remove rows in the Attacks card (DL-150).  Rows are plain inputs that post as
 * attack_name / attack_bonus / attack_damage / attack_type lists; the server cleans and validates them.
 * The damage roll is checked as you type with the same rule as the server (dnd5e.parse_damage), so a roll the
 * server would drop shows a message instead of silently vanishing on save. */
(function () {
  'use strict';
  var list = document.querySelector('[data-attacks]');
  var tpl = document.querySelector('[data-attack-template]');
  var add = document.querySelector('[data-attack-add]');
  if (!list || !tpl || !add) return;

  var SIDES = [4, 6, 8, 10, 12, 20, 100];
  var MAX_DICE = 30;

  // '' when the roll is fine (an empty roll is fine too), otherwise a short message saying what to change.
  function damageProblem(raw) {
    var text = String(raw || '').trim();
    if (!text) return '';
    var m = /^(\d{1,2})\s*d\s*(\d{1,3})\s*(?:([+-])\s*(\d{1,3}))?$/i.exec(text);
    if (!m) {
      if (/d.*d/i.test(text)) return 'One kind of die only, e.g. 1d8+3. Put extra dice in the notes.';
      return 'Write it like 1d8+3: dice, then an optional bonus.';
    }
    var count = parseInt(m[1], 10), sides = parseInt(m[2], 10);
    if (SIDES.indexOf(sides) < 0) return 'd' + sides + ' is not a die here. Use d4, d6, d8, d10, d12, d20 or d100.';
    if (count < 1 || count > MAX_DICE) return 'Roll between 1 and ' + MAX_DICE + ' dice.';
    return '';
  }

  function check(input) {
    var row = input.closest('.atk-edit-row');
    if (!row) return true;
    var msg = damageProblem(input.value);
    var note = row.querySelector('.atk-error');
    if (msg && !note) {
      note = document.createElement('p');
      note.className = 'atk-error';
      note.setAttribute('role', 'alert');
      note.id = 'atk-err-' + Math.random().toString(36).slice(2, 8);
      row.appendChild(note);
    }
    if (note) {
      if (msg) note.textContent = msg; else note.remove();
    }
    input.classList.toggle('is-invalid', !!msg);
    if (msg) {
      input.setAttribute('aria-invalid', 'true');
      input.setAttribute('aria-describedby', note.id);
    } else {
      input.removeAttribute('aria-invalid');
      input.removeAttribute('aria-describedby');
    }
    input.setCustomValidity(msg);
    return !msg;
  }

  add.addEventListener('click', function () {
    list.appendChild(tpl.content.cloneNode(true));
    var inputs = list.querySelectorAll('.atk-edit-row:last-child input');
    if (inputs[0]) inputs[0].focus();
  });
  list.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-attack-remove]');
    if (b) b.closest('.atk-edit-row').remove();
  });
  list.addEventListener('input', function (ev) {
    if (ev.target.name === 'attack_damage') check(ev.target);
  });

  // Saving with a roll the server would drop: stop and point at it.
  var form = list.closest('form');
  if (form) form.addEventListener('submit', function (ev) {
    var bad = null;
    list.querySelectorAll('input[name="attack_damage"]').forEach(function (i) { if (!check(i) && !bad) bad = i; });
    if (bad) {
      ev.preventDefault();
      bad.scrollIntoView({ block: 'center' });
      bad.focus();
    }
  });
  list.querySelectorAll('input[name="attack_damage"]').forEach(check);
})();
