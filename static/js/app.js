// Shared helpers used across pages: file input previews, color swatches.

document.addEventListener('DOMContentLoaded', () => {
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
    const customWrap = group.querySelector('.color-swatch-custom-wrap');
    const currentValue = (hiddenInput?.value || '').toLowerCase();
    const matchesPreset = Array.from(swatches).some((sw) => sw.dataset.color.toLowerCase() === currentValue);
    if (!matchesPreset && customWrap) customWrap.classList.add('selected');

    swatches.forEach((sw) => {
      if (hiddenInput && sw.dataset.color.toLowerCase() === currentValue) {
        sw.classList.add('selected');
      }
      sw.addEventListener('click', () => {
        swatches.forEach((s) => s.classList.remove('selected'));
        sw.classList.add('selected');
        if (customWrap) customWrap.classList.remove('selected');
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
      const wrap = picker.closest('.color-swatch-custom-wrap');
      if (wrap) wrap.classList.add('selected');
    });
  });

  // Confirm before destructive deletes
  document.querySelectorAll('[data-confirm]').forEach((form) => {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const confirmed = await window.confirmAction(form.dataset.confirm);
      if (confirmed) form.submit();
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

  // ---- Character notes: multi-box rich text editor (bold/italic/bullet list) ----
  const noteBlocks = document.getElementById('note-blocks');
  if (noteBlocks) {
    const addNoteBtn = document.getElementById('add-note-btn');
    const notesHiddenInput = document.getElementById('notes-hidden-input');
    const noteTemplate = document.getElementById('note-block-template');
    const form = noteBlocks.closest('form');

    // Use <div> per line on Enter (not the default <p> in some browsers) so line
    // breaks survive as one block per line, consistently across blocks.
    try { document.execCommand('defaultParagraphSeparator', false, 'div'); } catch (e) { /* no-op */ }

    const ALLOWED_NOTE_TAGS = new Set(['B', 'STRONG', 'I', 'EM', 'UL', 'LI', 'BR', 'DIV']);

    function sanitizeNoteHtml(html) {
      const scratch = document.createElement('div');
      scratch.innerHTML = html;
      (function clean(node) {
        Array.from(node.childNodes).forEach((child) => {
          if (child.nodeType === 1) {
            if (!ALLOWED_NOTE_TAGS.has(child.tagName)) {
              while (child.firstChild) node.insertBefore(child.firstChild, child);
              node.removeChild(child);
              return;
            }
            Array.from(child.attributes).forEach((attr) => child.removeAttribute(attr.name));
            clean(child);
          } else if (child.nodeType !== 3) {
            node.removeChild(child);
          }
        });
      }(scratch));
      return scratch.innerHTML;
    }

    function isEditorEmpty(editor) {
      const html = editor.innerHTML.trim().toLowerCase();
      return html === '' || html === '<br>' || html === '<div><br></div>';
    }

    function updateRemoveButtons() {
      const blocks = noteBlocks.querySelectorAll('[data-note-block]');
      blocks.forEach((block) => {
        const removeBtn = block.querySelector('[data-note-remove]');
        if (removeBtn) removeBtn.hidden = blocks.length <= 1;
      });
    }

    function addNoteBlock(focus) {
      const fragment = noteTemplate.content.cloneNode(true);
      noteBlocks.appendChild(fragment);
      updateRemoveButtons();
      if (focus) {
        const editors = noteBlocks.querySelectorAll('.note-editor');
        editors[editors.length - 1].focus();
      }
    }

    // Keep the empty-state placeholder (CSS :empty) accurate after edits, since
    // some browsers leave a stray <br> behind when a box is cleared out.
    noteBlocks.addEventListener('input', (e) => {
      const editor = e.target.closest('.note-editor');
      if (editor && isEditorEmpty(editor)) editor.innerHTML = '';
    });

    // Toolbar buttons: keep the editor's selection alive through the click, then
    // run the formatting command on it.
    noteBlocks.addEventListener('mousedown', (e) => {
      if (e.target.closest('.note-tool-btn')) e.preventDefault();
    });
    noteBlocks.addEventListener('click', (e) => {
      const toolBtn = e.target.closest('.note-tool-btn');
      if (toolBtn) {
        const editor = toolBtn.closest('.note-block').querySelector('.note-editor');
        editor.focus();
        document.execCommand(toolBtn.dataset.cmd, false, null);
        return;
      }
      const removeBtn = e.target.closest('[data-note-remove]');
      if (removeBtn) {
        const blocks = noteBlocks.querySelectorAll('[data-note-block]');
        if (blocks.length <= 1) return;
        removeBtn.closest('[data-note-block]').remove();
        updateRemoveButtons();
      }
    });

    if (addNoteBtn) addNoteBtn.addEventListener('click', () => addNoteBlock(true));
    updateRemoveButtons();

    if (form && notesHiddenInput) {
      form.addEventListener('submit', () => {
        const blocks = Array.from(noteBlocks.querySelectorAll('.note-editor'))
          .map((editor) => sanitizeNoteHtml(editor.innerHTML))
          .filter((html) => !['', '<br>', '<div><br></div>'].includes(html));
        notesHiddenInput.value = JSON.stringify(blocks);
      });
    }
  }
});
