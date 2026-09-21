/* Shared live-refresh scheduler for the battle tracker and the map editor.
 *
 * Replaces raw setInterval() polling.  Behaviour:
 *   - never polls while the tab is hidden; refreshes immediately when it is shown again
 *   - one request at a time (a slow response can never pile up overlapping requests)
 *   - backs off when nothing is changing (base -> up to `max`), and snaps back to `base`
 *     the moment something changes, the user touches the page, or the tab is re-shown
 *   - backs off exponentially after failures (server hiccup, offline) instead of hammering
 *
 * Usage:
 *   const poll = LedgerPoll.every(async () => {
 *     ...fetch + apply...
 *     return true;      // something changed          -> next poll at base speed
 *     return false;     // nothing changed             -> gradually slower
 *     return 'skip';    // user is mid-interaction     -> try again soon, not counted as idle
 *   }, { base: 3000, max: 12000 });
 *   poll.poke();        // e.g. after the user's own action: refresh soon, at base speed
 *   poll.stop();
 */
(function () {
  'use strict';

  function tuning(opts) {
    // Test hook: a page may define window.__LEDGER_POLL__ = {base, max, step} to speed things up.
    var t = window.__LEDGER_POLL__ || {};
    return {
      base: t.base || opts.base || 3000,
      max: t.max || opts.max || 12000,
      step: t.step || opts.step || 5,      // this many unchanged polls -> one 1.5x slow-down
      skip: t.skip || opts.skip || 1000    // retry delay when the task said 'skip'
    };
  }

  function every(task, opts) {
    var cfg = tuning(opts || {});
    var timer = null, running = false, stopped = false;
    var unchanged = 0, failures = 0, lastActivity = 0;

    function nextDelay() {
      if (failures) return Math.min(30000, cfg.base * Math.pow(2, failures));
      return Math.min(cfg.max, Math.round(cfg.base * Math.pow(1.5, Math.floor(unchanged / cfg.step))));
    }

    function schedule(ms) {
      clearTimeout(timer);
      timer = null;
      if (stopped || document.hidden) return;        // hidden tab: stay silent until visibilitychange
      timer = setTimeout(tick, ms === undefined ? nextDelay() : ms);
    }

    function tick() {
      timer = null;
      if (stopped || document.hidden || running) return;
      running = true;
      var delay;
      Promise.resolve()
        .then(task)
        .then(function (result) {
          failures = 0;
          if (result === 'skip') { delay = cfg.skip; return; }
          if (result) unchanged = 0; else unchanged += 1;
        })
        .catch(function () { failures = Math.min(failures + 1, 5); })
        .then(function () { running = false; schedule(delay); });
    }

    function poke() {                                 // refresh soon, at full speed
      unchanged = 0;
      failures = 0;
      if (!running) schedule(150);
    }

    function onVisibility() {
      if (document.hidden) { clearTimeout(timer); timer = null; return; }
      poke();                                         // just came back: catch up right away
    }

    function onActivity() {                           // user is active -> stay responsive
      var now = Date.now();
      if (now - lastActivity < 1000) return;
      lastActivity = now;
      if (unchanged > 0) {
        unchanged = 0;
        if (!running && !document.hidden && timer !== null) schedule(cfg.base);
      }
    }

    document.addEventListener('visibilitychange', onVisibility);
    document.addEventListener('pointerdown', onActivity, true);
    document.addEventListener('keydown', onActivity, true);

    schedule(cfg.base);

    return {
      poke: poke,
      stop: function () {
        stopped = true;
        clearTimeout(timer);
        document.removeEventListener('visibilitychange', onVisibility);
        document.removeEventListener('pointerdown', onActivity, true);
        document.removeEventListener('keydown', onActivity, true);
      },
      // exposed for tests / debugging
      state: function () { return { unchanged: unchanged, failures: failures, hidden: document.hidden, running: running, delay: nextDelay() }; }
    };
  }

  window.LedgerPoll = { every: every };
})();
