"""Test helper for DL-109: pages redirect to /campaigns/<short code>/..., tests still want the integer key."""
import re

import database

_TABLES = {'campaigns': 'campaigns', 'maps': 'maps', 'characters': 'characters', 'library': 'characters', 'journal': 'session_journal', 'quests': 'quests'}


def pk(kind, location):
    """The integer key of the campaign / map / character named in a Location header or URL (short code or number)."""
    m = re.search(r'/%s/([0-9a-f]{12}|\d+)' % kind, location)
    assert m, f'no /{kind}/ id in {location!r}'
    value = m.group(1)
    if value.isdigit():
        return int(value)
    db = database.get_db()
    try:
        return db.execute(f'SELECT id FROM {_TABLES[kind]} WHERE short_id = ?', (value,)).fetchone()['id']
    finally:
        db.close()


def short(kind, pk_):
    """The short code that page addresses use for one row (campaigns, maps, characters, library, journal, quests)."""
    db = database.get_db()
    try:
        return db.execute(f'SELECT short_id FROM {_TABLES[kind]} WHERE id = ?', (int(pk_),)).fetchone()['short_id']
    finally:
        db.close()
