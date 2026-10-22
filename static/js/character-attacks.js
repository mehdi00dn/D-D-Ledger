/* Character form: add and remove rows in the Attacks card (DL-150).  Rows are plain inputs that post as
 * attack_name / attack_bonus / attack_damage / attack_type lists; the server cleans and validates them. */
(function () {
  'use strict';
  var list = document.querySelector('[data-attacks]');
  var tpl = document.querySelector('[data-attack-template]');
  var add = document.querySelector('[data-attack-add]');
  if (!list || !tpl || !add) return;
  add.addEventListener('click', function () {
    list.appendChild(tpl.content.cloneNode(true));
    var inputs = list.querySelectorAll('.atk-edit-row:last-child input');
    if (inputs[0]) inputs[0].focus();
  });
  list.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-attack-remove]');
    if (b) b.closest('.atk-edit-row').remove();
  });
})();
