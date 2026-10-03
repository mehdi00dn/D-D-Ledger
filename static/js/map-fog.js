/* Map fog of war (client side).

   The fog is a low-resolution mask (one cell = `cell` map pixels, 1 = fogged). The server is the
   source of truth and does the real hiding: Players receive a map image with fogged areas already
   painted out, and no pins/drawings that sit under fog. Everything here is presentation plus the
   DM's painting tool -- a Player's tab only ever draws the cloud layer over areas that are already
   blank.

   Loaded before map.js, which calls MapFog.create({...}) and forwards pointer events while the fog
   tool is active. */
(function () {
  function create(host) {
    const layer = host.layerEl;
    const fillEl = layer.querySelector('.map-fog-fill');
    const cloudEl = layer.querySelector('.map-fog-clouds');
    const ring = host.ringEl;                  // brush outline (DM only)
    const W = host.naturalWidth, H = host.naturalHeight;
    const base = `/campaigns/${host.campaignId}/api/maps/${host.mapId}/fog`;

    let cell = 8, cols = Math.ceil(W / 8), rows = Math.ceil(H / 8);
    let mask = new Uint8Array(cols * rows);    // 1 byte per cell while editing
    let version = host.initialVersion || 0;
    let mode = 'paint';                        // 'paint' | 'erase'
    let brush = 100;                           // diameter, map pixels
    let painting = null;                       // {before, last, changed}
    let renderQueued = false;
    let saving = Promise.resolve();

    layer.classList.toggle('is-dm', !!host.isDM);

    // ---- cloud animation ----------------------------------------------------------------------
    // The smoke is a Lottie file drawn onto a SMALL canvas (the fog is soft, so resolution does not
    // matter) that the browser stretches to the map and cuts to the fog mask. To stay cheap it:
    //   * loads the player + animation only when fog first appears on the map,
    //   * draws ~15 frames a second, and only while fog is visible and the tab is in the foreground,
    //   * shows a single still frame for people who asked their system for reduced motion,
    //   * gives up animating (keeps a still frame) if frames cost too much on a slow machine,
    //   * and falls back to a plain CSS drift if the files cannot be loaded at all.
    const smoke = (function () {
      const canvas = layer.querySelector('.map-fog-smoke');
      if (!canvas) return { sync() {} };
      const c2d = canvas.getContext('2d');
      const FPS = 15, STILL_FRAME = 240, SLOW_MS = 33, WINDOW = 20;
      const reduced = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
      let anim = null, loading = null, failed = false;
      let wanted = false, running = false, still = false;
      let raf = 0, startTs = 0, lastTs = 0, slow = 0, samples = 0;

      function loadScript(src) {
        return new Promise((resolve, reject) => {
          if (window.lottie) { resolve(); return; }
          const tag = document.createElement('script');
          tag.src = src; tag.onload = resolve; tag.onerror = reject;
          document.head.appendChild(tag);
        });
      }
      function ensure() {
        if (anim || failed) return Promise.resolve(anim);
        if (loading) return loading;
        loading = Promise.all([
          loadScript(layer.dataset.lottieLib),
          fetch(layer.dataset.smokeJson).then((r) => { if (!r.ok) throw new Error('smoke'); return r.json(); }),
        ]).then(([, data]) => {
          anim = window.lottie.loadAnimation({
            renderer: 'canvas', loop: false, autoplay: false, animationData: data,
            rendererSettings: { context: c2d, clearCanvas: true, preserveAspectRatio: 'xMidYMid slice' },
          });
          return anim;
        }).catch(() => {
          failed = true;
          layer.classList.add('smoke-fallback');     // plain CSS clouds instead
          return null;
        });
        return loading;
      }
      function draw(frame) { anim.goToAndStop(Math.floor(frame), true); }
      function tick(ts) {
        if (!running) return;
        raf = requestAnimationFrame(tick);
        if (ts - lastTs < 1000 / FPS - 2) return;
        lastTs = ts;
        const t0 = performance.now();
        draw(((ts - startTs) * 0.06) % anim.totalFrames);          // the file is authored at 60 fps
        samples++;
        if (performance.now() - t0 > SLOW_MS) slow++;
        if (samples >= WINDOW) {
          if (slow > WINDOW / 2) { stop(); still = true; draw(STILL_FRAME); }   // too heavy here: hold still
          samples = 0; slow = 0;
        }
      }
      function start() {
        if (running || still || !anim) return;
        running = true; startTs = performance.now(); lastTs = 0; samples = 0; slow = 0;
        raf = requestAnimationFrame(tick);
      }
      function stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }
      function apply() {
        if (!wanted) { stop(); return; }
        ensure().then((a) => {
          if (!a || !wanted) return;
          if ((reduced && reduced.matches) || still) { draw(STILL_FRAME); return; }
          if (!document.hidden) start();
        });
      }
      document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); else if (wanted) apply(); });
      return { sync(active) { if (active === wanted) return; wanted = active; apply(); } };
    }());

    // ---- packing: rows padded to whole bytes, MSB first (matches fog.py / Pillow mode '1') ----
    function pack() {
      const rowBytes = (cols + 7) >> 3;
      const out = new Uint8Array(rowBytes * rows);
      for (let y = 0; y < rows; y++) {
        for (let x = 0; x < cols; x++) {
          if (mask[y * cols + x]) out[y * rowBytes + (x >> 3)] |= 0x80 >> (x & 7);
        }
      }
      let s = '';
      for (let i = 0; i < out.length; i++) s += String.fromCharCode(out[i]);
      return btoa(s);
    }
    function unpack(b64) {
      const next = new Uint8Array(cols * rows);
      if (b64) {
        const bin = atob(b64);
        const rowBytes = (cols + 7) >> 3;
        for (let y = 0; y < rows; y++) {
          for (let x = 0; x < cols; x++) {
            const byte = bin.charCodeAt(y * rowBytes + (x >> 3)) || 0;
            if (byte & (0x80 >> (x & 7))) next[y * cols + x] = 1;
          }
        }
      }
      return next;
    }
    function hasFog() { for (let i = 0; i < mask.length; i++) if (mask[i]) return true; return false; }

    // ---- drawing: the mask becomes an alpha-mask image for the fill and the cloud layer ----
    const mc = document.createElement('canvas');     // one pixel per cell
    const sc = document.createElement('canvas');     // smoothed, larger copy used for the final look
    function render(fast) {
      renderQueued = false;
      mc.width = cols; mc.height = rows;
      const mctx = mc.getContext('2d');
      const img = mctx.createImageData(cols, rows);
      for (let i = 0; i < mask.length; i++) {
        const o = i * 4;
        img.data[o] = img.data[o + 1] = img.data[o + 2] = 255;
        img.data[o + 3] = mask[i] ? 255 : 0;
      }
      mctx.putImageData(img, 0, 0);
      let source = mc;
      if (fast !== true) {
        // Upscale with smoothing (and a light blur where the browser supports it) so the fog edge is
        // soft instead of showing the mask's square cells. Skipped while painting to stay responsive.
        const k = Math.max(1, Math.min(4, Math.floor(1200 / Math.max(cols, rows))));
        sc.width = cols * k; sc.height = rows * k;
        const sctx = sc.getContext('2d');
        sctx.imageSmoothingEnabled = true;
        sctx.imageSmoothingQuality = 'high';
        if ('filter' in sctx) sctx.filter = `blur(${k * 1.5}px)`;
        sctx.drawImage(mc, 0, 0, sc.width, sc.height);
        source = sc;
      }
      const url = `url(${source.toDataURL('image/png')})`;
      [fillEl, cloudEl].forEach((el) => { el.style.webkitMaskImage = url; el.style.maskImage = url; });
      const active = hasFog();
      layer.hidden = !active;
      smoke.sync(active);
      if (host.onChange) host.onChange(active);
    }
    function queueRender() {
      if (renderQueued) return;
      renderQueued = true;
      requestAnimationFrame(() => render(!!painting));
    }

    // ---- painting ----
    function stamp(x, y) {
      const r = brush / 2;
      const x0 = Math.max(0, Math.floor((x - r) / cell)), x1 = Math.min(cols - 1, Math.floor((x + r) / cell));
      const y0 = Math.max(0, Math.floor((y - r) / cell)), y1 = Math.min(rows - 1, Math.floor((y + r) / cell));
      const v = mode === 'paint' ? 1 : 0;
      for (let cy = y0; cy <= y1; cy++) {
        for (let cx = x0; cx <= x1; cx++) {
          const dx = (cx + 0.5) * cell - x, dy = (cy + 0.5) * cell - y;
          if (dx * dx + dy * dy <= r * r && mask[cy * cols + cx] !== v) { mask[cy * cols + cx] = v; painting.changed = true; }
        }
      }
    }
    function strokeTo(pos) {
      const last = painting.last;
      const dist = Math.hypot(pos.x - last.x, pos.y - last.y);
      const step = Math.max(cell, brush / 4);
      const n = Math.max(1, Math.ceil(dist / step));
      for (let i = 1; i <= n; i++) stamp(last.x + (pos.x - last.x) * i / n, last.y + (pos.y - last.y) * i / n);
      painting.last = pos;
      queueRender();
    }
    function moveRing(pos) {
      if (!ring) return;
      ring.style.left = (pos.x / W * 100) + '%';
      ring.style.top = (pos.y / H * 100) + '%';
      ring.style.width = (brush / W * 100) + '%';
      ring.style.height = (brush / H * 100) + '%';
      ring.hidden = false;
    }

    // ---- talking to the server ----
    function post(body) {
      saving = saving.then(async () => {
        const res = await fetch(base, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        if (!res.ok) return;
        const d = await res.json();
        version = d.version;
      }).catch(() => { /* the next poll shows what the server really has */ });
      return saving;
    }
    function applyMask(next) {          // used by undo/redo
      mask = next.slice();
      render();
      return post({ mask: pack() });
    }

    async function refresh() {
      const res = await fetch(base);
      if (!res.ok) return;
      const d = await res.json();
      if (d.cols && d.rows) { cols = d.cols; rows = d.rows; cell = d.cell || cell; }
      mask = unpack(d.mask);
      version = d.version;
      render();
      if (!host.isDM && host.reloadImage) host.reloadImage(version);   // Player: the picture changed too
      if (host.onRefresh) host.onRefresh();
    }

    return {
      refresh,
      hasFog,
      // true if this map point is under fog (used so a Player cannot click a token they cannot see)
      pointFogged(x, y) {
        const cx = Math.floor(x / cell), cy = Math.floor(y / cell);
        if (!(cx >= 0 && cy >= 0 && cx < cols && cy < rows)) return false;
        return mask[cy * cols + cx] === 1;
      },
      isPainting: () => !!painting,
      setMode(m) { mode = m === 'erase' ? 'erase' : 'paint'; },
      setBrush(px) { brush = Math.max(20, Math.min(400, parseInt(px, 10) || 100)); },
      showRing(on) { if (ring && !on) ring.hidden = true; },
      begin(e) {
        const pos = host.eventPos(e);
        painting = { before: mask.slice(), last: pos, changed: false };
        stamp(pos.x, pos.y);
        queueRender();
        moveRing(pos);
      },
      move(e) {
        const pos = host.eventPos(e);
        moveRing(pos);
        if (painting) strokeTo(pos);
      },
      end() {
        if (!painting) return;
        const { before, changed } = painting;
        painting = null;
        render();
        if (!changed) return;
        const after = mask.slice();
        post({ mask: pack() });
        if (host.pushCommand) host.pushCommand({ undo: () => applyMask(before), redo: () => applyMask(after) });
      },
      fillAll() { mask.fill(1); render(); return post({ fill: true }); },
      clearAll() { mask.fill(0); render(); return post({ clear: true }); },
      onState(s) {                       // called with every poll result
        if (painting || s.fog_version === version) return;
        refresh();
      },
      init(active) { if (active) refresh(); },
    };
  }

  // The cloud drift is cheap on purpose: it only runs while fog is visible and the tab is in the
  // foreground, at a low frame rate (see .map-fog-clouds), and not at all for reduced-motion users.
  document.addEventListener('visibilitychange', () => {
    document.documentElement.classList.toggle('fog-paused', document.hidden);
  });

  window.MapFog = { create };
}());
