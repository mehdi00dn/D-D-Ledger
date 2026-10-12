// Small form behaviours that used to be inline onchange="..." attributes, which the Content-Security-Policy no longer allows.
//   data-auto-submit          -> submit the form when the control changes
//   data-auto-submit-request  -> same, through requestSubmit() so validation and submit handlers still run
document.addEventListener('change', function (e) {
  var el = e.target;
  if (!el || !el.form || !el.matches) return;
  if (el.matches('[data-auto-submit]')) el.form.submit();
  else if (el.matches('[data-auto-submit-request]')) el.form.requestSubmit();
});
