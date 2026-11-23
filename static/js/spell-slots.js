/* Spell slots (DL-185): the little rows of pips for a character's slots.
 *
 * A full pip is a slot that is still there, an empty one is spent.  Clicking a full pip spends a slot of that level, clicking an
 * empty one gives one back.  Whoever may not change them (not the DM, not the character's player) sees the pips without buttons.
 *
 *   SpellSlots.markup(slots, canEdit, who)   the HTML for the rows: slots is [{level, max, used}, ...]
 *   SpellSlots.send(characterId, action, level)   POST to the slots route; resolves to {slots, used_level} or rejects with a message
 *
 * The character details page binds itself: an element with data-slots (the pips), data-slots-url and data-slots-state (JSON), plus
 * any number of [data-cast-level] buttons in the spell list, which spend the lowest free slot of that level or higher. */
(function () {
  'use strict';

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  }

  function markup(slots, canEdit, who) {
    return '<div class="slot-groups">' + (slots || []).map(function (s) {
      var left = s.max - s.used, pips = '';
      for (var i = 0; i < s.max; i++) {
        var full = i < left;
        if (canEdit) {
          pips += '<button type="button" class="slot-pip' + (full ? ' is-full' : '') + '" data-action="slot-pip" data-slot-level="' + s.level +
            '" data-slot-do="' + (full ? 'spend' : 'restore') + '" aria-label="' + (full ? 'Spend' : 'Restore') + ' a level ' + s.level + ' slot' +
            (who ? ' for ' + esc(who) : '') + '" title="' + (full ? 'Spend a level ' + s.level + ' slot' : 'Restore a level ' + s.level + ' slot') + '"></button>';
        } else {
          pips += '<span class="slot-pip' + (full ? ' is-full' : '') + '" aria-hidden="true"></span>';
        }
      }
      return '<div class="slot-group" data-slot-level="' + s.level + '"><span class="slot-level" title="Level ' + s.level + ' spell slots">' + s.level +
        '</span><span class="slot-pips" role="group" aria-label="Level ' + s.level + ' slots: ' + left + ' of ' + s.max + ' left">' + pips + '</span></div>';
    }).join('') + '</div>';
  }

  function send(characterId, action, level) {
    var cid = window.CAMPAIGN_ID;
    return fetch('/campaigns/' + cid + '/api/characters/' + characterId + '/slots', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ action: action, level: level })
    }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (data) {
        if (!r.ok) { var e = new Error((data && data.error) || 'That did not work. Please try again.'); e.slots = data && data.slots; throw e; }
        return data;
      });
    });
  }

  window.SpellSlots = { markup: markup, send: send };

  // ---- the character details page ----
  var box = document.querySelector('[data-slots]');
  if (!box) return;
  var charId = box.getAttribute('data-character-id');
  var who = box.getAttribute('data-who') || '';
  var canEdit = box.getAttribute('data-can-edit') === '1';
  var status = document.querySelector('[data-slots-status]');
  var slots = [];
  try { slots = JSON.parse(box.getAttribute('data-slots-state') || '[]'); } catch (e) { slots = []; }
  function say(text) { if (status) status.textContent = text; }
  function draw() { box.innerHTML = markup(slots, canEdit, who); }
  draw();

  box.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-action="slot-pip"]');
    if (!b || b.disabled) return;
    b.disabled = true;
    var level = parseInt(b.getAttribute('data-slot-level'), 10);
    send(charId, b.getAttribute('data-slot-do'), level).then(function (out) {
      slots = out.slots; draw();
    }).catch(function (e) { say(e.message); b.disabled = false; });
  });

  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-cast-level]');
    if (!b || b.disabled) return;
    b.disabled = true;
    var spell = b.getAttribute('data-spell-name') || 'the spell';
    send(charId, 'cast', parseInt(b.getAttribute('data-cast-level'), 10)).then(function (out) {
      slots = out.slots; draw();
      say('Cast ' + spell + ' with a level ' + out.used_level + ' slot.');
    }).catch(function (e) {
      if (e.slots) { slots = e.slots; draw(); }
      say(e.message);
    }).then(function () { b.disabled = false; });
  });
})();
