"""Real-time wake-ups through Supabase Realtime Broadcast.

The server never sends data over Realtime -- only an empty "something changed" ping.  A page that hears it
refreshes through the same permission-checked, redacted endpoints it already polls, so nothing sensitive can
leak through the channel and a Player and a DM need no separate feeds.  Polling stays on underneath as the
safety net: with Realtime unconfigured, unreachable or blocked, the app behaves exactly as it did.

Channel names are unguessable (HMAC of the app secret + campaign id) and are only ever printed into pages
a campaign member is allowed to open.  Someone who learns a name (a removed member, say) can only watch
pings and cannot read any data from them.

Needs SUPABASE_URL, SUPABASE_SECRET_KEY (server -> Realtime) and SUPABASE_PUBLISHABLE_KEY (browser -> Realtime).
REALTIME_ENABLED=0 switches it off.
"""
import hashlib
import hmac
import logging
import os
import threading
import time

import requests

log = logging.getLogger('ledger.realtime')

PUBLISH_TIMEOUT = 1.5            # seconds; a ping is never worth holding anything up for
BREAKER_SECONDS = 30             # after a failed publish, stay quiet this long instead of failing on every change
_breaker_until = 0.0
_lock = threading.Lock()


def config(env=None):
    """{'url', 'secret_key', 'publishable_key'} when Realtime is fully configured, else None."""
    env = os.environ if env is None else env
    if env.get('REALTIME_ENABLED', '1') == '0':
        return None
    url = (env.get('SUPABASE_URL') or '').rstrip('/')
    secret = env.get('SUPABASE_SECRET_KEY') or env.get('SUPABASE_SERVICE_ROLE_KEY')
    public = env.get('SUPABASE_PUBLISHABLE_KEY') or env.get('SUPABASE_ANON_KEY')
    if not (url.startswith(('http://', 'https://')) and secret and public):
        return None
    return {'url': url, 'secret_key': secret, 'publishable_key': public}


def channel_name(app_secret, campaign_id):
    digest = hmac.new(str(app_secret).encode(), f'realtime:campaign:{int(campaign_id)}'.encode(), hashlib.sha256).hexdigest()
    return 'lc-' + digest[:40]


def browser_settings(app_secret, campaign_id):
    """What a campaign member's page needs to listen, or None when Realtime is off."""
    cfg = config()
    if not cfg:
        return None
    return {'url': cfg['url'], 'key': cfg['publishable_key'], 'channel': channel_name(app_secret, campaign_id)}


def csp_origins():
    """Origins the page may connect to for Realtime (https for the REST side, wss for the socket)."""
    cfg = config()
    if not cfg:
        return []
    host = cfg['url'].split('://', 1)[1]
    return [cfg['url'], ('wss://' if cfg['url'].startswith('https') else 'ws://') + host]


def scope_for(path):
    """Which pages care: 'battle' (battle pages), 'map' (map pages only) or 'both'."""
    if '/api/battle' in path:
        return 'battle'
    if '/api/maps/' in path and ('/drawings' in path or '/fog' in path):
        return 'map'
    return 'both'


def publish(campaign_id, scope, app_secret):
    """Send one ping.  Never raises and never blocks longer than PUBLISH_TIMEOUT."""
    global _breaker_until
    cfg = config()
    if not cfg or time.time() < _breaker_until:
        return False
    body = {'messages': [{'topic': channel_name(app_secret, campaign_id), 'event': 'changed',
                          'payload': {'s': scope}, 'private': False}]}
    try:
        r = requests.post(cfg['url'] + '/realtime/v1/api/broadcast', json=body, timeout=PUBLISH_TIMEOUT,
                          headers={'apikey': cfg['secret_key'], 'Authorization': 'Bearer ' + cfg['secret_key']})
        if r.status_code >= 300:
            raise RuntimeError(f'HTTP {r.status_code}')
        return True
    except Exception as exc:                                    # noqa: BLE001 -- a ping must never break a request
        with _lock:
            _breaker_until = time.time() + BREAKER_SECONDS
        log.warning('realtime ping failed (%s); pausing pings for %ss', exc, BREAKER_SECONDS)
        return False


def reset_breaker():                                            # for tests
    global _breaker_until
    _breaker_until = 0.0
