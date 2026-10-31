/* DL-172 (backlog DL-140): the page's Content-Security-Policy no longer allows style="..." attributes, so the few styles that
   depend on data (a faction colour, an HP bar's fill, a map pin's place) arrive as small data-* attributes and are applied here
   through the style object, which the policy allows.  Every value is checked first: only hex colours, var(--token) colours and
   plain numbers get through, so text that slipped into the page can never become arbitrary CSS.
     data-bg / data-bc    colour for background / border-colour
     data-grad            colour for the generated faction banner gradient
     data-p               a number 0-1 (the HP bar fill, --p)
     data-left, data-top, data-width, data-height   percentages
     data-rot             degrees
     data-hue             a number (--cover-hue)
     data-aspect          "w/h" (aspect-ratio)
   New nodes are handled as they appear (the battle and map screens re-draw their rows and pins all the time). */
(function () {
  'use strict';
  var HEX = /^#[0-9a-fA-F]{3,8}$/, VAR = /^var\(--[a-z0-9-]+\)$/i, NUM = /^-?\d+(\.\d+)?$/, RATIO = /^\d+(\.\d+)?\/\d+(\.\d+)?$/;
  var SEL = '[data-bg],[data-bc],[data-grad],[data-p],[data-left],[data-top],[data-width],[data-height],[data-rot],[data-hue],[data-aspect]';
  function color(v) { return v && (HEX.test(v) || VAR.test(v)) ? v : ''; }
  function num(v) { return v !== undefined && NUM.test(v) ? v : ''; }
  function apply(el) {
    var d = el.dataset, s = el.style, v;
    if ((v = color(d.bg))) s.background = v;
    if ((v = color(d.bc))) s.borderColor = v;
    if ((v = color(d.grad))) s.background = 'linear-gradient(135deg, ' + v + ', var(--bg-panel-inset))';
    if ((v = num(d.p))) s.setProperty('--p', v);
    if ((v = num(d.left))) s.left = v + '%';
    if ((v = num(d.top))) s.top = v + '%';
    if ((v = num(d.width))) s.width = v + '%';
    if ((v = num(d.height))) s.height = v + '%';
    if ((v = num(d.rot))) s.transform = 'rotate(' + v + 'deg)';
    if ((v = num(d.hue))) s.setProperty('--cover-hue', v);
    if (d.aspect !== undefined && RATIO.test(d.aspect)) s.aspectRatio = d.aspect.replace('/', ' / ');
  }
  function scan(node) {
    if (node.nodeType !== 1) return;
    if (node.matches(SEL)) apply(node);
    var all = node.querySelectorAll(SEL);
    for (var i = 0; i < all.length; i++) apply(all[i]);
  }
  window.ledgerApplyDataStyles = scan;
  new MutationObserver(function (list) {
    for (var i = 0; i < list.length; i++) {
      var added = list[i].addedNodes;
      for (var j = 0; j < added.length; j++) scan(added[j]);
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
  scan(document.documentElement);
})();
