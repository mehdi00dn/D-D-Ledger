"""Vercel entry point for the Campaign Ledger Flask application.

All persistent state lives in Postgres (DATABASE_URL); nothing is seeded,
copied or migrated on a cold start.  Apply schema changes explicitly with
`python migrate.py` (see VERCEL_DEPLOY.md).
"""
import os

if os.environ.get('VERCEL') and not os.environ.get('DATABASE_URL'):
    raise RuntimeError('DATABASE_URL is not configured for this deployment '
                       '(Vercel -> Project -> Settings -> Environment Variables).')

# Uploads are still written under /tmp until Phase 2 moves them to object storage.
os.environ.setdefault('DND_RUNTIME_DIR', '/tmp/dnd-ledger')
os.makedirs(os.path.join(os.environ['DND_RUNTIME_DIR'], 'uploads'), exist_ok=True)

from app import app  # noqa: E402,F401  (Vercel's Python runtime looks for `app`)
