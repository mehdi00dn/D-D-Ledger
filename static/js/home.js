// Campaign Overview: long text (the bio, the campaign notes) is cut to a few lines with a "Read more" button.
(function () {
  function sync(box) {
    var btn = box.nextElementSibling;
    if (!btn || !btn.hasAttribute('data-clamp-toggle')) return;
    var open = box.classList.contains('is-open');
    // Overflowing means there is more to show; once opened the button stays so the text can be folded again.
    var overflow = box.scrollHeight > box.clientHeight + 1;
    box.classList.toggle('is-clipped', !open && overflow);       // the fade at the bottom shows only while text is cut off
    btn.hidden = !(open || overflow);
  }
  document.querySelectorAll('[data-clamp]').forEach(function (box) {
    var btn = box.nextElementSibling;
    sync(box);
    if (btn) btn.addEventListener('click', function () {
      var open = box.classList.toggle('is-open');
      btn.textContent = open ? 'Read less' : 'Read more';
      sync(box);
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    window.addEventListener('resize', function () { sync(box); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { sync(box); });
  });
})();


// Next session: times are stored in UTC and shown in the viewer's own time zone.
(function () {
  var fmt = { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' };
  document.querySelectorAll('time[data-local-time]').forEach(function (el) {
    var d = new Date(el.getAttribute('datetime'));
    if (!isNaN(d)) el.textContent = d.toLocaleString(undefined, fmt);
  });
  document.querySelectorAll('[data-local-relative]').forEach(function (el) {
    var d = new Date(el.getAttribute('data-local-relative'));
    if (isNaN(d) || !window.Intl || !Intl.RelativeTimeFormat) return;
    var mins = Math.round((d - Date.now()) / 60000), abs = Math.abs(mins);
    var rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
    el.textContent = abs < 90 ? rtf.format(mins, 'minute') : abs < 2160 ? rtf.format(Math.round(mins / 60), 'hour') : rtf.format(Math.round(mins / 1440), 'day');
  });
  var form = document.querySelector('[data-session-form]');
  if (!form) return;
  var local = form.querySelector('#session-local'), hidden = form.querySelector('input[name=starts_at]');
  var pad = function (n) { return String(n).padStart(2, '0'); };
  var initial = local.getAttribute('data-initial');
  if (initial) {
    var d = new Date(initial);
    if (!isNaN(d)) local.value = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  var clearBtn = form.querySelector('[data-session-clear]');
  if (clearBtn && window.confirmAction) clearBtn.addEventListener('click', function (e) {
    e.preventDefault();
    window.confirmAction(clearBtn.getAttribute('data-confirm')).then(function (ok) { if (ok) form.requestSubmit(clearBtn); });
  });
  form.addEventListener('submit', function (e) {
    var clearing = e.submitter && e.submitter.hasAttribute('data-session-clear');
    if (clearing) return;
    var when = new Date(local.value);
    if (isNaN(when)) { e.preventDefault(); local.focus(); return; }
    hidden.value = when.toISOString();
  });
})();
