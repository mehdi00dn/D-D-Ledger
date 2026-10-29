"""DL-84: every column of `users` is explained in docs/DATA_WE_COLLECT.md, so personal data is never added silently."""
from pathlib import Path

import pytest

from conftest import BACKEND, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='information_schema')


def test_every_users_column_is_in_the_inventory(appmod):
    doc = (Path(__file__).resolve().parent.parent / 'docs' / 'DATA_WE_COLLECT.md').read_text()
    cols = [r['column_name'] for r in q("SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND table_schema = current_schema()")]
    assert cols, 'no users table found'
    missing = [c for c in cols if c != 'id' and f'`{c}`' not in doc]
    assert not missing, f'add these users columns to docs/DATA_WE_COLLECT.md: {missing}'
