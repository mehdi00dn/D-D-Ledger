/* Character form: the Spells card (DL-185).  Rows are plain inputs that post as spell_name / spell_level / spell_damage /
 * spell_type / spell_note lists; the server cleans and validates them.  "Add spell" adds an empty row.  "Add from spell
 * book" opens a search over the compendium's spells (the same public rules API the Compendium page uses); picking one adds
 * a row with its name, level, a first guess at the damage roll and a short note, all still editable.
 * The damage roll is checked as you type with the same rule as the server, like the attacks. */
(function () {
  'use strict';
  var card = document.querySelector('[data-spellbook]');
  if (!card) return;
  var list = card.querySelector('[data-spells]');
  var tpl = card.querySelector('[data-spell-template]');
  var addBtn = card.querySelector('[data-spell-add]');
  var openBtn = card.querySelector('[data-spellbook-open]');
  var panel = card.querySelector('[data-spellbook-panel]');
  var query = card.querySelector('[data-spellbook-query]');
  var results = card.querySelector('[data-spellbook-list]');
  var status = card.querySelector('[data-spellbook-status]');
  var base = card.getAttribute('data-base');
  var edition = card.getAttribute('data-edition');
  var MAX_SPELLS = 80;

  var SIDES = [4, 6, 8, 10, 12, 20, 100];
  function damageProblem(raw) {
    var text = String(raw || '').trim();
    if (!text) return '';
    var m = /^(\d{1,2})\s*d\s*(\d{1,3})\s*(?:([+-])\s*(\d{1,3}))?$/i.exec(text);
    if (!m) {
      if (/d.*d/i.test(text)) return 'One kind of die only, e.g. 8d6. Put extra dice in the note.';
      return 'Write it like 8d6 or 1d10+2: dice, then an optional bonus.';
    }
    if (SIDES.indexOf(parseInt(m[2], 10)) < 0) return 'd' + m[2] + ' is not a die here. Use d4, d6, d8, d10, d12, d20 or d100.';
    var count = parseInt(m[1], 10);
    if (count < 1 || count > 30) return 'Roll between 1 and 30 dice.';
    return '';
  }

  function check(input) {
    var row = input.closest('.spl-edit-row');
    if (!row) return true;
    var msg = damageProblem(input.value);
    var note = row.querySelector('.atk-error');
    if (msg && !note) {
      note = document.createElement('p');
      note.className = 'atk-error';
      note.setAttribute('role', 'alert');
      note.id = 'spl-err-' + Math.random().toString(36).slice(2, 8);
      row.appendChild(note);
    }
    if (note) { if (msg) note.textContent = msg; else note.remove(); }
    input.classList.toggle('is-invalid', !!msg);
    if (msg) { input.setAttribute('aria-invalid', 'true'); input.setAttribute('aria-describedby', note.id); }
    else { input.removeAttribute('aria-invalid'); input.removeAttribute('aria-describedby'); }
    input.setCustomValidity(msg);
    return !msg;
  }

  function rows() { return Array.prototype.slice.call(list.querySelectorAll('.spl-edit-row')); }
  function say(text) { if (status) status.textContent = text; }

  function newRow() {
    var frag = tpl.content.cloneNode(true);
    list.appendChild(frag);
    return list.lastElementChild;
  }
  function isBlank(row) { return !row.querySelector('[name="spell_name"]').value.trim(); }

  addBtn.addEventListener('click', function () {
    if (rows().length >= MAX_SPELLS) { say('A character can keep ' + MAX_SPELLS + ' spells.'); return; }
    var row = newRow();
    row.querySelector('input').focus();
  });
  list.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-spell-remove]');
    if (b) b.closest('.spl-edit-row').remove();
  });
  list.addEventListener('input', function (ev) {
    if (ev.target.name === 'spell_damage') check(ev.target);
  });
  var form = list.closest('form');
  if (form) form.addEventListener('submit', function (ev) {
    var bad = null;
    list.querySelectorAll('input[name="spell_damage"]').forEach(function (i) { if (!check(i) && !bad) bad = i; });
    if (bad) { ev.preventDefault(); bad.scrollIntoView({ block: 'center' }); bad.focus(); }
  });
  list.querySelectorAll('input[name="spell_damage"]').forEach(check);

  // ---- the spell book ----
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function getJson(url) {
    return fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (data) {
        if (!r.ok) throw new Error((data && data.error) || 'The spell book is unavailable right now.');
        return data;
      });
    });
  }
  var seq = 0, timer = null;
  function search() {
    var mine = ++seq;
    results.replaceChildren(el('li', 'ref-empty', 'Loading…'));
    getJson(base + 'search?kind=spells&q=' + encodeURIComponent(query.value.trim()) + '&edition=' + edition).then(function (found) {
      if (mine !== seq) return;
      results.replaceChildren();
      if (!found.length) results.append(el('li', 'ref-empty', 'No matches.'));
      found.forEach(function (row) {
        var li = el('li');
        var b = el('button', 'ref-item');
        b.type = 'button';
        b.setAttribute('data-ref', row.id);
        b.append(el('span', 'ref-item-name', row.name));
        if (row.sub) b.append(el('span', 'ref-item-sub', row.sub));
        li.append(b);
        results.append(li);
      });
    }).catch(function (e) {
      if (mine === seq) results.replaceChildren(el('li', 'ref-empty ref-error', e.message));
    });
  }

  function fill(row, s) {
    row.querySelector('[name="spell_name"]').value = s.name || '';
    row.querySelector('[name="spell_level"]').value = String(s.level || 0);
    row.querySelector('[name="spell_damage"]').value = s.damage || '';
    row.querySelector('[name="spell_type"]').value = s.damage_type || '';
    row.querySelector('[name="spell_note"]').value = s.note || '';
    check(row.querySelector('[name="spell_damage"]'));
  }

  results.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-ref]');
    if (!b || b.disabled) return;
    var name = b.querySelector('.ref-item-name').textContent;
    var have = rows().some(function (r) { return r.querySelector('[name="spell_name"]').value.trim().toLowerCase() === name.toLowerCase(); });
    if (have) { say(name + ' is already on the list.'); return; }
    if (rows().length >= MAX_SPELLS && !rows().some(isBlank)) { say('A character can keep ' + MAX_SPELLS + ' spells.'); return; }
    b.disabled = true;
    getJson(base + 'entry?kind=spells&ref=' + encodeURIComponent(b.getAttribute('data-ref')) + '&edition=' + edition).then(function (d) {
      var s = d && d.spell;
      if (!s) throw new Error('That spell could not be read.');
      var blank = rows().filter(isBlank)[0];
      var row = blank || newRow();
      fill(row, s);
      say('Added ' + s.name + '. Search again to add more.');
    }).catch(function (e) { say(e.message); }).then(function () { b.disabled = false; });
  });

  query.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(search, 180); });
  query.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') ev.preventDefault(); });    // Enter must not save the whole form
  card.querySelectorAll('[data-spellbook-edition]').forEach(function (b) {
    b.addEventListener('click', function () {
      edition = b.getAttribute('data-spellbook-edition');
      card.querySelectorAll('[data-spellbook-edition]').forEach(function (o) {
        var on = o === b;
        o.classList.toggle('active', on);
        o.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      search();
    });
  });
  openBtn.addEventListener('click', function () {
    var open = panel.hasAttribute('hidden');
    if (open) { panel.removeAttribute('hidden'); search(); query.focus(); } else { panel.setAttribute('hidden', ''); }
    openBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
})();
