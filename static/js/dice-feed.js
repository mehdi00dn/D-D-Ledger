/* Shared dice: rolls are made on the server, so everyone in the campaign sees the same result.
 *
 *   LedgerDice.roll({dice: {'20': 1}, modifier: 5, label: 'Stealth', mode: 'advantage', character_id: 7, private: false})
 *       -> Promise of the stored roll (rejects with an Error whose message is fit to show)
 *   LedgerDice.on(fn)    // fn(roll) for every roll this page learns about (own and other people's)
 *
 * Every campaign page runs this: it polls the roll feed (and wakes up early on a Realtime "dice" ping) and shows
 * each new roll as a small toast in the corner, whatever page the person is on.  Opening a page never replays
 * old rolls.  On the Dice page your own roll is not toasted: its big animated result is already on screen.
 */
(function () {
  'use strict';
  var cid = window.CAMPAIGN_ID;
  if (!cid || !window.fetch) return;
  var base = '/campaigns/' + cid + '/api/dice/';
  var latest = null, seen = {}, listeners = [], holder = null;
  var SHOW_MS = 8000, MAX_TOASTS = 4;

  function node(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function signed(n) { return (n >= 0 ? '+' : '−') + Math.abs(n); }

  function container() {
    if (!holder) {
      holder = node('div', 'dice-toasts');
      holder.setAttribute('role', 'log');
      holder.setAttribute('aria-live', 'polite');
      holder.setAttribute('aria-label', 'Dice rolls');
      document.body.appendChild(holder);
    }
    return holder;
  }

  /* Small, reusable: the dice of a roll as chips, e.g. "d20 14", "d20 3 (dropped)", "+5". */
  function diceChips(roll) {
    var wrap = node('span', 'roll-chips');
    roll.dice.forEach(function (d) {
      var chip = node('span', 'roll-chip' + (d.dropped ? ' is-dropped' : ''), 'd' + d.sides + ' ' + d.value);
      if (d.dropped) chip.title = 'Dropped (' + roll.mode + ')';
      wrap.appendChild(chip);
    });
    if (roll.modifier) wrap.appendChild(node('span', 'roll-chip roll-chip--mod', signed(roll.modifier)));
    return wrap;
  }

  function isNat(roll) {
    var kept = roll.dice.filter(function (d) { return !d.dropped; });
    if (kept.length !== 1 || kept[0].sides !== 20) return '';
    return kept[0].value === 20 ? 'is-crit' : (kept[0].value === 1 ? 'is-fumble' : '');
  }

  function toast(roll) {
    var box = container();
    var t = node('div', 'dice-toast ' + isNat(roll) + (roll.private ? ' is-private' : ''));
    var head = node('div', 'dice-toast-head');
    head.appendChild(node('strong', '', roll.character || roll.who));
    var what = roll.label || 'Roll';
    if (roll.mode !== 'normal') what += ' (' + roll.mode + ')';
    head.appendChild(node('span', 'dice-toast-what', what));
    if (roll.character) head.appendChild(node('span', 'dice-toast-by', 'by ' + roll.who));
    if (roll.private) head.appendChild(node('span', 'dice-toast-tag', 'DM only'));
    t.appendChild(head);
    var body = node('div', 'dice-toast-body');
    body.appendChild(node('span', 'dice-toast-total', String(roll.total)));
    body.appendChild(diceChips(roll));
    t.appendChild(body);
    var close = node('button', 'dice-toast-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', 'Dismiss');
    t.appendChild(close);
    function gone() { if (t.parentNode) t.parentNode.removeChild(t); }
    close.addEventListener('click', gone);
    var timer = setTimeout(gone, SHOW_MS);
    t.addEventListener('mouseenter', function () { clearTimeout(timer); });
    t.addEventListener('mouseleave', function () { timer = setTimeout(gone, 2500); });
    box.appendChild(t);
    while (box.children.length > MAX_TOASTS) box.removeChild(box.firstChild);
  }

  function learn(roll) {
    if (seen[roll.id]) return;
    seen[roll.id] = true;
    if (latest === null || roll.id > latest) latest = roll.id;
    listeners.forEach(function (fn) { try { fn(roll); } catch (e) { /* a listener must not break the feed */ } });
    if (roll.mine && window.DICE_PAGE) return;
    toast(roll);
  }

  function readJson(r) {
    return r.json().catch(function () { return {}; }).then(function (body) {
      if (!r.ok) throw new Error(body.error || 'Could not roll. Try again.');
      return body;
    });
  }

  function roll(spec) {
    return fetch(base + 'roll', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(spec) })
      .then(readJson)
      .then(function (r) { learn(r); return r; });
  }

  function check() {
    var url = latest === null ? base + 'rolls?init=1' : base + 'rolls?after=' + latest;
    return fetch(url, { credentials: 'same-origin' }).then(readJson).then(function (body) {
      var first = latest === null;
      if (first) latest = body.latest || 0;
      (body.rolls || []).forEach(learn);
      return !first && (body.rolls || []).length > 0;
    });
  }

  var poll = window.LedgerPoll ? LedgerPoll.every(check, { base: 4000, max: window.LEDGER_REALTIME ? 30000 : 15000 }) : null;   // a Realtime ping wakes it sooner
  if (poll) check();                                           // learn the newest id right away, so nothing old is replayed
  if (window.LEDGER_REALTIME && window.LedgerRealtime && poll) {
    LedgerRealtime.connect(window.LEDGER_REALTIME, function (p) { if (p.s === 'dice') poll.poke(); });
  }

  window.LedgerDice = {
    roll: roll,
    on: function (fn) { listeners.push(fn); },
    chips: diceChips,
    recent: function () { return fetch(base + 'rolls', { credentials: 'same-origin' }).then(readJson).then(function (b) { return b.rolls || []; }); },
    announceError: function (message) { toast({ id: 0, who: 'Dice', label: message, dice: [], modifier: 0, total: '!', mode: 'normal', private: false, mine: false }); }  // shown as a toast so it works on any page
  };
})();
