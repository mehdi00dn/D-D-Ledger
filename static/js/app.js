// Shared helpers used across pages: file input previews, color swatches.

document.addEventListener('DOMContentLoaded', () => {
  const appShell = document.getElementById('app-shell');
  const sidebar = document.getElementById('sidebar');
  const sidebarToggle = document.getElementById('sidebar-toggle');
  const EXPANDED_WIDTH = 232;
  const COLLAPSED_WIDTH = 86;

  if (appShell && sidebar && sidebarToggle) {
    let isCollapsed = false;

    const applySidebarWidth = (width) => {
      appShell.style.setProperty('--sidebar-width', `${width}px`);
      sidebar.style.width = `${width}px`;
      sidebar.style.minWidth = `${width}px`;
    };

    const refreshSidebarState = () => {
      sidebar.classList.toggle('is-collapsed', isCollapsed);
      sidebarToggle.setAttribute('aria-expanded', String(!isCollapsed));
      sidebarToggle.setAttribute('aria-label', isCollapsed ? 'Expand sidebar' : 'Collapse sidebar');
      sidebarToggle.title = isCollapsed ? 'Expand sidebar' : 'Collapse sidebar';
      const width = isCollapsed ? COLLAPSED_WIDTH : EXPANDED_WIDTH;
      applySidebarWidth(width);
    };

    sidebar.addEventListener('mouseenter', () => {
      if (isCollapsed) {
        applySidebarWidth(EXPANDED_WIDTH);
        sidebar.classList.add('is-hovered');
      }
    });

    sidebar.addEventListener('mouseleave', () => {
      if (isCollapsed) {
        applySidebarWidth(COLLAPSED_WIDTH);
        sidebar.classList.remove('is-hovered');
      }
    });

    sidebarToggle.addEventListener('click', () => {
      isCollapsed = !isCollapsed;
      sidebar.classList.remove('is-hovered');
      refreshSidebarState();
    });

    refreshSidebarState();
  }

  // Note: avatar file inputs ([data-avatar-input]) are handled entirely by
  // cropper.js, which opens the crop modal on selection and updates the
  // preview + input files once the user applies a crop.

  // Multi-file inputs (character sheets) -> just show a count
  document.querySelectorAll('[data-multi-input]').forEach((input) => {
    input.addEventListener('change', () => {
      const nameLabel = input.parentElement.querySelector('.file-name');
      const n = input.files ? input.files.length : 0;
      if (nameLabel) {
        nameLabel.textContent = n === 0 ? '' : `${n} file${n > 1 ? 's' : ''} selected`;
      }
    });
  });

  // Color swatches -> set hidden color input value
  document.querySelectorAll('[data-color-group]').forEach((group) => {
    const hiddenInput = document.querySelector(group.dataset.colorGroup);
    const swatches = group.querySelectorAll('.color-swatch');
    swatches.forEach((sw) => {
      if (hiddenInput && sw.dataset.color.toLowerCase() === (hiddenInput.value || '').toLowerCase()) {
        sw.classList.add('selected');
      }
      sw.addEventListener('click', () => {
        swatches.forEach((s) => s.classList.remove('selected'));
        sw.classList.add('selected');
        if (hiddenInput) hiddenInput.value = sw.dataset.color;
        const customPicker = group.parentElement.querySelector('.color-swatch-custom');
        if (customPicker) customPicker.value = sw.dataset.color;
      });
    });
  });

  // Custom color picker input syncing with hidden field + swatch highlight
  document.querySelectorAll('.color-swatch-custom').forEach((picker) => {
    picker.addEventListener('input', () => {
      const groupWrap = picker.closest('.form-section')?.querySelector('[data-color-group]');
      const hiddenInput = groupWrap ? document.querySelector(groupWrap.dataset.colorGroup) : null;
      if (hiddenInput) hiddenInput.value = picker.value;
      if (groupWrap) {
        groupWrap.querySelectorAll('.color-swatch').forEach((s) => s.classList.remove('selected'));
      }
    });
  });

  // Confirm before destructive deletes
  document.querySelectorAll('[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (e) => {
      if (!confirm(form.dataset.confirm)) {
        e.preventDefault();
      }
    });
  });

  // Sheet-image delete buttons: plain buttons (not nested forms) that POST via fetch,
  // since a <form> can't be nested inside the outer character-edit <form>.
  document.querySelectorAll('[data-sheet-delete-url]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const msg = btn.dataset.confirmMsg || 'Remove this image?';
      if (!confirm(msg)) return;
      fetch(btn.dataset.sheetDeleteUrl, { method: 'POST' })
        .then(() => window.location.reload())
        .catch(() => alert('Could not remove the image. Please try again.'));
    });
  });
});
