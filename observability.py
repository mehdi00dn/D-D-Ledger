"""Observability: structured logs, request ids, access log and error tracking.

* One JSON object per log line on stdout (Vercel keeps stdout as runtime logs), so logs can be
  filtered by request_id, status, user or path.  Set LOG_FORMAT=text for plain lines locally.
* Every request gets an id (X-Request-ID, echoed in the response) so a user's "something broke"
  can be matched to one log line.  A caller-supplied id is only trusted if it looks harmless.
* Every request except static files and /healthz writes one access line: method, route, status,
  duration, user id.  Query strings are never logged (they can hold tokens).
* Unhandled exceptions are logged with a stack trace and answered with a generic 500 that only
  shows the request id.  Specific handlers in app.py (400 for bad input, 503 for storage) still win.
* Optional error tracking: set SENTRY_DSN and install sentry-sdk and errors are sent to Sentry.
  Without either, nothing changes.  No personal data is sent (send_default_pii is off).
"""
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone

from flask import Response, g, jsonify, request, session
from werkzeug.exceptions import HTTPException

_SAFE_ID = re.compile(r'^[A-Za-z0-9._:-]{8,64}$')
_QUIET_ENDPOINTS = {'static', 'healthz'}
_EXTRA_FIELDS = ('request_id', 'method', 'route', 'status', 'duration_ms', 'user_id')


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {
            'ts': datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='milliseconds'),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
        }
        for key in _EXTRA_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                out[key] = value
        if record.exc_info:
            out['exc'] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


class _RequestIdFilter(logging.Filter):
    """Stamp every record logged during a request (app.logger.error(...) included) with its id."""

    def filter(self, record):
        try:
            if getattr(record, 'request_id', None) is None:
                rid = getattr(g, 'request_id', None)
                if rid:
                    record.request_id = rid
        except RuntimeError:        # outside an application context
            pass
        return True


def _json_logs_wanted():
    fmt = os.environ.get('LOG_FORMAT', '').lower()
    if fmt:
        return fmt == 'json'
    return bool(os.environ.get('VERCEL'))


def _setup_logging(app):
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    if _json_logs_wanted():
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s [%(request_id)s] %(message)s',
                                               defaults={'request_id': '-'}))
    app.logger.handlers[:] = [handler]
    app.logger.setLevel(os.environ.get('LOG_LEVEL', 'INFO').upper())
    app.logger.propagate = False


def _setup_sentry(app):
    dsn = os.environ.get('SENTRY_DSN')
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        app.logger.warning('SENTRY_DSN is set but sentry-sdk is not installed; error tracking is off.')
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get('VERCEL_ENV') or os.environ.get('FLASK_ENV') or 'development',
        release=(os.environ.get('VERCEL_GIT_COMMIT_SHA') or '')[:12] or None,
        send_default_pii=False,
        traces_sample_rate=0.0,
    )
    return True


def _report(exc, request_id):
    if 'sentry_sdk' not in sys.modules:
        return
    import sentry_sdk
    with sentry_sdk.new_scope() as scope:
        scope.set_tag('request_id', request_id)
        sentry_sdk.capture_exception(exc)


def init_app(app):
    _setup_logging(app)
    sentry_on = _setup_sentry(app)

    @app.before_request
    def _start_request():
        incoming = request.headers.get('X-Request-ID', '')
        g.request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        g._started = time.perf_counter()

    @app.after_request
    def _finish_request(resp):
        rid = getattr(g, 'request_id', None)
        if rid:
            resp.headers['X-Request-ID'] = rid
        if request.endpoint not in _QUIET_ENDPOINTS and hasattr(g, '_started'):
            ms = round((time.perf_counter() - g._started) * 1000, 1)
            level = logging.ERROR if resp.status_code >= 500 else logging.INFO
            app.logger.log(
                level, '%s %s -> %s in %sms', request.method, request.endpoint or request.path, resp.status_code, ms,
                extra={'method': request.method, 'route': request.endpoint or 'unmatched',
                       'status': resp.status_code, 'duration_ms': ms, 'user_id': session.get('user_id')})
        return resp

    @app.errorhandler(Exception)
    def _unhandled(exc):
        if isinstance(exc, HTTPException):
            return exc
        rid = getattr(g, 'request_id', None) or uuid.uuid4().hex
        app.logger.error('unhandled %s on %s %s', type(exc).__name__, request.method, request.endpoint or request.path,
                         exc_info=(type(exc), exc, exc.__traceback__), extra={'request_id': rid})
        if sentry_on:
            _report(exc, rid)
        if request.is_json or '/api/' in request.path:
            resp = jsonify({'error': 'something went wrong on our side', 'request_id': rid})
            resp.status_code = 500
            return resp
        return Response(f'Something went wrong on our side. Please try again in a moment.\n'
                        f'If it keeps happening, quote this reference: {rid}', status=500, mimetype='text/plain')
