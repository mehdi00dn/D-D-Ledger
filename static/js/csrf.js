/* CSRF protection for scripted requests.
 * Every state-changing request must carry this session's token.  Forms include it as a
 * hidden field; this wrapper adds it to fetch() calls automatically, but only for requests
 * to our own origin (the token is never sent anywhere else).
 */
(function () {
  'use strict';
  var meta = document.querySelector('meta[name="csrf-token"]');
  var token = meta ? meta.content : '';
  if (!token || !window.fetch) return;
  var nativeFetch = window.fetch.bind(window);
  var SAFE = /^(GET|HEAD|OPTIONS)$/i;

  window.fetch = function (input, init) {
    try {
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      var method = (init && init.method) || (input && input.method) || 'GET';
      var sameOrigin = new URL(url, window.location.href).origin === window.location.origin;
      if (sameOrigin && !SAFE.test(method)) {
        init = Object.assign({}, init);
        var headers = new Headers(init.headers || (typeof input !== 'string' && input.headers) || {});
        if (!headers.has('X-CSRF-Token')) headers.set('X-CSRF-Token', token);
        init.headers = headers;
      }
    } catch (e) { /* malformed URL: let fetch report it */ }
    return nativeFetch(input, init);
  };
})();
