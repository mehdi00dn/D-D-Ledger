/* Account page: stop at the showcase limit by disabling the unchecked boxes, and say how many are picked. */
(function () {
  'use strict';
  var list = document.querySelector('.showcase-picker');
  if (!list) return;
  var max = parseInt(list.getAttribute('data-max'), 10) || 6;
  var label = document.querySelector('.showcase-count');
  function update() {
    var boxes = list.querySelectorAll('input[type=checkbox]');
    var picked = list.querySelectorAll('input:checked').length;
    boxes.forEach(function (b) { b.disabled = !b.checked && picked >= max; });
    if (label) label.textContent = picked + ' of ' + max + ' picked.';
  }
  list.addEventListener('change', update);
  update();
}());
