// Avatar cropper: intercepts avatar file inputs, lets the user pan/zoom a
// square crop before it's attached to the form. Pure canvas + vanilla JS,
// no external libraries.

(function () {
  const modal = document.getElementById('crop-modal');
  if (!modal) return; // page has no crop modal (shouldn't happen, base.html always includes it)

  const canvas = document.getElementById('crop-canvas');
  const ctx = canvas.getContext('2d');
  const zoomSlider = document.getElementById('crop-zoom-slider');
  const applyBtn = document.getElementById('crop-apply-btn');
  const cancelBtn = document.getElementById('crop-cancel-btn');
  const cancelBtn2 = document.getElementById('crop-cancel-btn-2');

  const CANVAS_SIZE = canvas.width; // internal resolution (also output resolution)
  const MAX_ZOOM_MULT = 3; // slider goes from "fills the frame" up to 3x that

  let img = null;
  let baseScale = 1;
  let scale = 1;
  let offsetX = 0;
  let offsetY = 0;
  let dragging = false;
  let dragStart = { x: 0, y: 0 };
  let dragOffsetStart = { x: 0, y: 0 };
  let activeInput = null;
  let activePreviewSelector = null;
  let currentObjectUrl = null;

  function clampOffsets() {
    const scaledW = img.naturalWidth * scale;
    const scaledH = img.naturalHeight * scale;
    const minX = CANVAS_SIZE - scaledW;
    const minY = CANVAS_SIZE - scaledH;
    offsetX = Math.min(0, Math.max(minX, offsetX));
    offsetY = Math.min(0, Math.max(minY, offsetY));
  }

  function draw() {
    ctx.clearRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);
    ctx.drawImage(img, offsetX, offsetY, img.naturalWidth * scale, img.naturalHeight * scale);
  }

  function openCropper(file, inputEl, previewSelector) {
    activeInput = inputEl;
    activePreviewSelector = previewSelector;
    if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
    currentObjectUrl = URL.createObjectURL(file);

    img = new Image();
    img.onload = () => {
      baseScale = Math.max(CANVAS_SIZE / img.naturalWidth, CANVAS_SIZE / img.naturalHeight);
      scale = baseScale;
      offsetX = (CANVAS_SIZE - img.naturalWidth * scale) / 2;
      offsetY = (CANVAS_SIZE - img.naturalHeight * scale) / 2;
      zoomSlider.value = 0;
      draw();
      modal.hidden = false;
    };
    img.src = currentObjectUrl;
  }

  zoomSlider.addEventListener('input', () => {
    if (!img) return;
    const t = zoomSlider.value / 100;
    scale = baseScale * (1 + t * (MAX_ZOOM_MULT - 1));
    clampOffsets();
    draw();
  });

  function pointerPos(e) {
    const rect = canvas.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    const scaleX = CANVAS_SIZE / rect.width;
    const scaleY = CANVAS_SIZE / rect.height;
    return { x: (clientX - rect.left) * scaleX, y: (clientY - rect.top) * scaleY };
  }

  function startDrag(e) {
    if (!img) return;
    dragging = true;
    dragStart = pointerPos(e);
    dragOffsetStart = { x: offsetX, y: offsetY };
  }
  function moveDrag(e) {
    if (!dragging) return;
    const p = pointerPos(e);
    offsetX = dragOffsetStart.x + (p.x - dragStart.x);
    offsetY = dragOffsetStart.y + (p.y - dragStart.y);
    clampOffsets();
    draw();
  }
  function endDrag() { dragging = false; }

  canvas.addEventListener('mousedown', startDrag);
  window.addEventListener('mousemove', moveDrag);
  window.addEventListener('mouseup', endDrag);
  canvas.addEventListener('touchstart', startDrag, { passive: true });
  canvas.addEventListener('touchmove', moveDrag, { passive: true });
  window.addEventListener('touchend', endDrag);

  function closeModal() {
    modal.hidden = true;
    img = null;
  }

  function cancel() {
    if (activeInput) activeInput.value = '';
    closeModal();
  }
  cancelBtn.addEventListener('click', cancel);
  cancelBtn2.addEventListener('click', cancel);
  modal.addEventListener('click', (e) => { if (e.target === modal) cancel(); });

  applyBtn.addEventListener('click', () => {
    if (!img) return;
    canvas.toBlob((blob) => {
      const file = new File([blob], 'avatar.png', { type: 'image/png' });
      const dt = new DataTransfer();
      dt.items.add(file);
      activeInput.files = dt.files;

      const preview = document.querySelector(activePreviewSelector);
      const previewUrl = URL.createObjectURL(blob);
      if (preview) {
        preview.src = previewUrl;
        preview.style.display = 'block';
        const placeholder = preview.parentElement.querySelector('.avatar-placeholder-icon');
        if (placeholder) placeholder.style.display = 'none';
      }
      const nameLabel = activeInput.parentElement.querySelector('.file-name');
      if (nameLabel) nameLabel.textContent = 'Avatar cropped ✓';

      // A fresh avatar was chosen, so cancel any pending "remove" request and show the remove button
      if (activeInput.dataset.removeInput) {
        const ri = document.querySelector(activeInput.dataset.removeInput);
        if (ri) ri.value = '';
      }
      if (activeInput.dataset.removeBtn) {
        const rb = document.querySelector(activeInput.dataset.removeBtn);
        if (rb) rb.hidden = false;
      }

      closeModal();
    }, 'image/png', 0.95);
  });

  // Hook into every avatar input on the page (character form, group form, ...)
  document.querySelectorAll('[data-avatar-input]').forEach((input) => {
    input.addEventListener('change', () => {
      const file = input.files && input.files[0];
      if (file) openCropper(file, input, input.dataset.avatarInput);
    });

    // Remove-avatar button: clears the selection, reverts the preview, and
    // flags the avatar for removal on save.
    if (input.dataset.removeBtn) {
      const removeBtn = document.querySelector(input.dataset.removeBtn);
      if (removeBtn) {
        removeBtn.addEventListener('click', () => {
          input.value = '';
          const preview = document.querySelector(input.dataset.avatarInput);
          if (preview) {
            preview.style.display = 'none';
            preview.src = '';
            const placeholder = preview.parentElement.querySelector('.avatar-placeholder-icon');
            if (placeholder) placeholder.style.display = '';
          }
          const nameLabel = input.parentElement.querySelector('.file-name');
          if (nameLabel) nameLabel.textContent = '';
          if (input.dataset.removeInput) {
            const ri = document.querySelector(input.dataset.removeInput);
            if (ri) ri.value = '1';
          }
          removeBtn.hidden = true;
        });
      }
    }
  });
})();
