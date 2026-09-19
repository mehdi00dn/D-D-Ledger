(function () {
  const initData = JSON.parse(document.getElementById('map-init-data').textContent);
  const mapId = initData.id;

  const stageWrap = document.getElementById('map-stage-wrap');
  const stageInner = document.getElementById('map-stage-inner');
  const canvas = document.getElementById('map-canvas');
  const ctx = canvas.getContext('2d');
  const pinLayer = document.getElementById('map-pin-layer');

  const pinBubble = document.getElementById('pin-action-bubble');
  const pinViewBtn = document.getElementById('pin-view-btn');
  const pinPcNpcToggle = document.getElementById('pin-pc-npc-toggle');
  const pinRenameBtn = document.getElementById('pin-rename-btn');
  const pinResizeBtn = document.getElementById('pin-resize-btn');
  const pinRotateBtn = document.getElementById('pin-rotate-btn');
  const pinLockBtn = document.getElementById('pin-lock-btn');
  const pinDeleteBtn = document.getElementById('pin-delete-btn');
  const pinRenameBox = document.getElementById('pin-rename-box');
  const pinRenameInput = document.getElementById('pin-rename-input');

  const shapeBubble = document.getElementById('shape-action-bubble');
  const shapeResizeToggle = document.getElementById('shape-resize-toggle');
  const shapeRotateToggle = document.getElementById('shape-rotate-toggle');
  const shapeAngleToggle = document.getElementById('shape-angle-toggle');
  const shapeLockBtn = document.getElementById('shape-lock-btn');
  const shapeDeleteBtn = document.getElementById('shape-delete-btn');

  const undoBtn = document.getElementById('undo-btn');
  const redoBtn = document.getElementById('redo-btn');

  let naturalWidth = initData.image_width || 1000;
  let naturalHeight = initData.image_height || 1000;
  let gridSize = initData.grid_size || 50;
  let gridOffsetX = initData.grid_offset_x || 0;
  let gridOffsetY = initData.grid_offset_y || 0;
  let gridColor = initData.grid_color || 'gray';
  let gridVisible = initData.grid_visible == null ? true : !!initData.grid_visible;
  // While the mandatory first-time setup wizard is open, the grid must not
  // appear on the real (blurred) canvas behind it — only in the wizard's
  // own preview — regardless of the persisted/default grid_visible value.
  let suppressMainGrid = !initData.grid_setup_done;
  let linkedToBattle = !!initData.linked_to_battle;
  let lockedForPlayers = !!initData.locked_for_players;
  // A Player can't edit a map the DM has locked -- view/pan/zoom only.
  // The DM is never read-only on their own lock. Server-side enforcement
  // lives in app.py (map_edit_allowed); this just keeps the UI honest.
  const readOnly = () => lockedForPlayers && !window.IS_DM;

  let bgImage = new Image();
  let bgLoaded = false;
  let drawings = [];   // shape objects: {id, kind, cx, cy, w, h, rotation, color, fill, fill_opacity, data, locked}
  let pins = [];        // {id, pin_type, x, y, scale, rotation, locked, ...}
  let currentTool = 'select';
  let selectedShapeId = null;      // the single selected shape, only meaningful when selectedShapeIds.size === 1
  let selectedShapeIds = new Set(); // multi-select (Ctrl/Cmd+click)
  let selectedPinId = null;
  let shapeHandleMode = null; // null | 'resize' | 'rotate'
  let hoveredLockedShapeId = null; // shape under the cursor that's locked (shows the unlock icon)
  let pollTimer = null;

  const UNLOCK_ICON_PATH = 'M7 11h10a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2z M8 11V7a4 4 0 0 1 7.75-1.5';
  const UNLOCK_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M7 11h10a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2z"/><path d="M8 11V7a4 4 0 0 1 7.75-1.5"/></svg>';

  const PROP_ICON_SRC = {
    paw: '/static/icons/pins/paw.svg',
    skull: '/static/icons/pins/skull.svg',
    sword: '/static/icons/pins/sword.svg',
    hand: '/static/icons/pins/hand.svg',
  };
  const USER_FALLBACK_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="3.5"/><path d="M4.5 20c1.2-4 4-6 7.5-6s6.3 2 7.5 6"/></svg>';

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }

  // ---------------------------------------------------------------------
  // Canvas setup: internal resolution matches the image's natural pixels,
  // so every drawing operation and stored coordinate uses that same space.
  // ---------------------------------------------------------------------
  canvas.width = naturalWidth;
  canvas.height = naturalHeight;
  bgImage.onload = () => { bgLoaded = true; redraw(); };
  bgImage.src = '/uploads/' + initData.image_path;

  function getNaturalPos(clientX, clientY) {
    const rect = stageInner.getBoundingClientRect();
    const fracX = (clientX - rect.left) / rect.width;
    const fracY = (clientY - rect.top) / rect.height;
    return { x: fracX * naturalWidth, y: fracY * naturalHeight };
  }

  function eventPos(e) {
    const t = e.touches ? e.touches[0] : e;
    return getNaturalPos(t.clientX, t.clientY);
  }

  // ---------------------------------------------------------------------
  // Undo / redo — a generic command stack. Every mutating action (add,
  // delete, move, resize, rotate, recolor, clear) pushes a command with its
  // own undo()/redo() implementation so Ctrl+Z / Ctrl+Shift+Z works uniformly.
  // ---------------------------------------------------------------------
  const undoStack = [];
  const redoStack = [];

  function pushCommand(cmd) {
    undoStack.push(cmd);
    redoStack.length = 0;
    if (undoStack.length > 60) undoStack.shift();
    updateHistoryButtons();
  }
  async function undo() {
    const cmd = undoStack.pop();
    if (!cmd) return;
    await cmd.undo();
    redoStack.push(cmd);
    updateHistoryButtons();
  }
  async function redo() {
    const cmd = redoStack.pop();
    if (!cmd) return;
    await cmd.redo();
    undoStack.push(cmd);
    updateHistoryButtons();
  }
  function updateHistoryButtons() {
    undoBtn.disabled = undoStack.length === 0;
    redoBtn.disabled = redoStack.length === 0;
  }
  undoBtn.addEventListener('click', undo);
  redoBtn.addEventListener('click', redo);

  document.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 'z') {
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
      return;
    }
    if (e.key === 'Delete' || e.key === 'Backspace') {
      if (selectedShapeIds.size > 0) {
        e.preventDefault();
        [...selectedShapeIds].forEach((id) => {
          const d = drawings.find((s) => s.id === id);
          if (d) deleteShape(d);
        });
      } else if (selectedPinId != null) {
        e.preventDefault();
        const p = pins.find((x) => x.id === selectedPinId);
        if (p) deletePinObj(p);
      }
    }
  });

  // ---------------------------------------------------------------------
  // Shape geometry: every shape is a rotated bounding box (cx, cy, w, h,
  // rotation) in natural-image pixel space. Kind-specific geometry that
  // lives inside that box (line endpoints, pen points) is stored in `data`
  // as fractions of w/h, so resizing/rotating the box carries it along.
  // ---------------------------------------------------------------------
  function toRad(deg) { return ((deg || 0) * Math.PI) / 180; }

  function localOffsetToNatural(d, lx, ly) {
    const rad = toRad(d.rotation);
    const cos = Math.cos(rad), sin = Math.sin(rad);
    return { x: d.cx + (lx * cos - ly * sin), y: d.cy + (lx * sin + ly * cos) };
  }

  function naturalToLocalOffset(d, x, y) {
    const dx = x - d.cx, dy = y - d.cy;
    const rad = toRad(-d.rotation);
    const cos = Math.cos(rad), sin = Math.sin(rad);
    return { x: dx * cos - dy * sin, y: dx * sin + dy * cos };
  }

  function unitAxes(rotationDeg) {
    const rad = toRad(rotationDeg);
    return { u: { x: Math.cos(rad), y: Math.sin(rad) }, v: { x: -Math.sin(rad), y: Math.cos(rad) } };
  }

  function getShapeCorners(d) {
    if (d.kind === 'angle') {
      // The vertex (cx, cy) is one edge of the box, not its center — the
      // wedge reaches from the vertex out to radius w, spanning ±half the
      // sweep vertically. A generic center-based box would badly misfit it.
      const sweep = (((d.data && d.data.sweepDeg) || 60) * Math.PI) / 180;
      const yMax = d.w * Math.sin(sweep / 2);
      const r = d.w;
      return [
        localOffsetToNatural(d, 0, -yMax),
        localOffsetToNatural(d, r, -yMax),
        localOffsetToNatural(d, r, yMax),
        localOffsetToNatural(d, 0, yMax),
      ];
    }
    const hw = d.w / 2, hh = d.h / 2;
    return [
      localOffsetToNatural(d, -hw, -hh),
      localOffsetToNatural(d, hw, -hh),
      localOffsetToNatural(d, hw, hh),
      localOffsetToNatural(d, -hw, hh),
    ];
  }

  function getRotateHandlePos(d) {
    if (d.kind === 'angle') {
      const sweep = (((d.data && d.data.sweepDeg) || 60) * Math.PI) / 180;
      const yMax = d.w * Math.sin(sweep / 2);
      const dist = yMax + Math.max(30, canvas.width / 25);
      return localOffsetToNatural(d, d.w / 2, -dist);
    }
    const dist = d.h / 2 + Math.max(30, canvas.width / 25);
    return localOffsetToNatural(d, 0, -dist);
  }

  function getAngleHandlePositions(d) {
    const sweep = (((d.data && d.data.sweepDeg) || 60) * Math.PI) / 180;
    const half = sweep / 2;
    const r = d.w;
    return [
      localOffsetToNatural(d, r * Math.cos(-half), r * Math.sin(-half)),
      localOffsetToNatural(d, r * Math.cos(half), r * Math.sin(half)),
    ];
  }

  function distToSegment(p, a, b) {
    const abx = b.x - a.x, aby = b.y - a.y;
    const t = ((p.x - a.x) * abx + (p.y - a.y) * aby) / Math.max(1e-6, abx * abx + aby * aby);
    const tc = Math.max(0, Math.min(1, t));
    const cx = a.x + abx * tc, cy = a.y + aby * tc;
    return Math.hypot(p.x - cx, p.y - cy);
  }

  function hitTestShape(d, pos) {
    const local = naturalToLocalOffset(d, pos.x, pos.y);
    const lx = local.x, ly = local.y;
    const hw = d.w / 2, hh = d.h / 2;
    const pad = Math.max(6, canvas.width / 300);
    const hasFill = (d.fill_opacity || 0) > 0.001;
    if (d.kind === 'rect') {
      if (hasFill) return lx >= -hw - pad && lx <= hw + pad && ly >= -hh - pad && ly <= hh + pad;
      const nearVert = Math.abs(Math.abs(lx) - hw) < pad && ly >= -hh - pad && ly <= hh + pad;
      const nearHoriz = Math.abs(Math.abs(ly) - hh) < pad && lx >= -hw - pad && lx <= hw + pad;
      return nearVert || nearHoriz;
    }
    if (d.kind === 'oval') {
      if (hw < 0.01 || hh < 0.01) return false;
      const nx = lx / hw, ny = ly / hh;
      const r = Math.hypot(nx, ny);
      if (hasFill) return r <= 1 + pad / Math.max(hw, 1);
      return Math.abs(r - 1) < pad / Math.max(Math.min(hw, hh), 1);
    }
    if (d.kind === 'line') {
      const g = d.data || {};
      const p1 = { x: (g.x1n || 0) * d.w, y: (g.y1n || 0) * d.h };
      const p2 = { x: (g.x2n || 0) * d.w, y: (g.y2n || 0) * d.h };
      return distToSegment({ x: lx, y: ly }, p1, p2) < pad;
    }
    if (d.kind === 'pen') {
      const pts = (d.data && d.data.points) || [];
      for (let i = 1; i < pts.length; i++) {
        const p1 = { x: pts[i - 1][0] * d.w, y: pts[i - 1][1] * d.h };
        const p2 = { x: pts[i][0] * d.w, y: pts[i][1] * d.h };
        if (distToSegment({ x: lx, y: ly }, p1, p2) < pad) return true;
      }
      return false;
    }
    if (d.kind === 'angle') {
      const r = d.w;
      const sweep = (((d.data && d.data.sweepDeg) || 60) * Math.PI) / 180;
      const dist = Math.hypot(lx, ly);
      if (dist > r + pad) return false;
      const angle = Math.atan2(ly, lx);
      const half = sweep / 2;
      if (hasFill) return Math.abs(angle) <= half + 0.02;
      const nearEdge = Math.abs(angle - half) < 0.05 || Math.abs(angle + half) < 0.05;
      const nearArc = Math.abs(dist - r) < pad;
      return nearEdge || nearArc;
    }
    return false;
  }

  function hitTestResizeHandle(d, pos) {
    const corners = getShapeCorners(d);
    const hs = Math.max(12, canvas.width / 90);
    for (let i = 0; i < 4; i++) {
      if (Math.hypot(pos.x - corners[i].x, pos.y - corners[i].y) < hs) return i;
    }
    return null;
  }

  function hitTestRotateHandle(d, pos) {
    const rp = getRotateHandlePos(d);
    const hs = Math.max(14, canvas.width / 80);
    return Math.hypot(pos.x - rp.x, pos.y - rp.y) < hs;
  }

  // ---------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------
  function drawGridOnto(targetCtx, w, h, size, offX, offY, color, visible) {
    if (!visible || !size || size < 2) return;
    targetCtx.save();
    const startX = ((offX % size) + size) % size;
    const startY = ((offY % size) + size) % size;
    const outerWidth = Math.max(1, w / 700);
    const innerWidth = Math.max(0.5, w / 1400);

    function strokeLines(strokeColor, width) {
      targetCtx.strokeStyle = strokeColor;
      targetCtx.lineWidth = width;
      for (let x = startX; x < w; x += size) {
        targetCtx.beginPath(); targetCtx.moveTo(x, 0); targetCtx.lineTo(x, h); targetCtx.stroke();
      }
      for (let y = startY; y < h; y += size) {
        targetCtx.beginPath(); targetCtx.moveTo(0, y); targetCtx.lineTo(w, y); targetCtx.stroke();
      }
    }

    if (color === 'white') {
      strokeLines('rgba(255,255,255,0.7)', outerWidth);
    } else if (color === 'black') {
      strokeLines('rgba(0,0,0,0.7)', outerWidth);
    } else {
      // 'gray' (default): a two-tone dark+light pass gives the lines
      // contrast against both light and dark map art.
      strokeLines('rgba(0,0,0,0.45)', outerWidth);
      strokeLines('rgba(255,255,255,0.55)', innerWidth);
    }
    targetCtx.restore();
  }

  function drawGrid() {
    if (suppressMainGrid) return;
    drawGridOnto(ctx, canvas.width, canvas.height, gridSize, gridOffsetX, gridOffsetY, gridColor, gridVisible);
  }

  function drawShape(d) {
    ctx.save();
    ctx.translate(d.cx, d.cy);
    ctx.rotate(toRad(d.rotation));
    ctx.strokeStyle = d.color;
    ctx.fillStyle = d.color;
    ctx.lineWidth = Math.max(2, canvas.width / 450);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    const w = d.w, h = d.h;

    const hasFill = (d.fill_opacity || 0) > 0.001;
    if (d.kind === 'rect') {
      if (hasFill) { ctx.globalAlpha = d.fill_opacity; ctx.fillRect(-w / 2, -h / 2, w, h); ctx.globalAlpha = 1; }
      ctx.strokeRect(-w / 2, -h / 2, w, h);
    } else if (d.kind === 'oval') {
      ctx.beginPath();
      ctx.ellipse(0, 0, Math.max(0.01, Math.abs(w / 2)), Math.max(0.01, Math.abs(h / 2)), 0, 0, Math.PI * 2);
      if (hasFill) { ctx.globalAlpha = d.fill_opacity; ctx.fill(); ctx.globalAlpha = 1; }
      ctx.stroke();
    } else if (d.kind === 'line') {
      const g = d.data || {};
      ctx.beginPath();
      ctx.moveTo((g.x1n || 0) * w, (g.y1n || 0) * h);
      ctx.lineTo((g.x2n || 0) * w, (g.y2n || 0) * h);
      ctx.stroke();
    } else if (d.kind === 'pen') {
      const pts = (d.data && d.data.points) || [];
      if (pts.length > 1) {
        ctx.beginPath();
        ctx.moveTo(pts[0][0] * w, pts[0][1] * h);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0] * w, pts[i][1] * h);
        ctx.stroke();
      }
    } else if (d.kind === 'angle') {
      const r = w;
      const sweep = (((d.data && d.data.sweepDeg) || 60) * Math.PI) / 180;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.arc(0, 0, Math.max(0.01, r), -sweep / 2, sweep / 2);
      ctx.closePath();
      if (hasFill) { ctx.globalAlpha = d.fill_opacity; ctx.fill(); ctx.globalAlpha = 1; }
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawShapeOutline(d) {
    const corners = getShapeCorners(d);
    ctx.beginPath();
    ctx.moveTo(corners[0].x, corners[0].y);
    for (let i = 1; i < 4; i++) ctx.lineTo(corners[i].x, corners[i].y);
    ctx.closePath();
    ctx.stroke();
    return corners;
  }

  function drawSelectionOverlay() {
    if (selectedShapeIds.size === 0) return;
    ctx.save();
    ctx.strokeStyle = '#4d9eff';
    ctx.lineWidth = Math.max(1.5, canvas.width / 900);
    ctx.setLineDash([canvas.width / 150, canvas.width / 220]);

    let primaryCorners = null;
    selectedShapeIds.forEach((id) => {
      const d = drawings.find((s) => s.id === id);
      if (!d) return;
      const corners = drawShapeOutline(d);
      if (id === selectedShapeId) primaryCorners = { d, corners };
    });
    ctx.setLineDash([]);

    // Resize/rotate handles only make sense for a single selected shape.
    if (primaryCorners) {
      const { d, corners } = primaryCorners;
      if (shapeHandleMode === 'resize') {
        const hs = Math.max(9, canvas.width / 130);
        ctx.fillStyle = '#4d9eff';
        corners.forEach((c) => ctx.fillRect(c.x - hs / 2, c.y - hs / 2, hs, hs));
      }
      if (shapeHandleMode === 'rotate') {
        const top = { x: (corners[0].x + corners[1].x) / 2, y: (corners[0].y + corners[1].y) / 2 };
        const rp = getRotateHandlePos(d);
        ctx.beginPath();
        ctx.moveTo(top.x, top.y);
        ctx.lineTo(rp.x, rp.y);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(rp.x, rp.y, Math.max(8, canvas.width / 140), 0, Math.PI * 2);
        ctx.fillStyle = '#4d9eff';
        ctx.fill();
      }
      if (shapeHandleMode === 'angle-adjust' && d.kind === 'angle') {
        const handles = getAngleHandlePositions(d);
        const hs = Math.max(9, canvas.width / 130);
        ctx.fillStyle = '#4d9eff';
        handles.forEach((p) => {
          ctx.beginPath();
          ctx.arc(p.x, p.y, hs / 2, 0, Math.PI * 2);
          ctx.fill();
        });
      }
    }
    ctx.restore();
  }

  function lockIconRadius() { return Math.max(15, canvas.width / 55); }

  function drawLockHoverIcon() {
    if (hoveredLockedShapeId == null) return;
    const d = drawings.find((s) => s.id === hoveredLockedShapeId);
    if (!d) return;
    const r = lockIconRadius();
    ctx.save();
    ctx.translate(d.cx, d.cy);
    ctx.globalAlpha = 0.7;
    ctx.fillStyle = '#14161d';
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, Math.PI * 2);
    ctx.fill();
    const scale = (r * 1.1) / 24;
    ctx.translate(-12 * scale, -12 * scale);
    ctx.scale(scale, scale);
    ctx.strokeStyle = '#f2f2f2';
    ctx.lineWidth = 2.2;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.stroke(new Path2D(UNLOCK_ICON_PATH));
    ctx.restore();
  }

  let previewShape = null; // the shape currently being drawn, shown live

  function renderAngleLabels() {
    const layer = document.getElementById('angle-labels-layer');
    if (!layer) return;
    const angleShapes = drawings.filter((d) => d.kind === 'angle');
    if (previewShape && previewShape.kind === 'angle') angleShapes.push(previewShape);
    layer.innerHTML = angleShapes.map((d) => {
      const sweepDeg = Math.round((d.data && d.data.sweepDeg) || 60);
      const labelPos = localOffsetToNatural(d, d.w * 0.45, 0);
      const leftPct = (labelPos.x / naturalWidth) * 100;
      const topPct = (labelPos.y / naturalHeight) * 100;
      return `<div class="angle-degree-label" style="left:${leftPct}%; top:${topPct}%;">${sweepDeg}\u00b0</div>`;
    }).join('');
  }

  function redraw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (bgLoaded) ctx.drawImage(bgImage, 0, 0, canvas.width, canvas.height);
    drawGrid();
    drawings.forEach(drawShape);
    if (previewShape) drawShape(previewShape);
    drawSelectionOverlay();
    drawLockHoverIcon();
    renderAngleLabels();
  }

  // ---------------------------------------------------------------------
  // Data loading
  // ---------------------------------------------------------------------
  async function loadDrawings() {
    const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/drawings`);
    drawings = await res.json();
    redraw();
  }

  async function loadPins() {
    if (activeDrag && (activeDrag.type === 'move-pin' || activeDrag.type === 'resize-pin' || activeDrag.type === 'rotate-pin')) return;
    const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/pins`);
    pins = await res.json();
    renderPins();
  }

  // ---------------------------------------------------------------------
  // Pins
  // ---------------------------------------------------------------------
  function pinMarkup(p) {
    const isChar = p.pin_type === 'character';
    const scale = p.scale || 1;
    const wPct = (gridSize * scale / naturalWidth) * 100;
    const hPct = (gridSize * scale / naturalHeight) * 100;
    const leftPct = (p.x / naturalWidth) * 100;
    const topPct = (p.y / naturalHeight) * 100;
    const borderColor = isChar ? (p.group_color || '#c9a24b') : '#c9a24b';
    const dead = isChar && p.is_dead;
    const avatarInner = isChar
      ? (p.avatar_path ? `<img src="/uploads/${p.avatar_path}" alt="">` : USER_FALLBACK_ICON)
      : `<img src="${PROP_ICON_SRC[p.icon_key] || PROP_ICON_SRC.paw}" alt="" class="prop-icon-img">`;
    const label = isChar ? escapeHtml(p.char_name || '') : escapeHtml(p.custom_name || p.icon_key || 'Marker');
    const unlockOverlay = p.locked ? `<button type="button" class="pin-unlock-icon" data-unlock-pin="${p.id}" title="Unlock">${UNLOCK_SVG}</button>` : '';
    // The label sits on `.map-pin` itself (never rotated); only the inner
    // `.map-pin-visual` circle spins, so names stay upright and in place.
    return `
      <div class="map-pin" data-pin-id="${p.id}" data-pin-type="${p.pin_type}" data-locked="${!!p.locked}"
           data-character-id="${p.character_id || ''}"
           style="left:${leftPct}%; top:${topPct}%; width:${wPct}%; height:${hPct}%;">
        <div class="map-pin-visual ${dead ? 'is-dead' : ''}" style="border-color:${borderColor}; transform: rotate(${p.rotation || 0}deg);">
          <div class="map-pin-avatar">${avatarInner}</div>
        </div>
        <span class="map-pin-label">${label}</span>
        ${unlockOverlay}
      </div>`;
  }

  function renderPins() {
    pinLayer.innerHTML = pins.map(pinMarkup).join('');
    if (selectedPinId) positionPinBubble(selectedPinId);
  }

  // Chess-piece-style snapping: the pin's CENTER locks to the nearest grid
  // cell's center rather than forcing the whole pin inside one cell, so a
  // large creature's marker can still visually cover several cells.
  function snapPointToGridCenter(x, y) {
    if (!snapToGrid || !gridSize || gridSize < 2) return { x, y };
    const cellX = Math.round((x - gridOffsetX - gridSize / 2) / gridSize);
    const cellY = Math.round((y - gridOffsetY - gridSize / 2) / gridSize);
    return { x: gridOffsetX + gridSize / 2 + cellX * gridSize, y: gridOffsetY + gridSize / 2 + cellY * gridSize };
  }

  function applyPinStyle(pin, el) {
    const scale = pin.scale || 1;
    const wPct = (gridSize * scale / naturalWidth) * 100;
    const hPct = (gridSize * scale / naturalHeight) * 100;
    const leftPct = (pin.x / naturalWidth) * 100;
    const topPct = (pin.y / naturalHeight) * 100;
    el.style.left = `${leftPct}%`;
    el.style.top = `${topPct}%`;
    el.style.width = `${wPct}%`;
    el.style.height = `${hPct}%`;
    const visual = el.querySelector('.map-pin-visual');
    if (visual) visual.style.transform = `rotate(${pin.rotation || 0}deg)`;
  }

  function positionPinBubble(pinId) {
    const el = pinLayer.querySelector(`[data-pin-id="${pinId}"]`);
    const pin = pins.find((p) => p.id === pinId);
    if (!el || !pin) { pinBubble.hidden = true; return; }
    // Positioned in stageInner's own (untransformed) pixel space, computed
    // straight from the pin's data — not from the pin element's rendered
    // rect, which would already include the zoom transform and double it.
    const wrapRect = stageWrap.getBoundingClientRect();
    const leftPx = (pin.x / naturalWidth) * wrapRect.width;
    const topPx = (pin.y / naturalHeight) * wrapRect.height;
    const halfHeightPx = ((gridSize * (pin.scale || 1)) / naturalHeight) * wrapRect.height / 2;
    pinBubble.style.left = `${leftPx}px`;
    pinBubble.style.top = `${topPx - halfHeightPx - 14}px`;
    pinBubble.hidden = false;
    // A Player never gets a "View details" button on an NPC pin -- the
    // detail endpoint already 403s them, this just stops the dead-end click.
    pinViewBtn.style.display = (pin.pin_type === 'character' && (window.IS_DM || !pin.is_npc)) ? '' : 'none';
    pinRenameBtn.style.display = pin.pin_type === 'character' ? 'none' : '';
    if (pinPcNpcToggle) {
      const isFamiliar = pin.pin_type === 'prop' && !!pin.participant_id;
      pinPcNpcToggle.style.display = isFamiliar ? '' : 'none';
      if (isFamiliar) {
        pinPcNpcToggle.textContent = pin.is_npc ? 'NPC' : 'PC';
        pinPcNpcToggle.title = pin.is_npc
          ? 'Currently an NPC (stats hidden from players) — click to make it a PC'
          : 'Currently a PC (stats visible to players) — click to make it an NPC';
      }
    }
  }

  function positionPinRenameBox(pinId) {
    const pin = pins.find((p) => p.id === pinId);
    if (!pin) { pinRenameBox.hidden = true; return; }
    const wrapRect = stageWrap.getBoundingClientRect();
    const leftPx = (pin.x / naturalWidth) * wrapRect.width;
    const topPx = (pin.y / naturalHeight) * wrapRect.height;
    const halfHeightPx = ((gridSize * (pin.scale || 1)) / naturalHeight) * wrapRect.height / 2;
    pinRenameBox.style.left = `${leftPx}px`;
    pinRenameBox.style.top = `${topPx + halfHeightPx + 8}px`;
  }

  let renamingPinId = null;
  function openPinRename(pinId) {
    const pin = pins.find((p) => p.id === pinId);
    if (!pin || pin.pin_type === 'character') return;
    renamingPinId = pinId;
    pinBubble.hidden = true;
    positionPinRenameBox(pinId);
    pinRenameBox.hidden = false;
    pinRenameInput.value = pin.custom_name || '';
    pinRenameInput.focus();
    pinRenameInput.select();
  }
  async function commitPinRename() {
    const pinId = renamingPinId;
    renamingPinId = null;
    pinRenameBox.hidden = true;
    if (pinId == null) return;
    const pin = pins.find((p) => p.id === pinId);
    if (!pin) return;
    const before = { custom_name: pin.custom_name || null };
    const newName = pinRenameInput.value.trim();
    const after = { custom_name: newName || null };
    if (before.custom_name === after.custom_name) return;
    pin.custom_name = after.custom_name;
    renderPins();
    await persistPinFields(pin, after);
    pushCommand({
      label: 'rename-pin',
      undo: async () => { pin.custom_name = before.custom_name; renderPins(); await persistPinFields(pin, before); },
      redo: async () => { pin.custom_name = after.custom_name; renderPins(); await persistPinFields(pin, after); },
    });
  }
  pinRenameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); pinRenameInput.blur(); }
    if (e.key === 'Escape') { e.preventDefault(); renamingPinId = null; pinRenameBox.hidden = true; }
  });
  pinRenameInput.addEventListener('blur', commitPinRename);
  pinRenameBtn.addEventListener('click', () => {
    if (selectedPinId != null) openPinRename(selectedPinId);
  });

  function positionShapeBubble() {
    const d = drawings.find((s) => s.id === selectedShapeId);
    if (!d) { shapeBubble.hidden = true; return; }
    const wrapRect = stageWrap.getBoundingClientRect();
    // Angle shapes' adjust handles sit out at the full reach (radius w),
    // well beyond the shape's own small nominal bounding box — use a
    // bounding circle of that radius instead so the bubble never overlaps
    // them, regardless of rotation.
    const rawCorners = d.kind === 'angle'
      ? [{ x: d.cx - d.w, y: d.cy - d.w }, { x: d.cx + d.w, y: d.cy - d.w },
         { x: d.cx + d.w, y: d.cy + d.w }, { x: d.cx - d.w, y: d.cy + d.w }]
      : getShapeCorners(d);
    const corners = rawCorners.map((c) => ({
      x: (c.x / naturalWidth) * wrapRect.width,
      y: (c.y / naturalHeight) * wrapRect.height,
    }));
    const minX = Math.min(...corners.map((c) => c.x));
    const maxX = Math.max(...corners.map((c) => c.x));
    const minY = Math.min(...corners.map((c) => c.y));
    const maxY = Math.max(...corners.map((c) => c.y));
    const midY = (minY + maxY) / 2;
    // Place the bubble to the side of the shape (never above it), so it can
    // never overlap the on-canvas resize corners or the rotate handle, which
    // both sit near the top of the shape's bounding box.
    shapeBubble.hidden = false;
    const bubbleRect = shapeBubble.getBoundingClientRect();
    const bubbleW = bubbleRect.width || 118;
    const gutter = 18;
    let left = maxX + gutter;
    let translateX = '0%';
    if (left + bubbleW > wrapRect.width) {
      left = minX - gutter;
      translateX = '-100%';
    }
    shapeBubble.style.left = `${left}px`;
    shapeBubble.style.top = `${midY}px`;
    shapeBubble.style.transform = `translate(${translateX}, -50%)`;
  }

  // ---- Selection ----
  function updateAngleToggleVisibility() {
    const d = drawings.find((s) => s.id === selectedShapeId);
    shapeAngleToggle.hidden = !(d && d.kind === 'angle');
    if (shapeAngleToggle.hidden) shapeAngleToggle.classList.remove('active');
  }

  function selectShape(id) {
    selectedShapeIds = new Set([id]);
    selectedShapeId = id;
    shapeHandleMode = null;
    shapeResizeToggle.classList.remove('active');
    shapeRotateToggle.classList.remove('active');
    deselectPin();
    positionShapeBubble();
    updateAngleToggleVisibility();
    redraw();
    updateStylePanelVisibility();
  }
  function deselectShape() {
    if (selectedShapeIds.size === 0) return;
    selectedShapeIds = new Set();
    selectedShapeId = null;
    shapeHandleMode = null;
    shapeResizeToggle.classList.remove('active');
    shapeRotateToggle.classList.remove('active');
    shapeBubble.hidden = true;
    redraw();
    updateStylePanelVisibility();
  }
  // Ctrl/Cmd+click: add or remove a shape from the current multi-selection.
  function toggleShapeSelection(id) {
    if (selectedShapeIds.has(id)) selectedShapeIds.delete(id);
    else selectedShapeIds.add(id);
    if (selectedShapeIds.size === 0) {
      deselectShape();
    } else if (selectedShapeIds.size === 1) {
      selectedShapeId = [...selectedShapeIds][0];
      shapeHandleMode = null;
      shapeResizeToggle.classList.remove('active');
      shapeRotateToggle.classList.remove('active');
      deselectPin();
      positionShapeBubble();
      updateAngleToggleVisibility();
      redraw();
    } else {
      // Multiple shapes selected: hide the per-shape bubble (resize/rotate/
      // lock/delete for a single object don't make sense for a group).
      selectedShapeId = null;
      shapeHandleMode = null;
      shapeBubble.hidden = true;
      deselectPin();
      redraw();
    }
    updateStylePanelVisibility();
  }
  function selectPin(pinId) {
    selectedPinId = pinId;
    deselectShape();
    positionPinBubble(pinId);
  }
  function deselectPin() {
    if (selectedPinId == null) return;
    selectedPinId = null;
    pinBubble.hidden = true;
    if (renamingPinId != null) { renamingPinId = null; pinRenameBox.hidden = true; }
  }

  shapeResizeToggle.addEventListener('click', () => {
    shapeHandleMode = shapeHandleMode === 'resize' ? null : 'resize';
    shapeResizeToggle.classList.toggle('active', shapeHandleMode === 'resize');
    shapeRotateToggle.classList.remove('active');
    shapeAngleToggle.classList.remove('active');
    redraw();
  });
  shapeRotateToggle.addEventListener('click', () => {
    shapeHandleMode = shapeHandleMode === 'rotate' ? null : 'rotate';
    shapeRotateToggle.classList.toggle('active', shapeHandleMode === 'rotate');
    shapeResizeToggle.classList.remove('active');
    shapeAngleToggle.classList.remove('active');
    redraw();
  });
  shapeAngleToggle.addEventListener('click', () => {
    shapeHandleMode = shapeHandleMode === 'angle-adjust' ? null : 'angle-adjust';
    shapeAngleToggle.classList.toggle('active', shapeHandleMode === 'angle-adjust');
    shapeResizeToggle.classList.remove('active');
    shapeRotateToggle.classList.remove('active');
    redraw();
  });
  // Hold-and-drag straight from the buttons themselves (same gesture as the
  // pin buttons), on top of the click-to-toggle-handles behavior above.
  shapeResizeToggle.addEventListener('mousedown', (e) => {
    if (selectedShapeId == null) return;
    const d = drawings.find((s) => s.id === selectedShapeId);
    if (!d) return;
    e.preventDefault();
    e.stopPropagation();
    const pos = eventPos(e);
    const startDist = Math.max(1, Math.hypot(pos.x - d.cx, pos.y - d.cy));
    const before = { cx: d.cx, cy: d.cy, w: d.w, h: d.h, rotation: d.rotation };
    activeDrag = { type: 'resize-shape-uniform', d, startDist, startW: d.w, startH: d.h, before };
  });
  shapeRotateToggle.addEventListener('mousedown', (e) => {
    if (selectedShapeId == null) return;
    const d = drawings.find((s) => s.id === selectedShapeId);
    if (!d) return;
    e.preventDefault();
    e.stopPropagation();
    const pos = eventPos(e);
    const before = { cx: d.cx, cy: d.cy, w: d.w, h: d.h, rotation: d.rotation };
    const startAngle = Math.atan2(pos.y - d.cy, pos.x - d.cx) * (180 / Math.PI);
    activeDrag = { type: 'rotate-shape', d, before, startAngle, startRotation: d.rotation || 0 };
  });
  shapeDeleteBtn.addEventListener('click', () => {
    if (selectedShapeIds.size > 1) {
      [...selectedShapeIds].forEach((id) => {
        const d = drawings.find((s) => s.id === id);
        if (d) deleteShape(d);
      });
      return;
    }
    const d = drawings.find((s) => s.id === selectedShapeId);
    if (d) deleteShape(d);
  });
  shapeLockBtn.addEventListener('click', async () => {
    const ids = selectedShapeIds.size ? [...selectedShapeIds] : (selectedShapeId != null ? [selectedShapeId] : []);
    for (const id of ids) {
      const d = drawings.find((s) => s.id === id);
      if (!d) continue;
      await lockShape(d);
    }
    deselectShape();
  });
  async function lockShape(d) {
    const before = { locked: false };
    const after = { locked: true };
    d.locked = true;
    await persistShapeFields(d, after);
    pushCommand({
      label: 'lock-shape',
      undo: async () => { d.locked = false; await persistShapeFields(d, before); redraw(); },
      redo: async () => { d.locked = true; await persistShapeFields(d, after); redraw(); },
    });
  }
  async function unlockShape(d) {
    const before = { locked: true };
    const after = { locked: false };
    d.locked = false;
    redraw();
    await persistShapeFields(d, after);
    pushCommand({
      label: 'unlock-shape',
      undo: async () => { d.locked = true; await persistShapeFields(d, before); redraw(); },
      redo: async () => { d.locked = false; await persistShapeFields(d, after); redraw(); },
    });
  }

  document.addEventListener('click', (e) => {
    if (e.target.closest('.map-pin') || e.target.closest('#pin-action-bubble')) return;
    deselectPin();
  });

  // ---------------------------------------------------------------------
  // Persistence + undo/redo command factories
  // ---------------------------------------------------------------------
  function serializeShapeForCreate(d) {
    return { kind: d.kind, data: d.data, cx: d.cx, cy: d.cy, w: d.w, h: d.h, rotation: d.rotation, color: d.color, fill: d.fill, fill_opacity: d.fill_opacity, locked: !!d.locked };
  }
  async function persistShapeFields(d, fields) {
    await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/drawings/${d.id}/update`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields),
    });
  }
  function makeShapeTransformCommand(d, before, after) {
    return {
      label: 'transform-shape',
      undo: async () => { Object.assign(d, before); await persistShapeFields(d, before); redraw(); positionShapeBubble(); },
      redo: async () => { Object.assign(d, after); await persistShapeFields(d, after); redraw(); positionShapeBubble(); },
    };
  }
  async function finalizeNewShape(shapeToSave) {
    if (shapeToSave.kind === 'pen') {
      if (!shapeToSave.data.points || shapeToSave.data.points.length < 2) return;
    } else if (shapeToSave.kind !== 'angle' && Math.hypot(shapeToSave.w, shapeToSave.h) < 4) {
      return;
    }
    const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/drawings`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(serializeShapeForCreate(shapeToSave)),
    });
    const json = await res.json();
    const shape = { ...shapeToSave, id: json.id };
    drawings.push(shape);
    redraw();
    pushCommand(makeAddShapeCommand(shape));
  }

  function makeAddShapeCommand(d) {
    return {
      label: 'add-shape',
      undo: async () => {
        await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/drawings/${d.id}/delete`, { method: 'POST' });
        const idx = drawings.findIndex((s) => s.id === d.id);
        if (idx >= 0) drawings.splice(idx, 1);
        if (selectedShapeId === d.id) deselectShape();
        redraw();
      },
      redo: async () => {
        const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/drawings`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(serializeShapeForCreate(d)),
        });
        const json = await res.json();
        d.id = json.id;
        drawings.push(d);
        redraw();
      },
    };
  }
  function makeDeleteShapeCommand(d, index) {
    return {
      label: 'delete-shape',
      undo: async () => {
        const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/drawings`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(serializeShapeForCreate(d)),
        });
        const json = await res.json();
        d.id = json.id;
        drawings.splice(Math.min(index, drawings.length), 0, d);
        redraw();
      },
      redo: async () => {
        await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/drawings/${d.id}/delete`, { method: 'POST' });
        const idx = drawings.findIndex((s) => s.id === d.id);
        if (idx >= 0) drawings.splice(idx, 1);
        if (selectedShapeId === d.id) deselectShape();
        redraw();
      },
    };
  }
  async function deleteShape(d) {
    const index = drawings.findIndex((s) => s.id === d.id);
    await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/drawings/${d.id}/delete`, { method: 'POST' });
    drawings.splice(index, 1);
    if (selectedShapeId === d.id) deselectShape();
    redraw();
    pushCommand(makeDeleteShapeCommand(d, index));
  }

  function serializePinForCreate(p) {
    return { pin_type: p.pin_type, icon_key: p.icon_key, x: p.x, y: p.y, scale: p.scale || 1, rotation: p.rotation || 0, locked: !!p.locked };
  }
  async function persistPinFields(p, fields) {
    await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/pins/${p.id}/update`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(fields),
    });
  }
  function makePinTransformCommand(p, before, after) {
    return {
      label: 'transform-pin',
      undo: async () => { Object.assign(p, before); renderPins(); await persistPinFields(p, before); },
      redo: async () => { Object.assign(p, after); renderPins(); await persistPinFields(p, after); },
    };
  }
  function makeAddPinCommand(p) {
    return {
      label: 'add-pin',
      undo: async () => {
        await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/pins/${p.id}/delete`, { method: 'POST' });
        const idx = pins.findIndex((x) => x.id === p.id);
        if (idx >= 0) pins.splice(idx, 1);
        if (selectedPinId === p.id) deselectPin();
        renderPins();
      },
      redo: async () => {
        const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/pins`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(serializePinForCreate(p)),
        });
        const json = await res.json();
        p.id = json.id;
        pins.push(p);
        renderPins();
      },
    };
  }
  function makeDeletePinCommand(p, index) {
    return {
      label: 'delete-pin',
      undo: async () => {
        const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/pins`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(serializePinForCreate(p)),
        });
        const json = await res.json();
        p.id = json.id;
        pins.splice(Math.min(index, pins.length), 0, p);
        renderPins();
      },
      redo: async () => {
        await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/pins/${p.id}/delete`, { method: 'POST' });
        const idx = pins.findIndex((x) => x.id === p.id);
        if (idx >= 0) pins.splice(idx, 1);
        if (selectedPinId === p.id) deselectPin();
        renderPins();
      },
    };
  }
  async function deletePinObj(pin) {
    const index = pins.findIndex((p) => p.id === pin.id);
    await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/pins/${pin.id}/delete`, { method: 'POST' });
    pins.splice(index, 1);
    if (selectedPinId === pin.id) deselectPin();
    renderPins();
    pushCommand(makeDeletePinCommand(pin, index));
  }

  // Live color change on a selected shape (color swatches already exist in
  // the toolbar for setting the *next* drawing's color — reuse them here).
  // Live fill-opacity change on a selected shape (0 = no visible fill).
  const fillOpacityInput = document.getElementById('fill-opacity');
  let fillOpacityBefore = null;
  fillOpacityInput.addEventListener('pointerdown', () => {
    if (selectedShapeId == null) return;
    const d = drawings.find((s) => s.id === selectedShapeId);
    fillOpacityBefore = d ? d.fill_opacity : null;
  });
  fillOpacityInput.addEventListener('input', () => {
    if (selectedShapeId == null) return;
    const d = drawings.find((s) => s.id === selectedShapeId);
    if (!d) return;
    d.fill_opacity = getFillOpacity();
    redraw();
  });
  fillOpacityInput.addEventListener('change', async () => {
    if (selectedShapeId == null || fillOpacityBefore == null) return;
    const d = drawings.find((s) => s.id === selectedShapeId);
    if (!d) return;
    const before = { fill_opacity: fillOpacityBefore };
    const after = { fill_opacity: d.fill_opacity };
    fillOpacityBefore = null;
    if (before.fill_opacity === after.fill_opacity) return;
    await persistShapeFields(d, after);
    pushCommand({
      label: 'fill-opacity-shape',
      undo: async () => { d.fill_opacity = before.fill_opacity; await persistShapeFields(d, before); redraw(); },
      redo: async () => { d.fill_opacity = after.fill_opacity; await persistShapeFields(d, after); redraw(); },
    });
  });

  document.querySelectorAll('.color-swatch').forEach((sw) => {
    sw.addEventListener('click', () => {
      const flyoutSwatch = document.getElementById('colors-flyout-swatch');
      if (flyoutSwatch) flyoutSwatch.style.background = sw.dataset.color;
      const colorsFlyout = document.getElementById('colors-flyout');
      if (colorsFlyout) colorsFlyout.classList.remove('open');
      if (selectedShapeId == null) return;
      const d = drawings.find((s) => s.id === selectedShapeId);
      if (!d) return;
      const before = { color: d.color };
      const after = { color: sw.dataset.color };
      d.color = after.color;
      redraw();
      persistShapeFields(d, after);
      pushCommand({
        label: 'recolor-shape',
        undo: async () => { d.color = before.color; await persistShapeFields(d, before); redraw(); },
        redo: async () => { d.color = after.color; await persistShapeFields(d, after); redraw(); },
      });
    });
  });

  // ---- Pin drag (move) ----
  async function unlockPin(pin) {
    const before = { locked: true };
    const after = { locked: false };
    pin.locked = false;
    renderPins();
    await persistPinFields(pin, after);
    pushCommand({
      label: 'unlock-pin',
      undo: async () => { pin.locked = true; await persistPinFields(pin, before); renderPins(); },
      redo: async () => { pin.locked = false; await persistPinFields(pin, after); renderPins(); },
    });
  }
  async function lockPin(pin) {
    const before = { locked: false };
    const after = { locked: true };
    pin.locked = true;
    await persistPinFields(pin, after);
    pushCommand({
      label: 'lock-pin',
      undo: async () => { pin.locked = false; await persistPinFields(pin, before); renderPins(); },
      redo: async () => { pin.locked = true; await persistPinFields(pin, after); renderPins(); },
    });
  }

  pinLayer.addEventListener('mousedown', (e) => {
    const unlockBtn = e.target.closest('.pin-unlock-icon');
    if (unlockBtn) {
      e.preventDefault();
      e.stopPropagation();
      if (readOnly()) return;
      const pinId = Number(unlockBtn.dataset.unlockPin);
      const pin = pins.find((p) => p.id === pinId);
      if (pin) unlockPin(pin);
      return;
    }
    const el = e.target.closest('.map-pin');
    if (!el) return;
    const pin = pins.find((p) => String(p.id) === el.dataset.pinId);
    if (!pin) return;
    // A Player on a locked map can still click a pin to view it (subject
    // to the server's own NPC-detail redaction) -- just not drag/erase it.
    if (readOnly()) { e.preventDefault(); selectPin(pin.id); return; }
    if (pin.locked) return; // locked pins can't be selected, dragged, or erased
    if (currentTool === 'eraser') { e.preventDefault(); deletePinObj(pin); return; }
    if (currentTool !== 'select') return;
    const pos = eventPos(e);
    activeDrag = { type: 'move-pin', pin, el, startPos: pos, startX: pin.x, startY: pin.y, moved: false };
    e.preventDefault();
  });

  pinResizeBtn.addEventListener('mousedown', (e) => {
    if (!selectedPinId) return;
    const pin = pins.find((p) => p.id === selectedPinId);
    const el = pinLayer.querySelector(`[data-pin-id="${selectedPinId}"]`);
    if (!pin || !el) return;
    const rect = el.getBoundingClientRect();
    const centerClientX = rect.left + rect.width / 2;
    const centerClientY = rect.top + rect.height / 2;
    const startDist = Math.max(1, Math.hypot(e.clientX - centerClientX, e.clientY - centerClientY));
    activeDrag = { type: 'resize-pin', pin, el, centerClientX, centerClientY, startDist, startScale: pin.scale || 1 };
    e.preventDefault();
    e.stopPropagation();
  });

  pinRotateBtn.addEventListener('mousedown', (e) => {
    if (!selectedPinId) return;
    const pin = pins.find((p) => p.id === selectedPinId);
    const el = pinLayer.querySelector(`[data-pin-id="${selectedPinId}"]`);
    if (!pin || !el) return;
    const rect = el.getBoundingClientRect();
    const centerClientX = rect.left + rect.width / 2;
    const centerClientY = rect.top + rect.height / 2;
    const startAngle = Math.atan2(e.clientY - centerClientY, e.clientX - centerClientX) * (180 / Math.PI);
    activeDrag = { type: 'rotate-pin', pin, el, centerClientX, centerClientY, startAngle, startRotation: pin.rotation || 0 };
    e.preventDefault();
    e.stopPropagation();
  });

  pinLockBtn.addEventListener('click', async () => {
    const pin = pins.find((p) => p.id === selectedPinId);
    if (!pin) return;
    await lockPin(pin);
    deselectPin();
    renderPins();
  });

  pinDeleteBtn.addEventListener('click', () => {
    const pin = pins.find((p) => p.id === selectedPinId);
    if (pin) deletePinObj(pin);
  });

  pinViewBtn.addEventListener('click', () => {
    if (!selectedPinId) return;
    const pin = pins.find((p) => p.id === selectedPinId);
    if (pin && pin.character_id) openDetailModal(pin.character_id);
  });

  if (pinPcNpcToggle) {
    pinPcNpcToggle.addEventListener('click', async () => {
      const pin = pins.find((p) => p.id === selectedPinId);
      if (!pin) return;
      const nextIsNpc = !pin.is_npc;
      await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/pins/${pin.id}/update`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_npc: nextIsNpc }),
      });
      pin.is_npc = nextIsNpc;
      positionPinBubble(pin.id);
    });
  }

  // ---------------------------------------------------------------------
  // Detail modal (mirrors the battle screen's character viewer)
  // ---------------------------------------------------------------------
  const detailModal = document.getElementById('detail-modal');
  const detailTitle = document.getElementById('detail-modal-title');
  const detailBody = document.getElementById('detail-modal-body');

  async function openDetailModal(characterId) {
    const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/characters/${characterId}/detail`);
    const c = await res.json();
    detailTitle.textContent = c.name;
    const canDeleteSheets = window.IS_DM || c.created_by === window.USER_ID;
    const sheetsHtml = c.sheets.length
      ? `<div class="sheet-thumbs">${c.sheets.map((s) => `<div class="sheet-thumb"><img src="/uploads/${s.image_path}" data-lightbox-src="/uploads/${s.image_path}"${canDeleteSheets ? ` data-delete-url="/campaigns/${window.CAMPAIGN_ID}/characters/${c.id}/sheets/${s.id}/delete"` : ''}></div>`).join('')}</div>`
      : `<p class="hint-text">No sheet images attached.</p>`;
    detailBody.innerHTML = `
      <div class="detail-header">
        <div class="avatar-frame" style="width:72px;height:72px;">${c.avatar_path ? `<img src="/uploads/${c.avatar_path}">` : ''}</div>
        <div>
          <div class="dossier-name">${escapeHtml(c.name)}</div>
          <div class="dossier-meta">Lvl ${c.level} &middot; ${c.group_name || 'Ungrouped'}</div>
        </div>
      </div>
      <div class="dossier-stats" style="margin-top:16px;">
        <div class="stat-pill"><span class="val">${c.str_score}</span><span class="lbl">STR</span></div>
        <div class="stat-pill"><span class="val">${c.dex_score}</span><span class="lbl">DEX</span></div>
        <div class="stat-pill"><span class="val">${c.con_score}</span><span class="lbl">CON</span></div>
        <div class="stat-pill"><span class="val">${c.int_score}</span><span class="lbl">INT</span></div>
        <div class="stat-pill"><span class="val">${c.wis_score}</span><span class="lbl">WIS</span></div>
        <div class="stat-pill"><span class="val">${c.cha_score}</span><span class="lbl">CHA</span></div>
      </div>
      <div class="dossier-hp-ac" style="margin-top:12px;">
        <span class="badge-hp">${c.max_hp} HP</span>
        <span class="badge-ac">AC ${c.armor_class}</span>
      </div>
      <div class="detail-notes">${c.notes_html || '<em>No notes recorded.</em>'}</div>
      <div class="form-section-title" style="margin-top:18px;">Sheets</div>
      ${sheetsHtml}
    `;
    detailModal.hidden = false;
  }
  document.getElementById('close-detail-modal').addEventListener('click', () => { detailModal.hidden = true; });
  detailModal.addEventListener('click', (e) => { if (e.target === detailModal) detailModal.hidden = true; });

  // ---------------------------------------------------------------------
  // Toolbar
  // ---------------------------------------------------------------------
  const MODIFIER_HINT_KEY = 'ledger-map-modifier-hint-dismissed';
  const modifierHintEl = document.getElementById('modifier-hint');
  let modifierHintTimer = null;

  function hideModifierHint() {
    modifierHintEl.hidden = true;
    clearTimeout(modifierHintTimer);
  }
  function dismissModifierHintForGood() {
    hideModifierHint();
    try { localStorage.setItem(MODIFIER_HINT_KEY, '1'); } catch (err) { /* ignore */ }
  }
  function showModifierHintIfNeeded() {
    let alreadySeen = false;
    try { alreadySeen = !!localStorage.getItem(MODIFIER_HINT_KEY); } catch (err) { /* ignore */ }
    if (alreadySeen) return;
    modifierHintEl.hidden = false;
    clearTimeout(modifierHintTimer);
    modifierHintTimer = setTimeout(hideModifierHint, 6000);
  }
  // The hint disappears the moment the user actually uses Shift/Alt, so it
  // never nags again once they've learned the shortcut.
  document.addEventListener('keydown', (e) => {
    if (!modifierHintEl.hidden && (e.key === 'Shift' || e.key === 'Alt')) {
      dismissModifierHintForGood();
    }
  });

  // Zoom/pan hint: shown once on load, dismissed after a timeout or the
  // first time the person actually zooms or pans.
  const NAV_HINT_KEY = 'ledger-map-nav-hint-dismissed';
  const navHintEl = document.getElementById('nav-hint');
  let navHintTimer = null;
  function hideNavHint() {
    navHintEl.hidden = true;
    clearTimeout(navHintTimer);
  }
  function dismissNavHintForGood() {
    hideNavHint();
    try { localStorage.setItem(NAV_HINT_KEY, '1'); } catch (err) { /* ignore */ }
  }
  function showNavHintIfNeeded() {
    if (suppressMainGrid) return; // wait until the setup wizard is done
    let alreadySeen = false;
    try { alreadySeen = !!localStorage.getItem(NAV_HINT_KEY); } catch (err) { /* ignore */ }
    if (alreadySeen) return;
    navHintEl.hidden = false;
    navHintTimer = setTimeout(hideNavHint, 7000);
  }
  showNavHintIfNeeded();

  // "Hold Space to pan" toolbar hint disappears for good the first time
  // it's actually used.
  const SPACE_HINT_KEY = 'ledger-map-space-hint-dismissed';
  const holdSpaceHintEl = document.getElementById('hold-space-hint');
  if (holdSpaceHintEl) {
    let spaceHintSeen = false;
    try { spaceHintSeen = !!localStorage.getItem(SPACE_HINT_KEY); } catch (err) { /* ignore */ }
    if (spaceHintSeen) holdSpaceHintEl.hidden = true;
  }
  function dismissSpaceHint() {
    if (holdSpaceHintEl) holdSpaceHintEl.hidden = true;
    try { localStorage.setItem(SPACE_HINT_KEY, '1'); } catch (err) { /* ignore */ }
  }

  const SHAPE_TOOLS = ['line', 'rect', 'oval', 'pen', 'angle'];
  const styleGroup = document.getElementById('shape-style-group');
  function updateStylePanelVisibility() {
    const show = SHAPE_TOOLS.includes(currentTool) || selectedShapeIds.size > 0;
    styleGroup.hidden = !show;
  }

  document.querySelectorAll('.map-tool-btn[data-tool]').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.map-tool-btn[data-tool]').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      currentTool = btn.dataset.tool;
      deselectPin();
      deselectShape();
      canvas.classList.toggle('tool-eraser', currentTool === 'eraser');
      pinLayer.classList.toggle('draw-mode', SHAPE_TOOLS.includes(currentTool));
      angleDrawState = null;
      previewShape = null;
      if (['line', 'rect', 'oval'].includes(currentTool)) {
        showModifierHintIfNeeded();
      } else {
        hideModifierHint();
      }
      // If the chosen tool lives inside a flyout, mirror its icon onto the
      // flyout's own trigger button so the toolbar shows what's active.
      const flyout = btn.closest('.tool-flyout');
      document.querySelectorAll('.tool-flyout').forEach((f) => f.classList.remove('open'));
      document.querySelectorAll('.flyout-trigger').forEach((t) => t.classList.remove('active'));
      if (flyout) {
        const trigger = flyout.querySelector('.flyout-trigger');
        trigger.classList.add('active');
        trigger.innerHTML = btn.innerHTML;
      }
      updateStylePanelVisibility();
    });
  });

  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable) return;
    const tool = { v: 'select', e: 'eraser' }[e.key.toLowerCase()];
    if (!tool) return;
    const toolBtn = document.querySelector(`.map-tool-btn[data-tool="${tool}"]`);
    if (toolBtn) {
      e.preventDefault();
      toolBtn.click();
    }
  });

  // Flyout open/close: hover already reveals it via CSS, but click support
  // keeps it open for touch/trackpad users and while picking a tool.
  document.querySelectorAll('[data-flyout-trigger]').forEach((trigger) => {
    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      const flyout = trigger.closest('.tool-flyout');
      const isOpen = flyout.classList.contains('open');
      document.querySelectorAll('.tool-flyout').forEach((f) => f.classList.remove('open'));
      if (!isOpen) flyout.classList.add('open');
    });
  });
  document.addEventListener('click', (e) => {
    if (e.target.closest('.tool-flyout')) return;
    document.querySelectorAll('.tool-flyout').forEach((f) => f.classList.remove('open'));
  });

  function getColor() { return document.getElementById('map-color-input').value; }
  function getFillOpacity() { return parseInt(document.getElementById('fill-opacity').value, 10) / 100; }

  document.getElementById('clear-drawings-btn').addEventListener('click', async () => {
    if (!drawings.length) return;
    const ok = window.confirmAction ? await window.confirmAction('Clear all drawings on this map?') : confirm('Clear all drawings on this map?');
    if (!ok) return;
    const snapshot = drawings.map((d) => ({ ...d }));
    await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/drawings/clear`, { method: 'POST' });
    drawings = [];
    deselectShape();
    redraw();
    pushCommand({
      label: 'clear-drawings',
      undo: async () => {
        const restored = [];
        for (const s of snapshot) {
          const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/drawings`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(serializeShapeForCreate(s)),
          });
          const json = await res.json();
          restored.push({ ...s, id: json.id });
        }
        drawings = restored;
        redraw();
      },
      redo: async () => {
        await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/drawings/clear`, { method: 'POST' });
        drawings = [];
        deselectShape();
        redraw();
      },
    });
  });

  const linkBattleCheckbox = document.getElementById('link-battle-checkbox');
  if (linkBattleCheckbox) {
    linkBattleCheckbox.addEventListener('change', async (e) => {
      linkedToBattle = e.target.checked;
      await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ linked_to_battle: linkedToBattle }),
      });
      await loadPins();
      setupPolling();
    });
  }

  const lockMapCheckbox = document.getElementById('lock-map-checkbox');
  if (lockMapCheckbox) {
    lockMapCheckbox.addEventListener('change', async (e) => {
      lockedForPlayers = e.target.checked;
      await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ locked_for_players: lockedForPlayers }),
      });
    });
  }

  function setupPolling() {
    if (pollTimer) clearInterval(pollTimer);
    if (linkedToBattle) pollTimer = setInterval(loadPins, 4000);
  }

  function applyMapLockVisuals() {
    contentArea.classList.toggle('map-readonly', readOnly());
    if (lockedBanner) lockedBanner.hidden = !lockedForPlayers;
  }

  // Every open map tab polls this small state endpoint so grid settings,
  // the lock, and the battle-link toggle all take effect live -- without
  // this, another viewer's tab only ever picks up a DM's changes on their
  // next full reload. Skipped while this tab's own grid-settings dialog is
  // open so it can't clobber someone's in-progress edit there.
  setInterval(async () => {
    if (!setupOverlay.hidden) return;
    try {
      const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/state`);
      if (!res.ok) return;
      const s = await res.json();
      let gridChanged = false;

      const nowLocked = !!s.locked_for_players;
      if (nowLocked !== lockedForPlayers) {
        lockedForPlayers = nowLocked;
        applyMapLockVisuals();
        if (lockMapCheckbox) lockMapCheckbox.checked = lockedForPlayers;
      }

      const nowLinked = !!s.linked_to_battle;
      if (nowLinked !== linkedToBattle) {
        linkedToBattle = nowLinked;
        if (linkBattleCheckbox) linkBattleCheckbox.checked = linkedToBattle;
        setupPolling();
        if (linkedToBattle) loadPins();
      }

      if (s.grid_size !== gridSize) { gridSize = s.grid_size; gridChanged = true; }
      if (s.grid_color !== gridColor) { gridColor = s.grid_color; gridChanged = true; }
      if (!!s.grid_visible !== gridVisible) { gridVisible = !!s.grid_visible; gridChanged = true; }
      if (s.grid_offset_x !== gridOffsetX) { gridOffsetX = s.grid_offset_x; gridChanged = true; }
      if (s.grid_offset_y !== gridOffsetY) { gridOffsetY = s.grid_offset_y; gridChanged = true; }
      if (!!s.snap_to_grid !== snapToGrid) { snapToGrid = !!s.snap_to_grid; }
      if (gridChanged) { renderPins(); redraw(); }
    } catch (e) { /* transient network hiccup -- try again next tick */ }
  }, 3000);

  // Drawings/shapes get the same live-sync treatment, polled separately
  // from the lighter settings check above. Skipped while this tab is
  // mid-drag or mid-draw so a poll can't tear a shape out from under an
  // interaction already in progress.
  setInterval(() => {
    if (activeDrag || previewShape || !setupOverlay.hidden) return;
    loadDrawings();
  }, 3000);

  // ---------------------------------------------------------------------
  // Shape geometry helpers for the draw tools (shift = from-center, alt = 1:1 lock)
  // ---------------------------------------------------------------------
  function computeBoxGeometry(start, current, shiftKey, altKey) {
    let w = current.x - start.x;
    let h = current.y - start.y;
    if (altKey) {
      const mag = Math.max(Math.abs(w), Math.abs(h));
      w = (w < 0 ? -1 : 1) * mag;
      h = (h < 0 ? -1 : 1) * mag;
    }
    if (shiftKey) {
      const width = Math.abs(w) * 2;
      const height = Math.abs(h) * 2;
      return { cx: start.x, cy: start.y, w: width, h: height };
    }
    const x = Math.min(start.x, start.x + w), y = Math.min(start.y, start.y + h);
    const ww = Math.abs(w), hh = Math.abs(h);
    return { cx: x + ww / 2, cy: y + hh / 2, w: ww, h: hh };
  }

  function computeLineGeometry(start, current, shiftKey, altKey) {
    let x2 = current.x, y2 = current.y;
    if (altKey) {
      const dx = x2 - start.x, dy = y2 - start.y;
      const dist = Math.hypot(dx, dy);
      const angle = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
      x2 = start.x + Math.cos(angle) * dist;
      y2 = start.y + Math.sin(angle) * dist;
    }
    let x1 = start.x, y1 = start.y;
    if (shiftKey) {
      x1 = start.x - (x2 - start.x);
      y1 = start.y - (y2 - start.y);
    }
    const minX = Math.min(x1, x2), maxX = Math.max(x1, x2);
    const minY = Math.min(y1, y2), maxY = Math.max(y1, y2);
    const w = Math.max(1e-3, maxX - minX), h = Math.max(1e-3, maxY - minY);
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    return { cx, cy, w, h, data: { x1n: (x1 - cx) / w, y1n: (y1 - cy) / h, x2n: (x2 - cx) / w, y2n: (y2 - cy) / h } };
  }

  function finalizePenShape(points) {
    const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const w = Math.max(1e-3, maxX - minX), h = Math.max(1e-3, maxY - minY);
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    const norm = points.map(([x, y]) => [(x - cx) / w, (y - cy) / h]);
    return { cx, cy, w, h, data: { points: norm } };
  }

  // ---------------------------------------------------------------------
  // Canvas interactions
  // ---------------------------------------------------------------------
  let drawState = null;   // creating a brand-new shape, or moving the grid
  let angleDrawState = null; // {vertex} while placing an angle/cone shape (click, then click again)
  let activeDrag = null;  // manipulating an existing shape or pin

  canvas.addEventListener('mousedown', (e) => {
    if (readOnly()) return;
    const pos = eventPos(e);

    if (currentTool === 'select') {
      // Clicking the hover-unlock icon on a locked shape takes priority.
      if (hoveredLockedShapeId != null) {
        const locked = drawings.find((s) => s.id === hoveredLockedShapeId);
        if (locked && Math.hypot(pos.x - locked.cx, pos.y - locked.cy) < lockIconRadius()) {
          unlockShape(locked);
          hoveredLockedShapeId = null;
          return;
        }
      }
      if (selectedShapeId != null && shapeHandleMode === 'resize') {
        const d = drawings.find((s) => s.id === selectedShapeId);
        if (d) {
          const corner = hitTestResizeHandle(d, pos);
          if (corner != null) {
            const before = { cx: d.cx, cy: d.cy, w: d.w, h: d.h, rotation: d.rotation };
            if (d.kind === 'angle') {
              // The vertex must stay put — just scale the reach (radius)
              // by distance from it, rather than the generic opposite-
              // corner anchor math, which assumes a center-based box.
              const startDist = Math.max(1, Math.hypot(pos.x - d.cx, pos.y - d.cy));
              activeDrag = { type: 'resize-angle-radius', d, startDist, startW: d.w, before };
              return;
            }
            const corners = getShapeCorners(d);
            const anchor = corners[(corner + 2) % 4];
            const axes = unitAxes(d.rotation);
            activeDrag = { type: 'resize-shape', d, anchor, axes, before };
            return;
          }
        }
      }
      if (selectedShapeId != null && shapeHandleMode === 'rotate') {
        const d = drawings.find((s) => s.id === selectedShapeId);
        if (d && hitTestRotateHandle(d, pos)) {
          const before = { cx: d.cx, cy: d.cy, w: d.w, h: d.h, rotation: d.rotation };
          const startAngle = Math.atan2(pos.y - d.cy, pos.x - d.cx) * (180 / Math.PI);
          activeDrag = { type: 'rotate-shape', d, before, startAngle, startRotation: d.rotation || 0 };
          return;
        }
      }
      if (selectedShapeId != null && shapeHandleMode === 'angle-adjust') {
        const d = drawings.find((s) => s.id === selectedShapeId);
        if (d && d.kind === 'angle') {
          const handles = getAngleHandlePositions(d);
          const hs = Math.max(14, canvas.width / 80);
          const hitIdx = handles.findIndex((p) => Math.hypot(pos.x - p.x, pos.y - p.y) < hs);
          if (hitIdx !== -1) {
            const before = { data: { ...d.data } };
            activeDrag = { type: 'adjust-angle-sweep', d, before };
            return;
          }
        }
      }
      const multiKey = e.ctrlKey || e.metaKey;
      for (let i = drawings.length - 1; i >= 0; i--) {
        const cand = drawings[i];
        if (cand.locked) continue;
        if (hitTestShape(cand, pos)) {
          if (multiKey) {
            toggleShapeSelection(cand.id);
          } else if (!selectedShapeIds.has(cand.id)) {
            selectShape(cand.id);
          }
          beginGroupMoveDrag(pos);
          return;
        }
      }
      if (!multiKey) deselectShape();
      return;
    }

    if (currentTool === 'eraser') {
      for (let i = drawings.length - 1; i >= 0; i--) {
        if (drawings[i].locked) continue;
        if (hitTestShape(drawings[i], pos)) { deleteShape(drawings[i]); return; }
      }
      return;
    }

    if (currentTool.startsWith('prop-')) {
      const iconKey = currentTool.replace('prop-', '');
      placeProp(pos, iconKey);
      return;
    }
    if (currentTool === 'angle') {
      if (!angleDrawState) {
        angleDrawState = { vertex: pos };
      } else {
        const vertex = angleDrawState.vertex;
        angleDrawState = null;
        previewShape = null;
        const dist = Math.max(20, Math.hypot(pos.x - vertex.x, pos.y - vertex.y));
        const rotation = Math.atan2(pos.y - vertex.y, pos.x - vertex.x) * (180 / Math.PI);
        finalizeNewShape({
          kind: 'angle', cx: vertex.x, cy: vertex.y, w: dist, h: dist, rotation,
          data: { sweepDeg: 60 }, color: getColor(), fill: true, fill_opacity: getFillOpacity(),
        });
      }
      return;
    }
    if (['line', 'rect', 'oval'].includes(currentTool)) {
      drawState = { tool: currentTool, start: pos };
    } else if (currentTool === 'pen') {
      drawState = { tool: 'pen', points: [[pos.x, pos.y]] };
    }
  });

  function beginGroupMoveDrag(pos) {
    const ids = [...selectedShapeIds];
    const starts = new Map();
    ids.forEach((id) => {
      const d = drawings.find((s) => s.id === id);
      if (d) starts.set(id, { cx: d.cx, cy: d.cy });
    });
    activeDrag = { type: 'group-move-shapes', ids, starts, startPos: pos, moved: false };
  }

  canvas.addEventListener('mousemove', (e) => {
    // Hover detection for the "click to unlock" icon (select tool only).
    if (currentTool === 'select' && !drawState && !activeDrag) {
      const pos = eventPos(e);
      let hit = null;
      for (let i = drawings.length - 1; i >= 0; i--) {
        if (drawings[i].locked && hitTestShape(drawings[i], pos)) { hit = drawings[i].id; break; }
      }
      if (hit !== hoveredLockedShapeId) { hoveredLockedShapeId = hit; redraw(); }
    }

    if (currentTool === 'angle' && angleDrawState) {
      const pos = eventPos(e);
      const vertex = angleDrawState.vertex;
      const dist = Math.max(20, Math.hypot(pos.x - vertex.x, pos.y - vertex.y));
      const rotation = Math.atan2(pos.y - vertex.y, pos.x - vertex.x) * (180 / Math.PI);
      previewShape = {
        kind: 'angle', cx: vertex.x, cy: vertex.y, w: dist, h: dist, rotation,
        data: { sweepDeg: 60 }, color: getColor(), fill: true, fill_opacity: getFillOpacity(),
      };
      redraw();
      return;
    }

    if (!drawState) return;
    const pos = eventPos(e);

    if (drawState.tool === 'pen') {
      const pts = drawState.points;
      const last = pts[pts.length - 1];
      if (Math.hypot(pos.x - last[0], pos.y - last[1]) > 2) pts.push([pos.x, pos.y]);
      if (pts.length > 1) {
        const g = finalizePenShape(pts);
        previewShape = { kind: 'pen', cx: g.cx, cy: g.cy, w: g.w, h: g.h, rotation: 0, data: g.data, color: getColor(), fill: false, fill_opacity: 1 };
      }
      redraw();
      return;
    }
    if (drawState.tool === 'line') {
      const g = computeLineGeometry(drawState.start, pos, e.shiftKey, e.altKey);
      previewShape = { kind: 'line', cx: g.cx, cy: g.cy, w: g.w, h: g.h, rotation: 0, data: g.data, color: getColor(), fill: false, fill_opacity: 1 };
      redraw();
    } else if (drawState.tool === 'rect' || drawState.tool === 'oval') {
      const g = computeBoxGeometry(drawState.start, pos, e.shiftKey, e.altKey);
      previewShape = { kind: drawState.tool, cx: g.cx, cy: g.cy, w: g.w, h: g.h, rotation: 0, data: {}, color: getColor(), fill: true, fill_opacity: getFillOpacity() };
      redraw();
    }
  });

  window.addEventListener('mousemove', (e) => {
    if (!activeDrag) return;

    if (activeDrag.type === 'group-move-shapes') {
      const pos = eventPos(e);
      const dx = pos.x - activeDrag.startPos.x, dy = pos.y - activeDrag.startPos.y;
      if (Math.hypot(dx, dy) > 1) activeDrag.moved = true;
      activeDrag.ids.forEach((id) => {
        const d = drawings.find((s) => s.id === id);
        const start = activeDrag.starts.get(id);
        if (d && start) { d.cx = start.cx + dx; d.cy = start.cy + dy; }
      });
      redraw();
      if (selectedShapeId != null) positionShapeBubble();
      return;
    }
    if (activeDrag.type === 'resize-shape-uniform') {
      const pos = eventPos(e);
      const d = activeDrag.d;
      const dist = Math.max(1, Math.hypot(pos.x - d.cx, pos.y - d.cy));
      const ratio = dist / activeDrag.startDist;
      d.w = Math.max(6, activeDrag.startW * ratio);
      d.h = Math.max(6, activeDrag.startH * ratio);
      redraw();
      positionShapeBubble();
      return;
    }
    if (activeDrag.type === 'resize-angle-radius') {
      const pos = eventPos(e);
      const d = activeDrag.d;
      const dist = Math.max(1, Math.hypot(pos.x - d.cx, pos.y - d.cy));
      const ratio = dist / activeDrag.startDist;
      d.w = Math.max(20, activeDrag.startW * ratio);
      d.h = d.w;
      redraw();
      positionShapeBubble();
      return;
    }
    if (activeDrag.type === 'resize-shape') {
      const pos = eventPos(e);
      const d = activeDrag.d;
      const axes = activeDrag.axes;
      const vec = { x: pos.x - activeDrag.anchor.x, y: pos.y - activeDrag.anchor.y };
      let du = vec.x * axes.u.x + vec.y * axes.u.y;
      let dv = vec.x * axes.v.x + vec.y * axes.v.y;
      if (e.altKey) {
        // Lock to a 1:1 aspect ratio, using whichever axis moved further.
        const mag = Math.max(Math.abs(du), Math.abs(dv));
        du = (du < 0 ? -1 : 1) * mag;
        dv = (dv < 0 ? -1 : 1) * mag;
      }
      if (e.shiftKey) {
        // Resize symmetrically from the shape's original center instead of
        // from the opposite corner.
        d.w = Math.max(6, Math.abs(du) * 2);
        d.h = Math.max(6, Math.abs(dv) * 2);
        d.cx = activeDrag.before.cx;
        d.cy = activeDrag.before.cy;
      } else {
        // Reconstruct the (possibly alt-adjusted) dragged-corner position so
        // the center is derived from the same du/dv used for w/h, not the
        // raw mouse position.
        const effX = activeDrag.anchor.x + axes.u.x * du + axes.v.x * dv;
        const effY = activeDrag.anchor.y + axes.u.y * du + axes.v.y * dv;
        d.w = Math.max(6, Math.abs(du));
        d.h = Math.max(6, Math.abs(dv));
        d.cx = (activeDrag.anchor.x + effX) / 2;
        d.cy = (activeDrag.anchor.y + effY) / 2;
      }
      redraw();
      positionShapeBubble();
      return;
    }
    if (activeDrag.type === 'rotate-shape') {
      const pos = eventPos(e);
      const d = activeDrag.d;
      const angleNow = Math.atan2(pos.y - d.cy, pos.x - d.cx) * (180 / Math.PI);
      d.rotation = activeDrag.startRotation + (angleNow - activeDrag.startAngle);
      redraw();
      positionShapeBubble();
      return;
    }
    if (activeDrag.type === 'adjust-angle-sweep') {
      const pos = eventPos(e);
      const d = activeDrag.d;
      const local = naturalToLocalOffset(d, pos.x, pos.y);
      const angle = Math.atan2(local.y, local.x) * (180 / Math.PI);
      const newSweep = Math.max(5, Math.min(170, Math.abs(angle) * 2));
      d.data = { ...d.data, sweepDeg: newSweep };
      redraw();
      positionShapeBubble();
      return;
    }
    if (activeDrag.type === 'move-pin') {
      const pos = eventPos(e);
      const pin = pins.find((p) => p.id === activeDrag.pin.id);
      if (!pin) return;
      const dx = pos.x - activeDrag.startPos.x, dy = pos.y - activeDrag.startPos.y;
      if (Math.hypot(dx, dy) > 3) activeDrag.moved = true;
      const snapped = snapPointToGridCenter(activeDrag.startX + dx, activeDrag.startY + dy);
      pin.x = snapped.x;
      pin.y = snapped.y;
      applyPinStyle(pin, activeDrag.el);
      if (selectedPinId === pin.id) positionPinBubble(pin.id);
      return;
    }
    if (activeDrag.type === 'resize-pin') {
      const dist = Math.max(1, Math.hypot(e.clientX - activeDrag.centerClientX, e.clientY - activeDrag.centerClientY));
      let newScale = activeDrag.startScale * (dist / activeDrag.startDist);
      newScale = Math.max(0.3, Math.min(5, newScale));
      const pin = pins.find((p) => p.id === activeDrag.pin.id);
      if (!pin) return;
      pin.scale = newScale;
      applyPinStyle(pin, activeDrag.el);
      positionPinBubble(pin.id);
      return;
    }
    if (activeDrag.type === 'rotate-pin') {
      const angleNow = Math.atan2(e.clientY - activeDrag.centerClientY, e.clientX - activeDrag.centerClientX) * (180 / Math.PI);
      const pin = pins.find((p) => p.id === activeDrag.pin.id);
      if (!pin) return;
      pin.rotation = activeDrag.startRotation + (angleNow - activeDrag.startAngle);
      applyPinStyle(pin, activeDrag.el);
      positionPinBubble(pin.id);
      return;
    }
  });

  window.addEventListener('mouseup', async (e) => {
    if (activeDrag) {
      const drag = activeDrag;
      activeDrag = null;

      if (drag.type === 'group-move-shapes') {
        if (drag.moved) {
          const commands = [];
          for (const id of drag.ids) {
            const d = drawings.find((s) => s.id === id);
            const start = drag.starts.get(id);
            if (!d || !start) continue;
            const before = { cx: start.cx, cy: start.cy, w: d.w, h: d.h, rotation: d.rotation };
            const after = { cx: d.cx, cy: d.cy, w: d.w, h: d.h, rotation: d.rotation };
            commands.push({ d, before, after });
            await persistShapeFields(d, after);
          }
          pushCommand({
            label: 'group-move-shapes',
            undo: async () => { for (const c of commands) { Object.assign(c.d, c.before); await persistShapeFields(c.d, c.before); } redraw(); positionShapeBubble(); },
            redo: async () => { for (const c of commands) { Object.assign(c.d, c.after); await persistShapeFields(c.d, c.after); } redraw(); positionShapeBubble(); },
          });
        }
        return;
      }
      if (drag.type === 'resize-shape' || drag.type === 'rotate-shape' || drag.type === 'resize-shape-uniform' || drag.type === 'resize-angle-radius') {
        const d = drag.d;
        const after = { cx: d.cx, cy: d.cy, w: d.w, h: d.h, rotation: d.rotation };
        pushCommand(makeShapeTransformCommand(d, drag.before, after));
        await persistShapeFields(d, after);
        return;
      }
      if (drag.type === 'adjust-angle-sweep') {
        const d = drag.d;
        const before = drag.before;
        const after = { data: { ...d.data } };
        pushCommand({
          label: 'adjust-angle-sweep',
          undo: async () => { d.data = { ...before.data }; await persistShapeFields(d, before); redraw(); positionShapeBubble(); },
          redo: async () => { d.data = { ...after.data }; await persistShapeFields(d, after); redraw(); positionShapeBubble(); },
        });
        await persistShapeFields(d, after);
        return;
      }
      if (drag.type === 'move-pin') {
        const pin = pins.find((p) => p.id === drag.pin.id);
        const wasClick = !drag.moved;
        if (pin && drag.moved) {
          const before = { x: drag.startX, y: drag.startY };
          const after = { x: pin.x, y: pin.y };
          pushCommand(makePinTransformCommand(pin, before, after));
          await persistPinFields(pin, after);
        }
        if (wasClick) selectPin(drag.pin.id);
        return;
      }
      if (drag.type === 'resize-pin') {
        const pin = pins.find((p) => p.id === drag.pin.id);
        if (pin) {
          const before = { scale: drag.startScale };
          const after = { scale: pin.scale };
          pushCommand(makePinTransformCommand(pin, before, after));
          await persistPinFields(pin, after);
        }
        return;
      }
      if (drag.type === 'rotate-pin') {
        const pin = pins.find((p) => p.id === drag.pin.id);
        if (pin) {
          const before = { rotation: drag.startRotation };
          const after = { rotation: pin.rotation };
          pushCommand(makePinTransformCommand(pin, before, after));
          await persistPinFields(pin, after);
        }
        return;
      }
      return;
    }

    if (!drawState) return;
    const state = drawState;
    drawState = null;

    if (!previewShape) return;
    const shapeToSave = previewShape;
    previewShape = null;
    redraw();
    await finalizeNewShape(shapeToSave);
  });

  async function placeProp(pos, iconKey) {
    const snapped = snapPointToGridCenter(pos.x, pos.y);
    const res = await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/pins`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin_type: 'prop', icon_key: iconKey, x: snapped.x, y: snapped.y, scale: 1.0, rotation: 0 }),
    });
    const json = await res.json();
    const pin = { id: json.id, pin_type: 'prop', icon_key: iconKey, x: snapped.x, y: snapped.y, scale: 1.0, rotation: 0, locked: false, custom_name: null };
    pins.push(pin);
    renderPins();
    pushCommand(makeAddPinCommand(pin));
    // Drop back into select mode and prompt straight away for a name.
    const selectBtn = document.querySelector('.map-tool-btn[data-tool="select"]');
    if (selectBtn) selectBtn.click();
    selectPin(pin.id);
    openPinRename(pin.id);
  }

  // ---------------------------------------------------------------------
  // Grid/canvas settings lightbox. Shown mandatorily once per map on first
  // open; reopenable afterward (non-mandatory) from the toolbar's Grid
  // Settings button — same component both times, not two separate UIs.
  // ---------------------------------------------------------------------
  const setupOverlay = document.getElementById('map-setup-overlay');
  const contentArea = document.getElementById('map-content-area');
  const lockedBanner = document.getElementById('map-locked-banner');
  const setupVisibleCheckbox = document.getElementById('setup-grid-visible');
  const setupSizeSlider = document.getElementById('setup-grid-size');
  const setupSizeValue = document.getElementById('setup-grid-size-value');
  const setupSnapCheckbox = document.getElementById('setup-snap-grid');
  const setupConfirmBtn = document.getElementById('setup-confirm-btn');
  const setupCloseBtn = document.getElementById('setup-close-btn');
  const setupTitle = document.getElementById('setup-title');
  const previewCanvas = document.getElementById('setup-preview-canvas');
  const previewCtx = previewCanvas.getContext('2d');
  let snapToGrid = !!initData.snap_to_grid;
  let gridSettingsBefore = null; // snapshot for cancel/revert when reopened

  function syncSetupControlsFromState() {
    gridSize = Math.max(25, Math.min(100, gridSize || 50));
    setupSizeSlider.value = gridSize;
    setupSizeValue.textContent = `${gridSize}px`;
    setupSizeSlider.disabled = !gridVisible;
    setupVisibleCheckbox.checked = gridVisible;
    setupSnapCheckbox.checked = snapToGrid;
    document.querySelectorAll('.grid-color-swatch').forEach((sw) => {
      sw.classList.toggle('active', sw.dataset.gridColor === gridColor);
    });
  }

  // A small, crisp (never blurred) preview of the actual map + grid,
  // rendered at a downscaled resolution inside the lightbox itself.
  function drawSetupPreview() {
    const maxW = 900, maxH = 640;
    let w = naturalWidth, h = naturalHeight;
    const scale = Math.min(maxW / w, maxH / h, 1);
    w = Math.round(w * scale);
    h = Math.round(h * scale);
    previewCanvas.width = w;
    previewCanvas.height = h;
    previewCtx.clearRect(0, 0, w, h);
    if (bgLoaded) previewCtx.drawImage(bgImage, 0, 0, w, h);
    drawGridOnto(previewCtx, w, h, gridSize * scale, gridOffsetX * scale, gridOffsetY * scale, gridColor, gridVisible);
  }

  function openGridSettings(mandatory) {
    gridSettingsBefore = { gridSize, gridColor, gridVisible, gridOffsetX, gridOffsetY, snapToGrid };
    setupOverlay.hidden = false;
    contentArea.classList.add('blurred');
    setupCloseBtn.hidden = !!mandatory;
    setupTitle.textContent = mandatory ? 'Set up your grid' : 'Grid & canvas settings';
    if (mandatory) suppressMainGrid = true;
    syncSetupControlsFromState();
    drawSetupPreview();
    redraw();
  }

  function closeGridSettings(reverted) {
    if (reverted && gridSettingsBefore) {
      gridSize = gridSettingsBefore.gridSize;
      gridColor = gridSettingsBefore.gridColor;
      gridVisible = gridSettingsBefore.gridVisible;
      gridOffsetX = gridSettingsBefore.gridOffsetX;
      gridOffsetY = gridSettingsBefore.gridOffsetY;
      snapToGrid = gridSettingsBefore.snapToGrid;
    }
    suppressMainGrid = false;
    setupOverlay.hidden = true;
    contentArea.classList.remove('blurred');
    renderPins();
    redraw();
  }

  setupVisibleCheckbox.addEventListener('change', () => {
    gridVisible = setupVisibleCheckbox.checked;
    setupSizeSlider.disabled = !gridVisible;
    drawSetupPreview();
    redraw();
  });
  setupSizeSlider.addEventListener('input', () => {
    gridSize = parseInt(setupSizeSlider.value, 10);
    setupSizeValue.textContent = `${gridSize}px`;
    drawSetupPreview();
    redraw();
  });
  setupSnapCheckbox.addEventListener('change', () => {
    snapToGrid = setupSnapCheckbox.checked;
  });
  document.querySelectorAll('.grid-color-swatch').forEach((sw) => {
    sw.addEventListener('click', () => {
      gridColor = sw.dataset.gridColor;
      document.querySelectorAll('.grid-color-swatch').forEach((b) => b.classList.remove('active'));
      sw.classList.add('active');
      drawSetupPreview();
      redraw();
    });
  });

  // Hold and drag on the preview to move the grid without leaving the
  // lightbox — dragging in preview-pixel space is converted back to
  // natural-image pixels since the preview is downscaled.
  let previewDrag = null;
  previewCanvas.addEventListener('mousedown', (e) => {
    previewDrag = { startX: e.clientX, startY: e.clientY, origOffsetX: gridOffsetX, origOffsetY: gridOffsetY };
  });
  window.addEventListener('mousemove', (e) => {
    if (!previewDrag) return;
    const rect = previewCanvas.getBoundingClientRect();
    const scale = naturalWidth / rect.width;
    gridOffsetX = previewDrag.origOffsetX + (e.clientX - previewDrag.startX) * scale;
    gridOffsetY = previewDrag.origOffsetY + (e.clientY - previewDrag.startY) * scale;
    drawSetupPreview();
    redraw();
  });
  window.addEventListener('mouseup', () => {
    previewDrag = null;
  });

  // The image may still be loading when the overlay first appears.
  const prevOnLoad = bgImage.onload;
  bgImage.onload = () => { if (prevOnLoad) prevOnLoad(); drawSetupPreview(); };

  setupCloseBtn.addEventListener('click', () => closeGridSettings(true));

  setupConfirmBtn.addEventListener('click', async () => {
    await fetch(`/campaigns/${window.CAMPAIGN_ID}/api/maps/${mapId}/settings`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        grid_size: gridSize, grid_color: gridColor, grid_visible: gridVisible, grid_setup_done: true,
        grid_offset_x: Math.round(gridOffsetX), grid_offset_y: Math.round(gridOffsetY), snap_to_grid: snapToGrid,
      }),
    });
    closeGridSettings(false);
    showNavHintIfNeeded();
  });

  document.getElementById('grid-settings-btn').addEventListener('click', () => openGridSettings(false));

  if (!initData.grid_setup_done) openGridSettings(true);

  // ---------------------------------------------------------------------
  // Zoom (mouse wheel + trackpad pinch, centered on the cursor) and
  // Space+drag panning. The grid is baked into the canvas raster, so it
  // scales along with everything else automatically — no separate logic
  // needed to keep it "infinite"; it simply covers whatever of the map is
  // visible at any zoom/pan position.
  // ---------------------------------------------------------------------
  const ZOOM_MIN = 0.25, ZOOM_MAX = 6;
  let zoomLevel = 1;
  let panX = 0, panY = 0;
  const zoomLevelLabel = document.getElementById('zoom-level-label');
  const zoomResetBtn = document.getElementById('zoom-reset-btn');

  function applyZoomTransform() {
    stageInner.style.transform = `translate(${panX}px, ${panY}px) scale(${zoomLevel})`;
    stageInner.style.setProperty('--ledger-zoom-inv', String(1 / zoomLevel));
    if (zoomLevelLabel) zoomLevelLabel.textContent = `${Math.round(zoomLevel * 100)}%`;
  }

  stageWrap.addEventListener('wheel', (e) => {
    if (!(e.ctrlKey || e.metaKey)) return; // plain scroll is left alone; Ctrl/Cmd+wheel (or trackpad pinch) zooms
    e.preventDefault();
    dismissNavHintForGood();
    const rect = stageWrap.getBoundingClientRect();
    const vx = e.clientX - rect.left, vy = e.clientY - rect.top;
    const zoomFactor = Math.exp(-e.deltaY * 0.0018);
    const newZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoomLevel * zoomFactor));
    const factor = newZoom / zoomLevel;
    panX = vx - (vx - panX) * factor;
    panY = vy - (vy - panY) * factor;
    zoomLevel = newZoom;
    applyZoomTransform();
  }, { passive: false });

  zoomResetBtn.addEventListener('click', () => {
    zoomLevel = 1; panX = 0; panY = 0;
    applyZoomTransform();
  });

  // Hold Space to pan by dragging, regardless of the active tool — a
  // temporary override, like Figma/Photoshop's spacebar-pan.
  let spaceHeld = false;
  let panDrag = null;
  document.addEventListener('keydown', (e) => {
    if (e.code === 'Space') {
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return;
      e.preventDefault();
      if (!spaceHeld) { spaceHeld = true; stageWrap.classList.add('panning'); dismissNavHintForGood(); dismissSpaceHint(); }
    }
  });
  document.addEventListener('keyup', (e) => {
    if (e.code === 'Space') {
      spaceHeld = false;
      stageWrap.classList.remove('panning', 'panning-active');
    }
  });
  stageWrap.addEventListener('mousedown', (e) => {
    if (!spaceHeld) return;
    e.preventDefault();
    e.stopPropagation();
    panDrag = { startX: e.clientX, startY: e.clientY, origPanX: panX, origPanY: panY };
    stageWrap.classList.add('panning-active');
  }, true);
  window.addEventListener('mousemove', (e) => {
    if (!panDrag) return;
    panX = panDrag.origPanX + (e.clientX - panDrag.startX);
    panY = panDrag.origPanY + (e.clientY - panDrag.startY);
    applyZoomTransform();
  });
  window.addEventListener('mouseup', () => {
    if (!panDrag) return;
    panDrag = null;
    stageWrap.classList.remove('panning-active');
  });

  // ---------------------------------------------------------------------
  // Sticky toolbar: detaches with a shadow once it hits the top of the
  // viewport on scroll, and returns to its normal in-flow look once
  // scrolled back up. Positioning is handled by CSS (position: sticky);
  // this just toggles the "pinned" look at the exact moment it sticks.
  // ---------------------------------------------------------------------
  const mapToolbar = document.getElementById('map-toolbar');
  const toolbarSentinel = document.getElementById('map-toolbar-sentinel');
  if (mapToolbar && toolbarSentinel && 'IntersectionObserver' in window) {
    const toolbarPinObserver = new IntersectionObserver(
      ([entry]) => mapToolbar.classList.toggle('is-pinned', !entry.isIntersecting),
      { threshold: 0 }
    );
    toolbarPinObserver.observe(toolbarSentinel);
  }

  // ---------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------
  updateHistoryButtons();
  (async function init() {
    await loadDrawings();
    await loadPins();
    setupPolling();
  })();
})();
