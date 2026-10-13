/* The dice drawings, shared by the corner widget and the Dice page: one outlined silhouette per die (a few facets,
 * the number inside).  LedgerDiceArt.svg(sides, {label}) returns an <svg> that takes its colour from `currentColor`;
 * a d6 can also carry pips (LedgerDiceArt.showPips) for a result.
 */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var SHAPES = {
    4:   { body: '16,3.5 29,27 3,27', facets: 'M16 3.5 L16 27' },
    6:   { body: 'M7 4.5h18a2.5 2.5 0 0 1 2.5 2.5v18a2.5 2.5 0 0 1-2.5 2.5H7A2.5 2.5 0 0 1 4.5 25V7A2.5 2.5 0 0 1 7 4.5z', facets: '' },
    8:   { body: '16,3 28.5,16 16,29 3.5,16', facets: 'M3.5 16 H28.5' },
    10:  { body: '16,3 28.5,13 16,29 3.5,13', facets: 'M3.5 13 L16 18 L28.5 13 M16 3 L16 18' },
    12:  { body: '16,3 28.5,12 23.7,27 8.3,27 3.5,12', facets: 'M16 3 L16 10 M3.5 12 L10 14.5 M28.5 12 L22 14.5' },
    20:  { body: '16,3 27.3,9.5 27.3,22.5 16,29 4.7,22.5 4.7,9.5', facets: 'M16 3 L4.7 22.5 M16 3 L27.3 22.5 M4.7 22.5 H27.3' },
    100: { circle: true, facets: 'M5 16 a11 11 0 0 1 22 0' }
  };
  var PIPS = { 1: [5], 2: [1, 9], 3: [1, 5, 9], 4: [1, 3, 7, 9], 5: [1, 3, 5, 7, 9], 6: [1, 3, 4, 6, 7, 9] };

  function el(tag, attrs) {
    var n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }

  function svg(sides, opts) {
    opts = opts || {};
    var def = SHAPES[sides];
    var s = el('svg', { viewBox: '0 0 32 32', 'aria-hidden': 'true', focusable: 'false', 'class': 'dw-art' });
    s.appendChild(def.circle ? el('circle', { cx: 16, cy: 16, r: 12.5, 'class': 'dw-body' })
      : (def.body.charAt(0) === 'M' ? el('path', { d: def.body, 'class': 'dw-body' }) : el('polygon', { points: def.body, 'class': 'dw-body' })));
    if (def.facets) s.appendChild(el('path', { d: def.facets, 'class': 'dw-facet' }));
    if (opts.label !== false) {
      var t = el('text', { x: 16, y: sides === 4 ? 21.5 : 20, 'text-anchor': 'middle', 'class': 'dw-num' });
      t.textContent = opts.text !== undefined ? String(opts.text) : (sides === 100 ? '%' : String(sides));
      s.appendChild(t);
    }
    return s;
  }

  function setNumber(svgEl, value) {
    var pips = svgEl.querySelector('.dw-pips');
    if (pips) pips.remove();
    var t = svgEl.querySelector('.dw-num');
    if (t) { t.style.display = ''; t.textContent = String(value); t.setAttribute('font-size', String(value).length > 2 ? '8' : ''); }
  }

  /* A d6 result shown as pips instead of a number. */
  function showPips(svgEl, value) {
    var t = svgEl.querySelector('.dw-num');
    if (t) t.style.display = 'none';
    var old = svgEl.querySelector('.dw-pips'); if (old) old.remove();
    var g = el('g', { 'class': 'dw-pips' });
    (PIPS[value] || []).forEach(function (cell) {
      var col = (cell - 1) % 3, row = Math.floor((cell - 1) / 3);
      g.appendChild(el('circle', { cx: 10.5 + col * 5.5, cy: 10.5 + row * 5.5, r: 1.9 }));
    });
    svgEl.appendChild(g);
  }

  window.LedgerDiceArt = { svg: svg, setNumber: setNumber, showPips: showPips, SIDES: [4, 6, 8, 10, 12, 20, 100] };
})();
