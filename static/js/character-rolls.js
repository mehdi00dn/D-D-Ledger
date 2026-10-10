/* Click-to-roll for a character's numbers.  Any element with data-roll-bonus (a skill, a save, an ability check,
 * initiative) rolls d20 + that bonus through LedgerDice, so everyone in the campaign sees it.
 * Shift-click rolls with advantage, Ctrl/Cmd/Alt-click with disadvantage; a Normal / Adv. / Dis. switch does the same
 * for touch screens, where there is no modifier key.  The bonus is read from the page and the server just adds it,
 * so this is a convenience: the server never treats it as a character fact.
 *
 *   CharacterRolls.bind(root, getCharacterId, modeInputName)
 *       root: the element whose [data-roll-bonus] descendants roll (the document, or a popup body)
 *       getCharacterId: function returning the id of the character being shown right now
 *       modeInputName: name of the radio group holding the Normal / Adv. / Dis. choice
 *
 * The character details page binds itself (an element with data-roll-character); the map and battle popups call bind().
 */
(function () {
  'use strict';
  if (!window.LedgerDice) return;

  function bind(root, getCharacterId, modeName) {
    function chosenMode(ev) {
      if (ev && ev.shiftKey) return 'advantage';
      if (ev && (ev.ctrlKey || ev.metaKey || ev.altKey)) return 'disadvantage';
      var on = document.querySelector('input[name="' + modeName + '"]:checked');
      return on ? on.value : 'normal';
    }

    function fire(target, ev) {
      if (target.classList.contains('is-rolling')) return;
      target.classList.add('is-rolling');
      LedgerDice.roll({
        dice: { '20': 1 },
        modifier: parseInt(target.getAttribute('data-roll-bonus'), 10) || 0,
        label: target.getAttribute('data-roll-label') || '',
        mode: chosenMode(ev),
        character_id: getCharacterId()
      }).catch(function (err) { LedgerDice.announceError(err.message); })
        .then(function () { target.classList.remove('is-rolling'); });
    }

    root.addEventListener('click', function (ev) {
      var t = ev.target.closest('[data-roll-bonus]');
      if (t && root.contains(t)) fire(t, ev);
    });
    root.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      var t = ev.target.closest && ev.target.closest('[data-roll-bonus]');
      if (!t || t !== ev.target) return;
      ev.preventDefault();
      fire(t, ev);
    });
  }

  window.CharacterRolls = { bind: bind };

  var host = document.querySelector('[data-roll-character]');
  if (host) bind(document, function () { return host.getAttribute('data-roll-character'); }, 'sheet-roll-mode');
})();
