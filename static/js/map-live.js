/* Live movement on the map: everyone else sees a token, shape, drawing or fog stroke move AS it moves.
 *
 * The person moving something sends small "live" messages (about ten a second) over the campaign's Realtime
 * channel; everyone else eases the item toward each new position so it glides instead of stepping.  These
 * messages are for looks only -- nothing here stores anything.  When the gesture ends the normal save runs, the
 * server pings, and every page re-reads the saved state, which always wins over what was shown live.
 *
 * Because the channel is shared, every incoming message is treated as untrusted text: numbers are checked,
 * colours are validated, sizes are capped, only items this page already has are touched, and an item that stops
 * receiving updates falls back to its saved position after a couple of seconds.
 *
 *   var live = MapLive.create(host);   live.attach(realtimeConnection);   realtimeConnection -> live.receive(msg)
 */
(function () {
  'use strict';

  var SEND_EVERY = 100;      // ms between messages while dragging (about 10 a second -- kind to the Realtime rate limits)
  var EASE_MS = 70;          // how quickly the receiving side catches up with the newest position
  var STALE_MS = 2500;       // a live item that goes quiet this long snaps back to its saved state
  var MAX_ITEMS = 200, MAX_POINTS = 200, MAX_PEN = 4000;

  function num(v, fallback) { v = Number(v); return isFinite(v) ? v : fallback; }
  function now() { return (window.performance && performance.now) ? performance.now() : Date.now(); }

  function create(host) {
    var uid = Math.random().toString(36).slice(2, 10);
    var rt = null;

    // ------------------------------------------------------------------ sending
    var lastSent = {}, timers = {}, pending = {};
    function raw(msg) { if (!rt) return false; msg.m = host.mapId; msg.u = uid; return rt.send(msg); }

    // Leading + trailing throttle per kind: the first update goes out at once, later ones are batched so the
    // newest state is always the one sent.  `build` runs at send time so it reads the latest values.
    function throttled(key, build) {
      pending[key] = build;
      if (timers[key]) return;
      var wait = Math.max(0, SEND_EVERY - (now() - (lastSent[key] || 0)));
      timers[key] = setTimeout(function () {
        timers[key] = 0;
        var b = pending[key]; pending[key] = null;
        if (!b) return;
        lastSent[key] = now();
        var msg = b();
        if (msg) raw(msg);
      }, wait);
    }
    function flush(key) {                 // an end-of-gesture message must not be overtaken by a late "still moving" one
      clearTimeout(timers[key]); timers[key] = 0; pending[key] = null;
    }

    function pinItem(p) { return { id: p.id, x: p.x, y: p.y, s: p.scale || 1, r: p.rotation || 0 }; }
    function shapeItem(d) {
      var it = { id: d.id, cx: d.cx, cy: d.cy, w: d.w, h: d.h, r: d.rotation || 0 };
      if (d.data && d.kind !== 'pen') {   // small shapes carry their data (angle sweep, line ends); a pen's point list never moves
        try { var j = JSON.stringify(d.data); if (j.length < 400) it.data = d.data; } catch (e) { /* skip */ }
      }
      return it;
    }

    var dragKind = null;                  // 'pins' | 'shapes' while this page is sending a drag
    function itemsForDrag(drag) {
      switch (drag.type) {
        case 'move-pin': case 'resize-pin': case 'rotate-pin': {
          var p = host.pins().find(function (x) { return x.id === drag.pin.id; });
          return p ? { k: 'pins', items: [pinItem(p)] } : null;
        }
        case 'group-move-shapes': {
          var all = host.drawings(), items = [];
          drag.ids.forEach(function (id) { var d = all.find(function (x) { return x.id === id; }); if (d) items.push(shapeItem(d)); });
          return items.length ? { k: 'shapes', items: items } : null;
        }
        case 'resize-shape': case 'rotate-shape': case 'resize-shape-uniform': case 'resize-angle-radius': case 'adjust-angle-sweep':
          return drag.d ? { k: 'shapes', items: [shapeItem(drag.d)] } : null;
        default: return null;
      }
    }

    // ---- a pin / shape / group being dragged
    function dragMoved(drag) {
      if (!rt || !drag) return;
      var probe = itemsForDrag(drag);
      if (!probe) return;
      dragKind = probe.k;
      throttled(probe.k, function () { var m = itemsForDrag(drag); return m ? { k: m.k, items: m.items } : null; });
    }
    function dragEnded(drag) {
      if (!rt || !drag || !dragKind) return;
      var kind = dragKind; dragKind = null;
      flush(kind);
      var m = itemsForDrag(drag);
      if (m) raw({ k: m.k, end: 1, items: m.items });
    }

    // ---- a brand-new shape being drawn
    var penSent = 0, drafting = false;
    function draftMoved(shape, tool, points) {
      if (!rt || !shape) return;
      drafting = true;
      throttled('draft', function () {
        if (tool === 'pen' && points) {
          var from = penSent, pts = points.slice(from, from + MAX_POINTS);
          penSent = from + pts.length;
          return { k: 'draft', kind: 'pen', from: from, pts: pts, color: shape.color };
        }
        return { k: 'draft', kind: shape.kind, cx: shape.cx, cy: shape.cy, w: shape.w, h: shape.h, r: shape.rotation || 0,
                 data: shape.data || null, color: shape.color, fill: shape.fill ? 1 : 0, fo: shape.fill_opacity };
      });
    }
    function draftEnded() {
      if (!rt || !drafting) return;
      drafting = false; penSent = 0;
      flush('draft'); delete lastSent.draft;
      raw({ k: 'draft', end: 1 });
    }

    // ---- fog being painted or erased
    var fogBuf = [], fogOpts = null, fogOn = false;
    function fogMoved(mode, brush, x, y) {
      if (!rt) return;
      fogOn = true; fogOpts = { mode: mode, brush: brush };
      fogBuf.push([Math.round(x), Math.round(y)]);
      throttled('fog', function () {
        if (!fogBuf.length) return null;
        var pts = fogBuf.slice(0, MAX_POINTS); fogBuf = fogBuf.slice(MAX_POINTS);
        return { k: 'fog', mode: fogOpts.mode, brush: fogOpts.brush, pts: pts };
      });
    }
    function fogEnded() {
      if (!rt || !fogOn) return;
      fogOn = false;
      flush('fog'); delete lastSent.fog;
      while (fogBuf.length && fogOpts) { raw({ k: 'fog', mode: fogOpts.mode, brush: fogOpts.brush, pts: fogBuf.slice(0, MAX_POINTS) }); fogBuf = fogBuf.slice(MAX_POINTS); }
      fogBuf = [];
      raw({ k: 'fog', end: 1 });
    }

    // ------------------------------------------------------------------ receiving
    var livePins = new Map();      // id -> {cur:{x,y,s,r}, tgt:{...}, at, ending, from}
    var liveShapes = new Map();    // id -> {cur:{cx,cy,w,h,r}, tgt, data, at, ending}
    var drafts = new Map();        // sender uid -> {shape, pts, at, ending}
    var raf = 0, lastTick = 0;

    function ensureTick() { if (!raf) { lastTick = now(); raf = requestAnimationFrame(tick); } }

    function ease(entry, dt, keys) {
      var k = 1 - Math.exp(-dt / EASE_MS), settled = true;
      keys.forEach(function (key) {
        var d = entry.tgt[key] - entry.cur[key];
        if (Math.abs(d) < 0.25) entry.cur[key] = entry.tgt[key];
        else { entry.cur[key] += d * k; settled = false; }
      });
      return settled;
    }

    function tick() {
      raf = 0;
      var t = now(), dt = Math.min(100, Math.max(1, t - lastTick)); lastTick = t;
      var busy = false, shapesTouched = false;

      livePins.forEach(function (e, id) {
        var done = ease(e, dt, ['x', 'y', 's', 'r']);
        var el = host.pinEl(id);
        if (e.ending && done) { commitPin(id, e); livePins.delete(id); return; }
        if (!e.ending && t - e.at > STALE_MS) { livePins.delete(id); host.restorePin(id); return; }
        if (el) host.stylePin(id, el, { x: e.cur.x, y: e.cur.y, scale: e.cur.s, rotation: e.cur.r });
        busy = true;
      });

      liveShapes.forEach(function (e, id) {
        var done = ease(e, dt, ['cx', 'cy', 'w', 'h', 'r']);
        shapesTouched = true;
        if (e.ending && done) { commitShape(id, e); liveShapes.delete(id); return; }
        if (!e.ending && t - e.at > STALE_MS) { liveShapes.delete(id); return; }
        busy = true;
      });

      drafts.forEach(function (d, id) {
        if (t - d.at > (d.ending ? 1500 : STALE_MS)) { drafts.delete(id); shapesTouched = true; }
        else if (!d.ending) busy = true;
      });

      if (shapesTouched || drafts.size) host.redraw();
      if (busy || livePins.size || liveShapes.size) ensureTick();
    }

    function commitPin(id, e) {            // the gesture ended: keep the final spot until the saved state confirms it
      var p = host.pins().find(function (x) { return x.id === id; });
      if (p) { p.x = e.tgt.x; p.y = e.tgt.y; p.scale = e.tgt.s; p.rotation = e.tgt.r; }
      var el = host.pinEl(id);
      if (el && p) host.stylePin(id, el, p);
    }
    function commitShape(id, e) {
      var d = host.drawings().find(function (x) { return x.id === id; });
      if (!d) return;
      d.cx = e.tgt.cx; d.cy = e.tgt.cy; d.w = e.tgt.w; d.h = e.tgt.h; d.rotation = e.tgt.r;
      if (e.data) d.data = e.data;
    }

    function receivePins(msg) {
      var items = Array.isArray(msg.items) ? msg.items.slice(0, MAX_ITEMS) : [];
      var local = host.pins(), t = now(), any = false;
      items.forEach(function (it) {
        var id = num(it && it.id, null); if (id == null) return;
        var pin = local.find(function (x) { return x.id === id; });
        if (!pin || host.draggingPin(id)) return;          // unknown to this page, or this person is moving it themselves
        var tgt = { x: num(it.x, pin.x), y: num(it.y, pin.y), s: Math.max(0.3, Math.min(5, num(it.s, pin.scale || 1))), r: num(it.r, pin.rotation || 0) };
        var e = livePins.get(id);
        if (!e) e = { cur: { x: pin.x, y: pin.y, s: pin.scale || 1, r: pin.rotation || 0 }, tgt: tgt, at: t, ending: false };
        e.tgt = tgt; e.at = t; e.ending = !!msg.end;
        livePins.set(id, e); any = true;
      });
      if (any) ensureTick();
    }

    function receiveShapes(msg) {
      var items = Array.isArray(msg.items) ? msg.items.slice(0, MAX_ITEMS) : [];
      var local = host.drawings(), t = now(), any = false;
      items.forEach(function (it) {
        var id = num(it && it.id, null); if (id == null) return;
        var d = local.find(function (x) { return x.id === id; });
        if (!d || host.draggingShape(id)) return;
        var tgt = { cx: num(it.cx, d.cx), cy: num(it.cy, d.cy), w: Math.max(1e-3, num(it.w, d.w)), h: Math.max(1e-3, num(it.h, d.h)), r: num(it.r, d.rotation || 0) };
        var data = null;
        if (it.data && typeof it.data === 'object' && d.kind !== 'pen') {
          try { if (JSON.stringify(it.data).length < 400) data = JSON.parse(JSON.stringify(it.data)); } catch (e) { data = null; }
        }
        var e = liveShapes.get(id);
        if (!e) e = { cur: { cx: d.cx, cy: d.cy, w: d.w, h: d.h, r: d.rotation || 0 }, tgt: tgt, data: null, at: t, ending: false };
        e.tgt = tgt; e.at = t; e.ending = !!msg.end; if (data) e.data = data;
        liveShapes.set(id, e); any = true;
      });
      if (any) { ensureTick(); host.redraw(); }
    }

    function receiveDraft(msg, from) {
      if (msg.end) {
        var d0 = drafts.get(from);
        if (d0) { d0.ending = true; d0.at = now(); ensureTick(); }       // lingers until the saved shape arrives
        return;
      }
      var kind = String(msg.kind || '');
      var color = host.safeColor(msg.color, '#c9a24b');
      var d = drafts.get(from) || { shape: null, pts: [], at: 0, ending: false };
      d.at = now(); d.ending = false;
      if (kind === 'pen') {
        var f = num(msg.from, 0);
        if (f === 0) d.pts = [];
        if (f > d.pts.length || d.pts.length > MAX_PEN) return;           // missed a message: wait for the saved version
        var pts = Array.isArray(msg.pts) ? msg.pts.slice(0, MAX_POINTS) : [];
        pts.forEach(function (p) { if (Array.isArray(p)) d.pts.push([num(p[0], 0), num(p[1], 0)]); });
        if (d.pts.length < 2) { drafts.set(from, d); return; }
        var g = host.finalizePenShape(d.pts);
        d.shape = { kind: 'pen', cx: g.cx, cy: g.cy, w: g.w, h: g.h, rotation: 0, data: g.data, color: color, fill: false, fill_opacity: 1 };
      } else if (kind === 'line' || kind === 'rect' || kind === 'oval' || kind === 'angle') {
        var data = {};
        if (msg.data && typeof msg.data === 'object') { try { if (JSON.stringify(msg.data).length < 400) data = JSON.parse(JSON.stringify(msg.data)); } catch (e) { data = {}; } }
        d.shape = { kind: kind, cx: num(msg.cx, 0), cy: num(msg.cy, 0), w: Math.max(1e-3, num(msg.w, 1)), h: Math.max(1e-3, num(msg.h, 1)),
                    rotation: num(msg.r, 0), data: data, color: color, fill: !!msg.fill, fill_opacity: Math.max(0, Math.min(1, num(msg.fo, 0.3))) };
      } else { return; }
      drafts.set(from, d);
      ensureTick(); host.redraw();
    }

    function receive(msg) {
      if (!msg || typeof msg !== 'object' || msg.m !== host.mapId) return;
      var from = String(msg.u || '');
      if (!from || from === uid) return;
      switch (msg.k) {
        case 'pins': receivePins(msg); break;
        case 'shapes': receiveShapes(msg); break;
        case 'draft': receiveDraft(msg, from); break;
        case 'fog':
          if (!host.fog) break;
          if (msg.end) host.fog.liveEnd(from);
          else host.fog.liveStroke(from, msg.mode === 'erase' ? 'erase' : 'paint', num(msg.brush, 100), Array.isArray(msg.pts) ? msg.pts.slice(0, MAX_POINTS) : []);
          break;
        default: break;
      }
    }

    return {
      attach: function (conn) { rt = conn; },
      receive: receive,
      dragMoved: dragMoved, dragEnded: dragEnded,
      draftMoved: draftMoved, draftEnded: draftEnded,
      fogMoved: fogMoved, fogEnded: fogEnded,
      // Used by the canvas redraw: the shape as it should look right now (mid-glide), plus other people's unfinished drawings.
      shapeView: function (d) {
        var e = liveShapes.size ? liveShapes.get(d.id) : null;
        if (!e) return d;
        var v = Object.assign({}, d, { cx: e.cur.cx, cy: e.cur.cy, w: e.cur.w, h: e.cur.h, rotation: e.cur.r });
        if (e.data) v.data = e.data;
        return v;
      },
      drafts: function () { var out = []; drafts.forEach(function (d) { if (d.shape) out.push(d.shape); }); return out; },
      // The saved state just arrived and replaced the shapes: unfinished drafts are now the real thing.
      savedStateArrived: function () {
        var gone = false;
        drafts.forEach(function (d, id) { if (d.ending) { drafts.delete(id); gone = true; } });
        return gone;
      },
      drafting: function () { return drafting; },
      busyWith: function (kind, id) { return kind === 'pin' ? livePins.has(id) : liveShapes.has(id); },
      uid: uid
    };
  }

  window.MapLive = { create: create };
}());
