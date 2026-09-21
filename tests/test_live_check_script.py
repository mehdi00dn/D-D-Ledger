"""scripts/live_check.py is what you run against the deployed site; prove it against a real server."""
import os, subprocess, sys
import pytest
from conftest import BACKEND, APP_DIR

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def run_check(server, *extra):
    return subprocess.run([sys.executable, os.path.join(APP_DIR, 'scripts', 'live_check.py'), '--base', server.url, *extra],
                          capture_output=True, text=True, timeout=240)


def test_live_check_passes_end_to_end_including_the_large_direct_upload(shared_server):
    r = run_check(shared_server)
    print(r.stdout)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
    assert ' 0 failed' in r.stdout
    assert 'this computer can upload directly to storage' in r.stdout and 'FAIL' not in r.stdout


def test_live_check_reports_an_unreachable_site_cleanly():
    r = subprocess.run([sys.executable, os.path.join(APP_DIR, 'scripts', 'live_check.py'), '--base', 'http://127.0.0.1:1'],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and 'FAIL' in r.stdout and 'Traceback' not in r.stderr
