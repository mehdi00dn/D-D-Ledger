"""Observability: request ids, JSON access log, safe 500s.  Needs no database: uses a tiny Flask app."""
import io
import json
import logging

import pytest
from flask import Flask

import observability


@pytest.fixture()
def app_and_logs(monkeypatch):
    monkeypatch.setenv('LOG_FORMAT', 'json')
    monkeypatch.delenv('SENTRY_DSN', raising=False)
    app = Flask(__name__)
    observability.init_app(app)
    buf = io.StringIO()
    app.logger.handlers[0].setStream(buf)

    @app.route('/ok')
    def ok():
        return 'fine'

    @app.route('/boom')
    def boom():
        raise RuntimeError('secret detail')

    @app.route('/api/boom')
    def api_boom():
        raise RuntimeError('secret detail')

    @app.route('/healthz')
    def healthz():
        return 'ok'

    return app, buf


def _lines(buf):
    return [json.loads(x) for x in buf.getvalue().splitlines() if x.strip()]


def test_request_id_generated_and_logged(app_and_logs):
    app, buf = app_and_logs
    r = app.test_client().get('/ok?token=abc')
    rid = r.headers['X-Request-ID']
    assert len(rid) == 32
    line = _lines(buf)[-1]
    assert line['request_id'] == rid and line['status'] == 200 and line['route'] == 'ok'
    assert 'token' not in json.dumps(line)


def test_safe_incoming_id_kept_unsafe_replaced(app_and_logs):
    app, _ = app_and_logs
    c = app.test_client()
    assert c.get('/ok', headers={'X-Request-ID': 'abc-12345678'}).headers['X-Request-ID'] == 'abc-12345678'
    assert c.get('/ok', headers={'X-Request-ID': 'bad id<x>!'}).headers['X-Request-ID'] != 'bad id<x>!'


def test_healthz_and_static_not_logged(app_and_logs):
    app, buf = app_and_logs
    app.test_client().get('/healthz')
    assert buf.getvalue() == ''


def test_unhandled_error_is_generic_and_logged_with_stack(app_and_logs):
    app, buf = app_and_logs
    c = app.test_client()
    r = c.get('/boom')
    rid = r.headers['X-Request-ID']
    assert r.status_code == 500 and rid in r.get_data(as_text=True)
    assert 'secret detail' not in r.get_data(as_text=True)
    err = next(x for x in _lines(buf) if 'exc' in x)
    assert err['request_id'] == rid and 'RuntimeError' in err['exc']
    j = c.get('/api/boom')
    assert j.status_code == 500 and j.get_json()['request_id'] == j.headers['X-Request-ID']


def test_404_still_404(app_and_logs):
    app, _ = app_and_logs
    assert app.test_client().get('/nope').status_code == 404
