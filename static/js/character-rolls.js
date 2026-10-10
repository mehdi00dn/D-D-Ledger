/* Click-to-roll on a character's details page.  Any element with data-roll-bonus (a skill, a save, an ability check,
 * initiative) rolls d20 + that bonus through LedgerDice, so everyone in the campaign sees it.
 * Shift-click rolls with advantage, Ctrl/Cmd/Alt-click with disadvantage; the Normal / Adv. / Dis. switch does the same
 * for touch screens, where there is no modifier key.  The bonus is read from the page and the server just adds it,
 * so this is a convenience: the server never treats it as a character fact.
 */
(function () {
  'use strict';
  var host = document.querySelector('[data-roll-character]');
  if (!host || !window.LedgerDice) return;
  var characterId = host.getAttribute('data-roll-character');

  function chosenMode(ev) {
    if (ev && ev.shiftKey) return 'advantage';
    if (ev && (ev.ctrlKey || ev.metaKey || ev.altKey)) return 'disadvantage';
    var on = document.querySelector('input[name="sheet-roll-mode"]:checked');
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
      character_id: characterId
    }).catch(function (err) { LedgerDice.announceError(err.message); })
      .then(function () { target.classList.remove('is-rolling'); });
  }

  document.addEventListener('click', function (ev) {
    var t = ev.target.closest('[data-roll-bonus]');
    if (t) fire(t, ev);
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    var t = ev.target.closest && ev.target.closest('[data-roll-bonus]');
    if (!t || t !== ev.target) return;
    ev.preventDefault();
    fire(t, ev);
  });
})();
