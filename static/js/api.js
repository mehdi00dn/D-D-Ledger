/* Reading the server's standard error shape.
 *
 * Every AJAX route answers a failure as  {"error": "<sentence for people>", "code": "<slug>", "request_id": "..."}
 * with a real HTTP status (see app.py: api_error).  This file turns that into one thing the pages can use:
 *
 *   var msg = await LedgerApi.message(res, 'Could not save.')   // the server's sentence, or a status-based fallback
 *   await LedgerApi.check(res, 'Could not save.')               // throws LedgerApi.Error (message, status, code) unless res.ok
 *   LedgerApi.notify(msg)                                       // a small alert toast, de-duplicated, gone after a few seconds
 *   LedgerApi.fail(res, 'Could not save.')                      // message() + notify() in one call; returns the message
 *
 * Polling requests must stay quiet, so nothing here runs unless a page calls it.
 */
(function () {
  'use strict';

  var FALLBACKS = {
    0: 'No connection. Check your network and try again.',
    401: 'Your session ended. Reload the page to sign in again.',
    403: 'You do not have permission to do that.',
    404: 'That could not be found. It may have been deleted.',
    413: 'That is too large to send.',
    429: 'Too many requests. Wait a moment and try again.'
  };

  function ApiError(message, status, code, requestId) {
    this.name = 'LedgerApiError';
    this.message = message;
    this.status = status;
    this.code = code || null;
    this.requestId = requestId || null;
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  async function details(res, fallback) {
    var body = null;
    try { body = await res.clone().json(); } catch (e) { /* not JSON (an HTML error page, a proxy): use the fallback */ }
    var text = body && typeof body.error === 'string' && body.error;
    var message = text || FALLBACKS[res.status] || fallback || 'Something went wrong. Please try again.';
    if (!text && res.status >= 500 && fallback) message = fallback;
    return new ApiError(message, res.status, body && body.code, body && body.request_id);
  }

  var holder = null;
  function notify(message, kind) {
    if (!message) return;
    if (!holder || !document.body.contains(holder)) {
      holder = document.createElement('div');
      holder.className = 'ledger-toasts';
      holder.setAttribute('role', 'region');
      holder.setAttribute('aria-label', 'Notices');
      document.body.appendChild(holder);
    }
    var again = Array.prototype.find.call(holder.children, function (el) { return el.dataset.msg === message; });
    if (again) { again.classList.remove('is-in'); void again.offsetWidth; again.classList.add('is-in'); return; }
    var t = document.createElement('div');
    t.className = 'ledger-toast is-in' + (kind === 'info' ? ' is-info' : '');
    t.dataset.msg = message;
    t.setAttribute('role', kind === 'info' ? 'status' : 'alert');
    var span = document.createElement('span');
    span.textContent = message;                          // text, never HTML: the message may echo user input
    var close = document.createElement('button');
    close.type = 'button'; close.className = 'ledger-toast-close'; close.setAttribute('aria-label', 'Dismiss'); close.textContent = '×';
    t.appendChild(span); t.appendChild(close);
    function remove() { t.classList.remove('is-in'); setTimeout(function () { t.remove(); }, 200); }
    close.addEventListener('click', remove);
    setTimeout(remove, 7000);
    holder.appendChild(t);
  }

  window.LedgerApi = {
    Error: ApiError,
    notify: notify,
    message: async function (res, fallback) { return (await details(res, fallback)).message; },
    check: async function (res, fallback) { if (!res.ok) throw await details(res, fallback); return res; },
    fail: async function (res, fallback) { var err = await details(res, fallback); notify(err.message); return err.message; },
    networkMessage: FALLBACKS[0]
  };
})();
