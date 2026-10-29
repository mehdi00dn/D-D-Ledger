"""Upload quotas: how much image storage one user and one campaign may hold.

Per-file size is already capped by images.KIND_MAX_BYTES (input) and the importer's ZIP limits; this module adds
*totals*.  Usage is the sum of `stored_files.bytes`, i.e. the size of what is actually kept after re-encoding
(an 8 MB photo that becomes a 60 KB avatar counts 60 KB).

Change the limits with environment variables, no code change needed:
    LEDGER_QUOTA_USER_MB       total per user      (default 150)
    LEDGER_QUOTA_CAMPAIGN_MB   total per campaign  (default 300)
Set either to 0 to turn that limit off.

Sizing note: Supabase Free keeps 1 GB of storage, Pro 100 GB; with the defaults ~3 full campaigns fit in
the free tier, and the temp bucket (staging + exports) is cleaned by scripts/cleanup_temp.py.
"""
import os

MB = 1024 * 1024
DEFAULT_USER_MB = 150
DEFAULT_CAMPAIGN_MB = 300


class QuotaExceeded(Exception):
    """A limit would be passed; str(exc) is a message that is safe to show a user."""

    def __init__(self, message, scope):
        super().__init__(message)
        self.scope = scope           # 'user' | 'campaign'


def _limit(name, default_mb):
    try:
        return max(0, int(float(os.environ.get(name, default_mb)) * MB))
    except ValueError:
        return int(default_mb * MB)


def user_limit():
    return _limit('LEDGER_QUOTA_USER_MB', DEFAULT_USER_MB)


def campaign_limit():
    return _limit('LEDGER_QUOTA_CAMPAIGN_MB', DEFAULT_CAMPAIGN_MB)


def _mb(n):
    return f'{n / MB:.0f} MB' if n >= MB else f'{max(1, n // 1024)} KB'


def usage(db, user_id=None, campaign_id=None):
    col, val = ('user_id', user_id) if user_id is not None else ('campaign_id', campaign_id)
    row = db.execute(f'SELECT COALESCE(SUM(bytes), 0) AS n FROM stored_files WHERE {col} = ?', (val,)).fetchone()
    return int(row['n'])


def check(db, user_id, campaign_id, add_bytes=0):
    """Raise QuotaExceeded if adding `add_bytes` would pass the user's or the campaign's limit.
    With add_bytes=0 it only refuses when a limit has already been reached."""
    ul, cl = user_limit(), campaign_limit()
    if ul and user_id is not None:
        used = usage(db, user_id=user_id)
        if used + add_bytes > ul or (add_bytes == 0 and used >= ul):
            raise QuotaExceeded(
                f'Your upload storage is full ({_mb(used)} of {_mb(ul)}). Delete images you no longer need and try again.', 'user')
    if cl and campaign_id is not None:
        used = usage(db, campaign_id=campaign_id)
        if used + add_bytes > cl or (add_bytes == 0 and used >= cl):
            raise QuotaExceeded(
                f"This campaign's image storage is full ({_mb(used)} of {_mb(cl)}). Delete images you no longer need and try again.", 'campaign')


def record(db, key, user_id, campaign_id, kind, nbytes):
    db.execute('INSERT INTO stored_files (key, user_id, campaign_id, kind, bytes) VALUES (?, ?, ?, ?, ?) '
               'ON CONFLICT (key) DO UPDATE SET bytes = EXCLUDED.bytes', (key, user_id, campaign_id, kind, int(nbytes)))


def forget(db, keys):
    keys = [k for k in keys if k]
    if keys:
        db.execute(f"DELETE FROM stored_files WHERE key IN ({','.join('?' * len(keys))})", tuple(keys))


def summary(db, user_id, campaign_id=None):
    """Numbers for the UI: {'user': (used, limit), 'campaign': (used, limit) | None}."""
    out = {'user': (usage(db, user_id=user_id), user_limit()), 'campaign': None}
    if campaign_id is not None:
        out['campaign'] = (usage(db, campaign_id=campaign_id), campaign_limit())
    return out
