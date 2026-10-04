"""DL-100: card previews of character notes and faction bios keep the author's line structure."""
import html
import json
import re
import pytest
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


def test_preview_lines_keeps_breaks_and_drops_blank_runs():
    from app import preview_lines
    assert preview_lines('One  two\r\n\r\n\r\n  Three \t four\n') == 'One two\nThree four'
    assert preview_lines('') == '' and preview_lines(None) == ''


def test_notes_preview_keeps_one_line_per_block_break_and_paragraph():
    from app import notes_plain_preview
    raw = json.dumps(['<div>First line</div><div>Second line<br>Third line</div>', '<p>Next note</p>',
                      {'t': 'Secret', 'h': 1}])
    assert notes_plain_preview(raw, False) == 'First line\nSecond line\nThird line\nNext note'
    assert notes_plain_preview(raw, True).endswith('Next note\nSecret')
    assert notes_plain_preview('Legacy text\nsecond & <line>') == 'Legacy text\nsecond & <line>'


def _inner(page_html, cls):
    m = re.search(r'<(?:p|div) class="%s">(.*?)</(?:p|div)>' % cls, page_html, re.S)
    assert m, cls
    return html.unescape(m.group(1))


def test_cards_render_the_stored_line_breaks(make_user):
    dm = make_user(); cid = dm.new_campaign()
    dm.new_character(cid, name='Poet', notes=json.dumps(['<div>Line one</div><div>Line two</div><div><br></div><div>Line three</div>']))
    page = dm.get(f'/campaigns/{cid}/characters').data.decode()
    assert _inner(page, 'dossier-notes').strip() == 'Line one\nLine two\nLine three'
    r = dm.post(f'/campaigns/{cid}/factions/new', data={'name': 'Guild', 'color': '#336699', 'bio': 'Founded long ago.\n\n\nMotto: stay sharp.'}, content_type='multipart/form-data')
    assert r.status_code == 302
    page = dm.get(f'/campaigns/{cid}/factions').data.decode()
    assert _inner(page, 'group-bio').strip() == 'Founded long ago.\nMotto: stay sharp.'
