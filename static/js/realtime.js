/* Listens for "something changed" pings on Supabase Realtime and tells the page to refresh now.
 *
 * Speaks the small Phoenix-channel protocol Realtime uses over a WebSocket (no library needed, which also
 * keeps the CSP at script-src 'self').  The ping carries no data on purpose: the page re-reads the state
 * through its normal, permission-checked endpoints.  Polling keeps running underneath, so if this socket
 * cannot connect, drops, or is blocked, the page still updates -- just at polling speed.
 *
 *   var rt = LedgerRealtime.connect({url, key, channel}, onPing, onLive);
 *   rt.send({...})      // live, throwaway messages to everyone else on the channel (e.g. a token being dragged)
 *
 * "Live" messages are for looks only: they never change what is stored, and the saved state always wins.
 */
(function () {
  'use strict';

  function connect(cfg, onPing, onLive) {
    if (!cfg || !cfg.url || !cfg.key || !cfg.channel || typeof WebSocket === 'undefined') return { stop: function () {}, state: function () { return 'off'; }, send: function () { return false; } };
    var ws = null, heartbeat = null, retry = 0, ref = 0, stopped = false, state = 'connecting', timer = null;
    var topic = 'realtime:' + cfg.channel;

    function send(msg) {
      try { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); } catch (e) { /* socket closing */ }
    }

    function open() {
      if (stopped) return;
      state = 'connecting';
      var url = cfg.url.replace(/^http/, 'ws') + '/realtime/v1/websocket?apikey=' + encodeURIComponent(cfg.key) + '&vsn=1.0.0';
      try { ws = new WebSocket(url); } catch (e) { return later(); }

      ws.onopen = function () {
        send({
          topic: topic, event: 'phx_join', ref: String(++ref), join_ref: '1',
          payload: { config: { broadcast: { self: false, ack: false }, presence: { key: '' }, postgres_changes: [], private: false }, access_token: cfg.key }
        });
        clearInterval(heartbeat);
        heartbeat = setInterval(function () { send({ topic: 'phoenix', event: 'heartbeat', payload: {}, ref: String(++ref) }); }, 25000);
      };

      ws.onmessage = function (ev) {
        var m;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (!m) return;
        if (m.event === 'phx_reply' && m.topic === topic) {
          if (m.payload && m.payload.status === 'ok') { state = 'joined'; retry = 0; }
          else { state = 'rejected'; }
        } else if (m.event === 'broadcast' && m.topic === topic && m.payload && m.payload.event === 'changed') {
          try { onPing(m.payload.payload || {}); } catch (e) { /* a page handler must not kill the socket */ }
        } else if (m.event === 'broadcast' && m.topic === topic && m.payload && m.payload.event === 'live' && onLive) {
          try { onLive(m.payload.payload || {}); } catch (e) { /* ditto */ }
        }
      };

      ws.onclose = function () { clearInterval(heartbeat); if (!stopped) { state = 'reconnecting'; later(); } };
      ws.onerror = function () { try { ws.close(); } catch (e) { /* already closed */ } };
    }

    function later() {                                  // exponential back-off with jitter, capped at 30 s
      clearTimeout(timer);
      var wait = Math.min(30000, 1000 * Math.pow(2, retry++)) + Math.random() * 500;
      timer = setTimeout(open, wait);
    }

    function sendLive(payload) {                         // false when the socket is not joined (the message is simply dropped)
      if (!ws || ws.readyState !== 1 || state !== 'joined') return false;
      send({ topic: topic, event: 'broadcast', ref: String(++ref), payload: { type: 'broadcast', event: 'live', payload: payload } });
      return true;
    }

    open();
    return {
      send: sendLive,
      stop: function () { stopped = true; clearTimeout(timer); clearInterval(heartbeat); try { ws.close(); } catch (e) { /* ignore */ } },
      state: function () { return state; }
    };
  }

  window.LedgerRealtime = { connect: connect };
})();
