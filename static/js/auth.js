/* Log in / sign up: show-hide password, password strength, live checks, and a loading state on submit.
 * The server checks everything again; this only tells people sooner. */
(function () {
  'use strict';

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-toggle-password]');
    if (!btn) return;
    var input = document.querySelector(btn.getAttribute('data-toggle-password'));
    if (!input) return;
    var show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.setAttribute('aria-pressed', show ? 'true' : 'false');
    btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  });

  function strength(pw) {
    if (!pw) return 0;
    var score = 0;
    if (pw.length >= 8) score++;
    if (pw.length >= 12) score++;
    if (/[a-z]/.test(pw) && /[A-Z]/.test(pw) && /\d/.test(pw)) score++;
    if (/[^A-Za-z0-9]/.test(pw) || pw.length >= 16) score++;
    if (pw.length < 8) score = Math.min(score, 1);
    return score;
  }
  var WORDS = ['Add a few more characters.', 'Short of 8 characters.', 'Okay. Longer is stronger.', 'Good.', 'Strong.'];

  var pwInput = document.querySelector('[data-strength-input]');
  var meter = document.querySelector('[data-strength-meter]');
  var meterText = document.querySelector('[data-strength-text]');
  var confirmInput = document.querySelector('[data-confirm-input]');
  var confirmHint = document.querySelector('[data-confirm-hint]');
  var emailInput = document.querySelector('[data-email-input]');
  var emailHint = document.querySelector('[data-email-hint]');
  var emailDefault = emailHint ? emailHint.textContent : '';

  function checkConfirm(final) {
    if (!confirmInput || !confirmHint) return;
    var same = confirmInput.value === pwInput.value;
    var show = confirmInput.value.length > 0 && (final || confirmInput.value.length >= pwInput.value.length || !same && final);
    confirmHint.textContent = !confirmInput.value ? '' : same ? 'Passwords match.' : (show ? 'Passwords do not match yet.' : '');
    confirmHint.className = 'auth-hint' + (!confirmInput.value || (!same && !show) ? '' : same ? ' is-good' : ' is-bad');
    confirmInput.classList.toggle('is-bad', !!confirmInput.value && !same && show);
  }

  if (pwInput && meter) {
    pwInput.addEventListener('input', function () {
      var level = strength(pwInput.value);
      meter.setAttribute('data-level', level);
      if (meterText) meterText.textContent = pwInput.value ? WORDS[pwInput.value.length < 8 ? 1 : level] : 'At least 8 characters.';
      checkConfirm(false);
    });
  }
  if (confirmInput && pwInput) {
    confirmInput.addEventListener('input', function () { checkConfirm(false); });
    confirmInput.addEventListener('blur', function () { checkConfirm(true); });
  }
  if (emailInput && emailHint) {
    emailInput.addEventListener('blur', function () {
      var v = emailInput.value.trim();
      var ok = !v || /^[^\s@<>,;"\\]+@[^\s@.]+(\.[^\s@.]+)+$/.test(v);
      emailInput.classList.toggle('is-bad', !ok);
      emailHint.textContent = ok ? emailDefault : 'That does not look like an email address.';
      emailHint.classList.toggle('is-bad', !ok);
    });
  }

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-once') || e.defaultPrevented) return;
    if (form.hasAttribute('data-signup') && pwInput && confirmInput && pwInput.value !== confirmInput.value) {
      e.preventDefault();
      checkConfirm(true);
      confirmInput.focus();
      return;
    }
    setTimeout(function () {
      form.querySelectorAll('button[type=submit]').forEach(function (b) { b.disabled = true; b.classList.add('is-loading'); });
    }, 0);
  });
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    document.querySelectorAll('form[data-once] button[type=submit]').forEach(function (b) { b.disabled = false; b.classList.remove('is-loading'); });
  });
}());
