"""Login / registration throttling, backed by the database.

Rules are deliberately layered so that one abuser cannot lock others out, yet a
distributed guessing attack is still capped:

  login-pair   (username + IP)   6 failures / 15 min    the normal "wrong password" limit
  login-user   (username, any IP) 40 failures / hour     backstop against distributed guessing
  login-ip     (IP, any name)    60 failures / 15 min    generous: mobile carriers put many
                                                          players behind ONE address
  register-ip  (IP)              20 sign-ups / hour      slows mass account creation

Every threshold is overridable by env (LOGIN_LIMIT_PAIR, ...).  Stored keys are HMACs, so
neither usernames nor IP addresses sit in the table in clear text.
"""
import hashlib
import hmac
import os
import random

WINDOWS = {
    'login-pair': (int(os.environ.get('LOGIN_LIMIT_PAIR', 6)), 15 * 60),
    'login-user': (int(os.environ.get('LOGIN_LIMIT_USER', 40)), 60 * 60),
    'login-ip': (int(os.environ.get('LOGIN_LIMIT_IP', 60)), 15 * 60),
    'register-ip': (int(os.environ.get('REGISTER_LIMIT_IP', 20)), 60 * 60),
}
_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"


def _key(secret, kind, value):
    return hmac.new(secret.encode(), ('%s|%s' % (kind, value.lower())).encode(), hashlib.sha256).hexdigest()[:40]


def _keys_for_login(secret, username, ip):
    return {
        'login-pair': _key(secret, 'pair', username + '|' + ip),
        'login-user': _key(secret, 'user', username),
        'login-ip': _key(secret, 'ip', ip),
    }


def _retry_after(db, kind, key):
    """Seconds until the oldest counted failure ages out, or 0 when under the limit."""
    limit, window = WINDOWS[kind]
    rows = db.execute(
        "SELECT CAST(EXTRACT(EPOCH FROM (%s - created_at)) AS INTEGER) AS age FROM login_attempts "
        "WHERE kind = ? AND key = ? AND created_at > %s - make_interval(secs => ?) ORDER BY created_at ASC LIMIT ?"
        % (_NOW, _NOW), (kind, key, window, limit)).fetchall()
    if len(rows) < limit:
        return 0
    return max(1, window - (rows[0]['age'] or 0))


def login_blocked_for(db, secret, username, ip):
    keys = _keys_for_login(secret, username, ip)
    return max((_retry_after(db, kind, key) for kind, key in keys.items()), default=0)


def record_login_failure(db, secret, username, ip):
    for kind, key in _keys_for_login(secret, username, ip).items():
        db.execute('INSERT INTO login_attempts (kind, key) VALUES (?, ?)', (kind, key))
    _maybe_cleanup(db)


def clear_login_failures(db, secret, username, ip):
    """A correct password wipes that user+IP's counter (the IP-wide counter keeps running)."""
    db.execute('DELETE FROM login_attempts WHERE kind = ? AND key = ?',
               ('login-pair', _key(secret, 'pair', username + '|' + ip)))


def register_blocked_for(db, secret, ip):
    return _retry_after(db, 'register-ip', _key(secret, 'regip', ip))


def record_registration(db, secret, ip):
    db.execute('INSERT INTO login_attempts (kind, key) VALUES (?, ?)', ('register-ip', _key(secret, 'regip', ip)))
    _maybe_cleanup(db)


def _maybe_cleanup(db):
    if random.random() < 0.05:                      # keep the table small without a cron job
        db.execute("DELETE FROM login_attempts WHERE created_at < %s - make_interval(secs => ?)" % _NOW, (24 * 3600,))
