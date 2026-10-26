"""DL-72: page structure that screen readers rely on - one h1, headings that never skip a level, named landmarks and
form controls - on the pages that have empty states and on the newest pages."""
import re

import pytest
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

HEADING = re.compile(r'<h([1-6])\b', re.I)


def _headings(html):
    body = re.sub(r'<(script|style)\b.*?</\1>', '', html, flags=re.S | re.I)
    body = re.sub(r'<div class="modal-overlay.*?</div>\s*</div>', '', body, flags=re.S)       # dialogs are outside the page outline
    return [int(m.group(1)) for m in HEADING.finditer(body)]


def _check(html, where):
    levels = _headings(html)
    assert levels.count(1) == 1, f'{where}: expected one h1, found {levels.count(1)}'
    for prev, cur in zip(levels, levels[1:]):
        assert cur <= prev + 1, f'{where}: heading level jumps from h{prev} to h{cur}'


def test_pages_have_one_h1_and_headings_in_order_when_empty(make_user):
    dm = make_user(); cid = dm.new_campaign()
    base = f'/campaigns/{cid}'
    for path in ('/home', '/characters', '/factions', '/maps', '/battle', '/dice', '/activity', '/treasury', '/encounters', '/reference', '/edit'):
        r = dm.get(base + path)
        assert r.status_code == 200, path
        _check(r.data.decode(), path or '/')
    for path in ('/campaigns', '/notifications', '/account'):
        _check(dm.get(path).data.decode(), path)


def test_the_encounter_builder_has_a_heading_and_the_sidebar_a_name(make_user):
    dm = make_user(); cid = dm.new_campaign()
    dm.post(f'/campaigns/{cid}/encounters/new', data={'name': 'x'})
    from conftest import q
    eid = q('SELECT id FROM encounters WHERE campaign_id = ?', cid)[0]['id']
    html = dm.get(f'/campaigns/{cid}/encounters/{eid}').data.decode()
    _check(html, 'encounter builder')
    assert 'id="sidebar" aria-label="Menu"' in html


def test_pages_with_a_second_aside_name_both_landmarks(make_user):
    dm = make_user(); cid = dm.new_campaign()
    for path in (f'/campaigns/{cid}/characters/new', f'/campaigns/{cid}/edit'):
        html = dm.get(path).data.decode()
        asides = re.findall(r'<aside\b[^>]*>', html)
        assert len(asides) >= 2 and all('aria-label=' in a for a in asides), (path, asides)


def test_note_editors_and_role_selects_have_names(make_user):
    dm = make_user(); cid = dm.new_campaign()
    html = dm.get(f'/campaigns/{cid}/characters/new').data.decode()
    editors = re.findall(r'<div class="note-editor"[^>]*>', html)
    assert editors and all('aria-label=' in e for e in editors)
    html = dm.get(f'/campaigns/{cid}/edit').data.decode()
    assert all('aria-label=' in s for s in re.findall(r'<select\b[^>]*title=[^>]*>', html))
