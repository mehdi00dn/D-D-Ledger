/* Large uploads.
 *
 * A form may carry at most ~3.4 MB of files itself (Vercel rejects request bodies
 * over 4.5 MB).  When the chosen files are bigger, this script -- before the form
 * is submitted -- does one of two things:
 *
 *   1. shrinks big images in the browser (max 2000 px, same limit the server uses).
 *      That is usually enough, and keeps everything on our own domain.
 *   2. otherwise uploads each file straight to storage through a one-off signed URL
 *      obtained from POST /uploads/sign, then submits the form with a small
 *      "<field>__key" reference instead of the file bytes.
 *
 * The server re-validates everything either way; nothing here is trusted.
 *
 * window.LEDGER_UPLOAD_MODE = 'direct' | 'inline' forces a path (for troubleshooting/tests).
 */
(function () {
  'use strict';

  var INLINE_LIMIT = 3.4 * 1024 * 1024;       // keep in sync with DIRECT_INLINE_LIMIT in app.py
  var MAX_DIM = 2000;
  var PURPOSE = { avatar: 'avatar', sheets: 'sheet', image: 'map', import_file: 'import' };

  function csrfHeaders() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m && m.content ? { 'X-CSRF-Token': m.content } : {};
  }

  function inputsWithFiles(form) {
    return Array.prototype.filter.call(form.querySelectorAll('input[type="file"][name]'), function (i) {
      return PURPOSE[i.name] && i.files && i.files.length;
    });
  }

  function totalSize(inputs) {
    var n = 0;
    inputs.forEach(function (i) { Array.prototype.forEach.call(i.files, function (f) { n += f.size; }); });
    return n;
  }

  function setFiles(input, files) {
    var dt = new DataTransfer();
    files.forEach(function (f) { dt.items.add(f); });
    input.files = dt.files;
  }

  function toBlob(canvas, type, quality) {
    return new Promise(function (resolve) { canvas.toBlob(resolve, type, quality); });
  }

  // --- Progress bar (currently only the import forms carry the markup for
  // this; any other form just silently skips these since progressEl is null) ---
  function progressEl(form) { return form.querySelector('.upload-progress'); }

  function showProgress(form, indeterminate) {
    var el = progressEl(form);
    if (!el) return;
    el.hidden = false;
    el.classList.toggle('is-indeterminate', !!indeterminate);
    el.querySelector('.upload-progress-bar').style.width = indeterminate ? '' : '0%';
  }

  function setProgress(form, fraction) {
    var el = progressEl(form);
    if (!el || el.classList.contains('is-indeterminate')) return;
    el.querySelector('.upload-progress-bar').style.width = Math.max(0, Math.min(100, fraction * 100)) + '%';
  }

  function hideProgress(form) {
    var el = progressEl(form);
    if (!el) return;
    el.hidden = true;
    el.classList.remove('is-indeterminate');
  }

  function renamed(blob, file, ext) {
    var base = (file.name || 'image').replace(/\.[^.]+$/, '');
    return new File([blob], base + '.' + ext, { type: blob.type, lastModified: Date.now() });
  }

  async function shrinkImage(file) {
    if (!/^image\/(png|jpeg|webp)$/.test(file.type) || typeof createImageBitmap !== 'function') return file;
    try {
      var bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
      var scale = Math.min(1, MAX_DIM / Math.max(bmp.width, bmp.height));
      var w = Math.max(1, Math.round(bmp.width * scale)), h = Math.max(1, Math.round(bmp.height * scale));
      var canvas = document.createElement('canvas');
      canvas.width = w; canvas.height = h;
      var ctx = canvas.getContext('2d');
      if (file.type === 'image/png') {                        // PNG first: keeps transparency
        ctx.drawImage(bmp, 0, 0, w, h);
        var png = await toBlob(canvas, 'image/png');
        if (png && png.size <= INLINE_LIMIT) return renamed(png, file, 'png');
      }
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h);
      ctx.drawImage(bmp, 0, 0, w, h);
      var qualities = [0.86, 0.72, 0.6];
      for (var q = 0; q < qualities.length; q++) {
        var jpg = await toBlob(canvas, 'image/jpeg', qualities[q]);
        if (jpg && jpg.size <= INLINE_LIMIT) return renamed(jpg, file, 'jpg');
      }
    } catch (e) { /* fall through to the original file */ }
    return file;
  }

  function xhrUpload(url, method, headers, body, onProgress) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open(method || 'PUT', url);
      Object.keys(headers || {}).forEach(function (k) { xhr.setRequestHeader(k, headers[k]); });
      if (xhr.upload && onProgress) {
        xhr.upload.onprogress = function (e) { if (e.lengthComputable) onProgress(e.loaded, e.total); };
      }
      xhr.onload = function () { resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status }); };
      xhr.onerror = function () { reject(new Error('network error')); };
      xhr.send(body);
    });
  }

  async function stage(file, purpose, onProgress) {
    var res = await fetch('/uploads/sign', {
      method: 'POST', credentials: 'same-origin',
      headers: Object.assign({ 'Content-Type': 'application/json' }, csrfHeaders()),
      body: JSON.stringify({ purpose: purpose, size: file.size, content_type: file.type || 'application/octet-stream' })
    });
    var info = {};
    try { info = await res.json(); } catch (e) { /* keep {} */ }
    if (!res.ok) throw new Error(info.error || ('Could not start the upload (' + res.status + ')'));

    var t = info.upload, sameOrigin = t.url.charAt(0) === '/', headers = Object.assign({}, t.headers || {}), body;
    if (t.body === 'formdata') {                               // exactly what the Supabase client sends
      body = new FormData();
      body.append('cacheControl', '3600');
      body.append('', file);
    } else {
      body = file;
      headers['Content-Type'] = 'application/octet-stream';
      Object.assign(headers, csrfHeaders());
    }
    var up;
    try {
      up = await xhrUpload(t.url, t.method || 'PUT', headers, body, onProgress);
    } catch (e) {
      throw new Error('The file could not reach the storage server. Check your connection and try again.');
    }
    if (!up.ok) throw new Error(up.status === 413 ? 'The file is too large.' : 'The upload was rejected (' + up.status + ').');
    return info.key;
  }

  function setBusy(form, submitter, busy, label) {
    form.classList.toggle('is-uploading', busy);
    document.body.style.cursor = busy ? 'progress' : '';
    if (submitter) {
      if (busy) {
        submitter.dataset.origHtml = submitter.innerHTML;
        submitter.disabled = true;
        submitter.textContent = label || 'Uploading\u2026';
      } else if (submitter.dataset.origHtml !== undefined) {
        submitter.innerHTML = submitter.dataset.origHtml;
        submitter.disabled = false;
        delete submitter.dataset.origHtml;
      }
    }
  }

  function resubmit(form, submitter) {
    form.dataset.uploadsReady = '1';
    if (form.requestSubmit) { submitter && submitter.form === form ? form.requestSubmit(submitter) : form.requestSubmit(); }
    else { form.submit(); }
  }

  document.addEventListener('submit', async function (ev) {
    var form = ev.target;
    if (!(form instanceof HTMLFormElement) || form.dataset.uploadsReady) return;
    var inputs = inputsWithFiles(form);
    if (!inputs.length) return;
    var mode = window.LEDGER_UPLOAD_MODE;
    if (mode === 'inline' || (mode !== 'direct' && totalSize(inputs) <= INLINE_LIMIT)) return;   // ordinary submit

    ev.preventDefault();
    ev.stopPropagation();                                      // other submit handlers run on the re-submit below
    var submitter = ev.submitter || null;
    setBusy(form, submitter, true, 'Preparing\u2026');
    try {
      if (mode !== 'direct') {                                 // 1. try to stay on our own domain
        for (var a = 0; a < inputs.length; a++) {
          if (inputs[a].name === 'import_file') continue;
          var list = await Promise.all(Array.prototype.map.call(inputs[a].files, function (f) {
            return f.size > 400 * 1024 ? shrinkImage(f) : f;
          }));
          setFiles(inputs[a], list);
        }
        if (totalSize(inputs) <= INLINE_LIMIT) {
          setBusy(form, submitter, false);
          showProgress(form, true);                            // no separate upload phase to measure -- the
          resubmit(form, submitter);                            // browser's own nav spinner covers what's left
          return;
        }
      }
      setBusy(form, submitter, true, 'Uploading\u2026');       // 2. straight to storage
      var grandTotal = totalSize(inputs), sent = 0;
      showProgress(form, false);
      for (var b = 0; b < inputs.length; b++) {
        var input = inputs[b], files = Array.prototype.slice.call(input.files), keys = [];
        for (var c = 0; c < files.length; c++) {
          var fileStartSent = sent;
          keys.push(await stage(files[c], PURPOSE[input.name], function (loaded, total) {
            setProgress(form, grandTotal ? (fileStartSent + loaded) / grandTotal : 0);
          }));
          sent = fileStartSent + files[c].size;
          setProgress(form, grandTotal ? sent / grandTotal : 1);
        }
        keys.forEach(function (k) {
          var h = document.createElement('input');
          h.type = 'hidden'; h.name = input.name + '__key'; h.value = k;
          form.appendChild(h);
        });
        input.value = '';                                      // never send the bytes twice
      }
      setBusy(form, submitter, false);
      showProgress(form, true);                                // upload's done; server still has to process it
      resubmit(form, submitter);
    } catch (err) {
      setBusy(form, submitter, false);
      hideProgress(form);
      Array.prototype.forEach.call(form.querySelectorAll('input[type="hidden"][name$="__key"]'), function (n) { n.remove(); });
      window.alert((err && err.message) || 'The upload failed. Please try again.');
    }
  }, true);

  // Coming back via the browser's back button: forget half-finished upload state.
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    Array.prototype.forEach.call(document.forms, function (f) {
      delete f.dataset.uploadsReady;
      Array.prototype.forEach.call(f.querySelectorAll('input[type="hidden"][name$="__key"]'), function (n) { n.remove(); });
      hideProgress(f);
    });
    document.body.style.cursor = '';
  });
})();
