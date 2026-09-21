"""Vercel entry point for the Campaign Ledger Flask application.

All persistent state lives in the cloud: Postgres (DATABASE_URL) and object storage
(Supabase Storage).  Nothing is seeded, copied or migrated on a cold start -- apply
schema changes explicitly with `python migrate.py` (see VERCEL_DEPLOY.md).

A serverless instance's disk is throw-away and every request may land on a different
instance, so the app REFUSES TO START on Vercel unless both stores are configured.
Failing loudly at deploy time beats silently losing uploads later.
"""
import os

if os.environ.get('VERCEL'):
    problems = []
    if not os.environ.get('DATABASE_URL'):
        problems.append('DATABASE_URL (the Supabase Transaction-pooler connection string)')
    if not os.environ.get('SECRET_KEY'):
        problems.append('SECRET_KEY (a long random string that signs login cookies)')
    backend = (os.environ.get('STORAGE_BACKEND') or 'supabase').lower()
    if backend != 'supabase':
        problems.append('STORAGE_BACKEND must be "supabase" (or unset) on Vercel - local disk is not persistent')
    if not os.environ.get('SUPABASE_URL'):
        problems.append('SUPABASE_URL')
    if not (os.environ.get('SUPABASE_SECRET_KEY') or os.environ.get('SUPABASE_SERVICE_ROLE_KEY')):
        problems.append('SUPABASE_SECRET_KEY (or the legacy SUPABASE_SERVICE_ROLE_KEY)')
    if problems:
        raise RuntimeError('This deployment is missing required environment variables '
                           '(Vercel -> Project -> Settings -> Environment Variables):\n  - ' + '\n  - '.join(problems))
    os.environ.setdefault('STORAGE_BACKEND', 'supabase')

from app import app  # noqa: E402,F401  (Vercel's Python runtime looks for `app`)
