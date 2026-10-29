"""DL-139: the uptime script says healthy only for HTTP 200 with ok=true, and retries before it says down."""
import http.server
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import check_uptime  # noqa: E402


def _serve(responses):
    calls = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code, body = responses[min(len(calls), len(responses) - 1)]
            calls.append(self.path)
            self.send_response(code); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, 'http://127.0.0.1:%d' % srv.server_port, calls


def test_healthy_site_passes_on_the_first_try():
    srv, base, calls = _serve([(200, {'ok': True})])
    assert check_uptime.check(base, tries=3, pause=0)[0] is True and calls == ['/healthz']
    srv.shutdown()


def test_a_blip_is_retried_and_a_real_outage_fails_with_the_hint():
    srv, base, calls = _serve([(503, {'ok': False, 'hint': 'storage down'}), (200, {'ok': True})])
    assert check_uptime.check(base, tries=3, pause=0)[0] is True and len(calls) == 2
    srv.shutdown()
    srv, base, calls = _serve([(503, {'ok': False, 'hint': 'storage down'})])
    ok, why = check_uptime.check(base, tries=3, pause=0)
    assert ok is False and 'storage down' in why and len(calls) == 3
    srv.shutdown()


def test_200_without_ok_true_is_down_and_a_dead_host_is_down():
    srv, base, _ = _serve([(200, {'ok': False})])
    assert check_uptime.check(base, tries=1, pause=0)[0] is False
    srv.shutdown()
    assert check_uptime.check('http://127.0.0.1:1', tries=1, pause=0, timeout=2)[0] is False


def test_main_needs_an_address():
    assert check_uptime.main(['x']) == 2
