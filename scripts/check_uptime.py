"""Uptime check for the live site: GET <base>/healthz and exit 1 unless it answers 200 with {"ok": true}.

Used by .github/workflows/uptime.yml (a failed run emails the repository's watchers).  A single failed try is retried
twice, 20 seconds apart, so one slow cold start does not raise an alarm.  Only the status and the fixed hint that
/healthz returns are printed; nothing secret is ever in them.

    python scripts/check_uptime.py https://your-app.vercel.app
"""
import json
import sys
import time
import urllib.error
import urllib.request

TRIES = 3
PAUSE = 20          # seconds between tries
TIMEOUT = 25        # seconds per try


def check(base, tries=TRIES, pause=PAUSE, timeout=TIMEOUT):
    """(True, message) when healthy, else (False, why) after every try failed."""
    url = base.rstrip('/') + '/healthz'
    why = 'no try was made'
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'ledger-uptime-check'}), timeout=timeout) as resp:
                body = json.loads(resp.read(65536) or b'{}')
                if resp.status == 200 and body.get('ok') is True:
                    return True, 'healthy (try %d)' % attempt
                why = 'HTTP %s, ok=%s' % (resp.status, body.get('ok'))
        except urllib.error.HTTPError as exc:
            try:
                hint = json.loads(exc.read(65536) or b'{}').get('hint', '')
            except ValueError:
                hint = ''
            why = 'HTTP %s %s' % (exc.code, hint)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            why = '%s: %s' % (type(exc).__name__, exc)
        if attempt < tries:
            time.sleep(pause)
    return False, why.strip()


def main(argv):
    if len(argv) != 2 or not argv[1].startswith(('http://', 'https://')):
        print('usage: check_uptime.py https://your-app.vercel.app', file=sys.stderr)
        return 2
    ok, message = check(argv[1])
    print(('OK: ' if ok else 'DOWN: ') + message)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
