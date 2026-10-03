// Shared image lightbox. Any image can opt in by adding:
//   data-lightbox-src="<full image url>"
//   data-delete-url="<optional POST endpoint to delete this image>"
// Click anywhere to toggle zoom in/out (zooming toward the click point);
// while zoomed, hold and drag to pan around the image.

(function () {
  const overlay = document.getElementById('image-lightbox');
  if (!overlay) return;

  const img = document.getElementById('lightbox-img');
  const closeBtn = document.getElementById('lightbox-close-btn');
  const deleteBtn = document.getElementById('lightbox-delete-btn');
  const prevBtn = document.getElementById('lightbox-prev-btn');
  const nextBtn = document.getElementById('lightbox-next-btn');
  const stage = overlay.querySelector('.lightbox-stage');

  const ZOOM_SCALE = 2.4;   // what a click zooms to
  const MAX_SCALE = 8;      // how far the mouse wheel can zoom in
  const DRAG_THRESHOLD = 6; // px of movement before a mousedown counts as a drag, not a click

  let zoomed = false;
  let scale = 1;            // current zoom; `zoomed` is simply scale > 1
  let panX = 0;
  let panY = 0;
  let dragging = false;
  let dragStart = { x: 0, y: 0 };
  let panStart = { x: 0, y: 0 };
  let movedDistance = 0;
  let currentDeleteUrl = null;
  let currentImages = [];
  let currentIndex = -1;
  let baseRect = null; // the image's unscaled rect, captured at zoom-in time

  function applyTransform() {
    img.style.transform = zoomed
      ? `translate(${panX}px, ${panY}px) scale(${scale})`
      : 'translate(0, 0) scale(1)';
    img.classList.toggle('zoomed', zoomed);
    stage.classList.toggle('is-dragging', dragging && zoomed);
  }

  function reset() {
    zoomed = false;
    scale = 1;
    panX = 0;
    panY = 0;
    dragging = false;
    baseRect = null;
    applyTransform();
  }

  function updateNavigation() {
    const hasPrev = currentIndex > 0;
    const hasNext = currentIndex >= 0 && currentIndex < currentImages.length - 1;
    prevBtn.classList.toggle('is-off', !hasPrev);
    nextBtn.classList.toggle('is-off', !hasNext);
  }

  function open(src, deleteUrl, images) {
    currentImages = Array.isArray(images) ? images : [];
    currentIndex = currentImages.findIndex(item => item.src === src);
    if (currentIndex < 0 && src) {
      currentImages = [{ src, deleteUrl: deleteUrl || null }];
      currentIndex = 0;
    }
    img.src = src;
    currentDeleteUrl = deleteUrl || null;
    deleteBtn.hidden = !currentDeleteUrl;
    updateNavigation();
    reset();
    overlay.hidden = false;
  }

  function navigate(direction) {
    const nextIndex = currentIndex + direction;
    if (nextIndex < 0 || nextIndex >= currentImages.length) return;
    const item = currentImages[nextIndex];
    currentIndex = nextIndex;
    img.src = item.src;
    currentDeleteUrl = item.deleteUrl || null;
    deleteBtn.hidden = !currentDeleteUrl;
    updateNavigation();
    reset();
  }

  function close() {
    overlay.hidden = true;
    img.src = '';
    currentDeleteUrl = null;
    currentImages = [];
    currentIndex = -1;
    updateNavigation();
    reset();
  }

  function clamp(value, max) {
    return Math.min(max, Math.max(-max, value));
  }

  function clampPan(rect) {
    const maxPanX = (rect.width * (scale - 1)) / 2;
    const maxPanY = (rect.height * (scale - 1)) / 2;
    panX = clamp(panX, maxPanX);
    panY = clamp(panY, maxPanY);
  }

  // Zoom to `next`, keeping the picture point under (clientX, clientY) where it is.
  function zoomAt(next, clientX, clientY) {
    next = Math.min(MAX_SCALE, Math.max(1, next));
    if (next <= 1.001) {
      zoomed = false; scale = 1; panX = 0; panY = 0;
      return;
    }
    if (!zoomed) baseRect = img.getBoundingClientRect(); // still unscaled here
    const cx = baseRect.left + baseRect.width / 2;
    const cy = baseRect.top + baseRect.height / 2;
    const ratio = next / scale;
    panX = (clientX - cx) - ((clientX - cx) - panX) * ratio;
    panY = (clientY - cy) - ((clientY - cy) - panY) * ratio;
    scale = next;
    zoomed = true;
    clampPan(baseRect);
  }

  // ---- Open triggers: any element with data-lightbox-src ----
  document.addEventListener('click', (e) => {
    const trigger = e.target.closest('[data-lightbox-src]');
    if (!trigger) return;
    const group = trigger.closest('.sheet-thumbs');
    const images = group
      ? Array.from(group.querySelectorAll('[data-lightbox-src]')).map(el => ({
          src: el.dataset.lightboxSrc,
          deleteUrl: el.dataset.deleteUrl || null
        }))
      : [{ src: trigger.dataset.lightboxSrc, deleteUrl: trigger.dataset.deleteUrl || null }];
    open(trigger.dataset.lightboxSrc, trigger.dataset.deleteUrl, images);
  });

  // ---- Click to zoom, drag to pan (unified via mousedown/move/up) ----
  img.addEventListener('mousedown', (e) => {
    dragging = true;
    movedDistance = 0;
    dragStart = { x: e.clientX, y: e.clientY };
    panStart = { x: panX, y: panY };
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const dx = e.clientX - dragStart.x;
    const dy = e.clientY - dragStart.y;
    movedDistance = Math.hypot(dx, dy);
    if (zoomed && baseRect) {
      panX = panStart.x + dx;
      panY = panStart.y + dy;
      clampPan(baseRect);
      applyTransform();
    }
  });

  window.addEventListener('mouseup', (e) => {
    if (!dragging) return;
    dragging = false;
    if (movedDistance < DRAG_THRESHOLD) {
      // A real click (not a drag): toggle zoom
      if (!zoomed) {
        zoomAt(ZOOM_SCALE, e.clientX, e.clientY);
      } else {
        zoomAt(1);
      }
    }
    applyTransform();
  });

  // Touch equivalents (single-finger drag/tap)
  img.addEventListener('touchstart', (e) => {
    const t = e.touches[0];
    dragging = true;
    movedDistance = 0;
    dragStart = { x: t.clientX, y: t.clientY };
    panStart = { x: panX, y: panY };
  }, { passive: true });

  img.addEventListener('touchmove', (e) => {
    if (!dragging) return;
    const t = e.touches[0];
    const dx = t.clientX - dragStart.x;
    const dy = t.clientY - dragStart.y;
    movedDistance = Math.hypot(dx, dy);
    if (zoomed && baseRect) {
      panX = panStart.x + dx;
      panY = panStart.y + dy;
      clampPan(baseRect);
      applyTransform();
    }
  }, { passive: true });

  img.addEventListener('touchend', (e) => {
    if (!dragging) return;
    dragging = false;
    if (movedDistance < DRAG_THRESHOLD) {
      const t = e.changedTouches[0];
      if (!zoomed) {
        zoomAt(ZOOM_SCALE, t.clientX, t.clientY);
      } else {
        zoomAt(1);
      }
    }
    applyTransform();
  });


  // ---- Mouse wheel zooms the picture (and never scrolls the page behind) ----
  let wheelTimer = null;
  overlay.addEventListener('wheel', (e) => {
    e.preventDefault();
    if (!img.getAttribute('src')) return;
    const r = zoomed && baseRect ? baseRect : img.getBoundingClientRect();
    const overImage = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
    const px = overImage ? e.clientX : r.left + r.width / 2;
    const py = overImage ? e.clientY : r.top + r.height / 2;
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;   // lines / pages -> pixels
    zoomAt(scale * Math.exp(-e.deltaY * unit * 0.0015), px, py);
    stage.classList.add('is-wheeling');                                  // no easing while the wheel turns
    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(() => stage.classList.remove('is-wheeling'), 150);
    applyTransform();
  }, { passive: false });

  prevBtn.addEventListener('click', (e) => { e.stopPropagation(); navigate(-1); });
  nextBtn.addEventListener('click', (e) => { e.stopPropagation(); navigate(1); });

  // ---- Delete ----
  deleteBtn.addEventListener('click', async () => {
    if (!currentDeleteUrl) return;
    const ok = window.confirmAction
      ? await window.confirmAction('Remove this image? This cannot be undone.')
      : confirm('Remove this image? This cannot be undone.');
    if (!ok) return;
    try {
      await fetch(currentDeleteUrl, { method: 'POST' });
    } finally {
      close();
      window.location.reload();
    }
  });

  // ---- Close ----
  closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) close();
  });
  document.addEventListener('keydown', (e) => {
    if (overlay.hidden) return;
    if (e.key === 'Escape') close();
    else if (e.key === 'ArrowLeft') navigate(-1);
    else if (e.key === 'ArrowRight') navigate(1);
  });

  window.openLightbox = open;
})();
