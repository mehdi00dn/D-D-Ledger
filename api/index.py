"""Vercel entry point for the D&D Ledger Flask application."""
import os
import shutil

# Ensure Vercel-specific runtime paths are available before importing app.
os.environ.setdefault('DND_RUNTIME_DIR', '/tmp/dnd-ledger')
os.makedirs(os.environ['DND_RUNTIME_DIR'], exist_ok=True)

from app import app  # noqa: E402
from database import init_db  # noqa: E402

# SQLite and uploaded files are ephemeral on Vercel. Copy the bundled seed
# uploads into /tmp on a cold start so existing sample data still renders.
_RUNTIME_UPLOADS = os.path.join(os.environ['DND_RUNTIME_DIR'], 'uploads')
_SEED_UPLOADS = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'uploads')
if not os.path.exists(_RUNTIME_UPLOADS):
    if os.path.isdir(_SEED_UPLOADS):
        shutil.copytree(_SEED_UPLOADS, _RUNTIME_UPLOADS)
    else:
        os.makedirs(_RUNTIME_UPLOADS, exist_ok=True)

init_db()

# Vercel's Python runtime discovers the Flask WSGI application as `app`.
