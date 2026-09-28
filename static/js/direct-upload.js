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

  // --- Progress indicator: a ring over image previews, a bar on the import forms. ---
  // One continuous 0..100 fill, never a spinner:
  //   * real bytes leaving the browser fill the first UPLOAD_SHARE percent;
  //   * once they're out, the server still has to re-encode / store / redirect, and it can't
  //     report progress -- so the fill eases toward CREEP_CEILING instead of sitting still or
  //     spinning, and only reaches 100 when the server actually answers.
  // A form with no .upload-progress element just skips all of this (progressEl is null).
  var UPLOAD_SHARE = 85;
  var CREEP_CEILING = 98;
  var CREEP_TAU_SECONDS = 2.5;

  function progressEl(form) {
    var avatarProgress = form.querySelector('.upload-progress-circle');
    var avatarInput = form.querySelector('input[type="file"][name="avatar"]');
    if (avatarProgress && avatarInput && avatarInput.files && avatarInput.files.length) return avatarProgress;
    return form.querySelector('.upload-progress:not(.upload-progress-circle)');
  }

  function paint(el, pct) {
    el._pct = pct;
    if (el.classList.contains('upload-progress-circle')) {
      el.style.setProperty('--pct', pct);                       // the ring
      el.style.setProperty('--pct-int', Math.round(pct));       // the number in its middle
    } else {
      el.querySelector('.upload-progress-bar').style.width = pct + '%';
    }
    if (el._onPaint) el._onPaint(pct);                          // mirror it on the Save button
  }

  function stopCreep(el) {
    if (el._creep) { clearInterval(el._creep); el._creep = null; }
  }

  function showProgress(form) {
    var el = progressEl(form);
    if (!el) return;
    stopCreep(el);
    el.classList.remove('is-indeterminate');
    paint(el, 0);
    el.hidden = false;
  }

  // fraction = share of the bytes uploaded so far (0..1); never moves the fill backwards
  function setProgress(form, fraction) {
    var el = progressEl(form);
    if (!el) return;
    var pct = Math.max(0, Math.min(1, fraction)) * UPLOAD_SHARE;
    if (pct > (el._pct || 0)) paint(el, pct);
  }

  // Bytes are out and the server is working: ease toward the ceiling.
  function startProcessing(form) {
    var el = progressEl(form);
    if (!el || el._creep) return;
    var from = Math.max(el._pct || 0, UPLOAD_SHARE), t0 = Date.now();
    paint(el, from);
    el._creep = setInterval(function () {
      var t = (Date.now() - t0) / 1000;
      paint(el, from + (CREEP_CEILING - from) * (1 - Math.exp(-t / CREEP_TAU_SECONDS)));
    }, 80);
  }

  function finishProgress(form) {
    var el = progressEl(form);
    if (!el) return;
    stopCreep(el);
    paint(el, 100);
  }

  function hideProgress(form) {
    var el = progressEl(form);
    if (!el) return;
    stopCreep(el);
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

  // The button that carries the progress: the Save/Create button that was clicked, or -- on the
  // import forms, where the file picker auto-submits -- the Import label itself.
  function progressButton(form, submitter) { return submitter || form.querySelector('.import-label'); }

  function restoreButton(btn) {
    var textNode = btn.querySelector('.btn-label');            // the Import label: swap only its text, never its file input
    if (btn.dataset.origHtml !== undefined) {
      if (textNode) textNode.textContent = btn.dataset.origHtml; else btn.innerHTML = btn.dataset.origHtml;
    }
    if ('disabled' in btn) btn.disabled = false; else btn.style.pointerEvents = '';
    btn.classList.remove('is-progress');
    btn.style.removeProperty('--btn-pct');
    delete btn.dataset.origHtml;
    delete btn.dataset.busyLabel;
  }

  function setBusy(form, submitter, busy, label) {
    form.classList.toggle('is-uploading', busy);
    document.body.style.cursor = busy ? 'progress' : '';
    var el = progressEl(form), btn = progressButton(form, submitter);
    if (!btn) return;
    var textNode = btn.querySelector('.btn-label');
    if (busy) {
      if (btn.dataset.origHtml === undefined) btn.dataset.origHtml = textNode ? textNode.textContent : btn.innerHTML;
      btn.dataset.busyLabel = label || 'Uploading\u2026';
      if ('disabled' in btn) btn.disabled = true; else btn.style.pointerEvents = 'none';
      (textNode || btn).textContent = btn.dataset.busyLabel;
      // Where the eyes are after clicking (the avatar ring can be scrolled out of view):
      // a fill behind the label plus the percent, driven by the same value as the ring.
      if (el) el._onPaint = function (pct) {
        btn.classList.add('is-progress');
        btn.style.setProperty('--btn-pct', pct);
        (textNode || btn).textContent = btn.dataset.busyLabel + ' ' + Math.round(pct) + '%';
      };
    } else {
      restoreButton(btn);
      if (el) el._onPaint = null;
    }
  }

  // Submits the form ourselves via XHR instead of letting the browser do it natively.
  // This is the only way to get real upload-progress events -- a native form submission
  // exposes none.  These routes always redirect (success or failure -- see app.py), so
  // following the redirect chain and navigating to wherever it ends is equivalent to what
  // a native submission would have done either way.
  // continueProgress: the direct-to-storage path has already filled the indicator with the
  // real file bytes; this final POST only carries the object keys, so don't reset it.
  function submitFormWithProgress(form, continueProgress) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open(form.method || 'POST', form.action);
      if (!continueProgress) showProgress(form);
      xhr.upload.onprogress = function (e) { if (e.lengthComputable && !continueProgress) setProgress(form, e.loaded / e.total); };
      xhr.upload.onload = function () { if (!continueProgress) setProgress(form, 1); startProcessing(form); };
      xhr.onload = function () {
        if (xhr.status >= 400) { reject(new Error('The upload failed. Please try again.')); return; }
        finishProgress(form);
        setTimeout(function () { window.location.href = xhr.responseURL || form.action; }, 150);   // let the full ring be seen
        resolve();
      };
      xhr.onerror = function () { reject(new Error('The upload failed. Please try again.')); };
      xhr.send(new FormData(form));
    });
  }

  // Bubble phase on purpose: the form's own submit listeners (e.g. app.js serializing the
  // note editors into their hidden field) have to run BEFORE we read the form's data.
  // Running in the capture phase and stopping propagation would skip them and silently
  // drop those edits.  If one of them already cancelled the submit, respect that.
  document.addEventListener('submit', async function (ev) {
    var form = ev.target;
    if (ev.defaultPrevented || !(form instanceof HTMLFormElement) || form.dataset.uploadsReady) return;
    var inputs = inputsWithFiles(form);
    if (!inputs.length) return;
    ev.preventDefault();
    if (form.dataset.uploading) return;                        // already on its way: a second click must not create a duplicate
    form.dataset.uploading = '1';
    var submitter = ev.submitter || null;
    var mode = window.LEDGER_UPLOAD_MODE;
    var saving = Array.prototype.some.call(inputs, function (i) { return i.name === 'import_file'; }) ? 'Importing\u2026' : 'Saving\u2026';
    try {
      if (mode === 'inline' || (mode !== 'direct' && totalSize(inputs) <= INLINE_LIMIT)) {
        setBusy(form, submitter, true, saving);
        await submitFormWithProgress(form, false);              // small enough to post as-is
        return;
      }

      setBusy(form, submitter, true, 'Preparing\u2026');
      if (mode !== 'direct') {                                 // 1. try to stay on our own domain
        for (var a = 0; a < inputs.length; a++) {
          if (inputs[a].name === 'import_file') continue;
          var list = await Promise.all(Array.prototype.map.call(inputs[a].files, function (f) {
            return f.size > 400 * 1024 ? shrinkImage(f) : f;
          }));
          setFiles(inputs[a], list);
        }
        if (totalSize(inputs) <= INLINE_LIMIT) {
          setBusy(form, submitter, true, saving);
          await submitFormWithProgress(form, false);             // shrunk down enough to post as-is
          return;
        }
      }
      setBusy(form, submitter, true, 'Uploading\u2026');       // 2. straight to storage
      var grandTotal = totalSize(inputs), sent = 0;
      showProgress(form);
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
      setBusy(form, submitter, true, saving);
      await submitFormWithProgress(form, true);               // final, small POST: just the __key refs
    } catch (err) {
      delete form.dataset.uploading;
      setBusy(form, submitter, false);
      hideProgress(form);
      Array.prototype.forEach.call(form.querySelectorAll('input[type="hidden"][name$="__key"]'), function (n) { n.remove(); });
      window.alert((err && err.message) || 'The upload failed. Please try again.');
    }
  }, false);

  // Coming back via the browser's back button: forget half-finished upload state.
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    Array.prototype.forEach.call(document.forms, function (f) {
      delete f.dataset.uploadsReady;
      delete f.dataset.uploading;
      f.classList.remove('is-uploading');
      Array.prototype.forEach.call(f.querySelectorAll('[data-orig-html]'), restoreButton);
      Array.prototype.forEach.call(f.querySelectorAll('input[type="hidden"][name$="__key"]'), function (n) { n.remove(); });
      hideProgress(f);
    });
    document.body.style.cursor = '';
  });
})();
