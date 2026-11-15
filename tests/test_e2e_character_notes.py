"""DL-182: a line is drawn between one note and the next on a character's page."""
import json
import os
import pytest
if os.path.isdir('/opt/pw-browsers'):
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', '/opt/pw-browsers')
playwright = pytest.importorskip('playwright.sync_api')
from test_e2e_browser import Api, pw                      # noqa: F401  (pw is a fixture)
from conftest import BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_notes_are_separated_by_a_line(shared_server, pw, theme):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Lines')
    notes = ['<div>First note</div>', '<div>Second note</div>', {'t': 'Third, DM only', 'h': 1}]
    char = dm.character(cid, 'Kael Thorne', notes=json.dumps(notes))
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    page.goto(base + '/'); page.evaluate("t => localStorage.setItem('oaken-theme', t)", theme)
    page.goto(f'{base}/campaigns/{cid}/characters/{char}')
    blocks = page.locator('.detail-notes .note-block-text')
    assert blocks.count() == 3
    width = lambda i: blocks.nth(i).evaluate('e => parseFloat(getComputedStyle(e).borderTopWidth)')
    colour = lambda i: blocks.nth(i).evaluate('e => getComputedStyle(e).borderTopColor')
    assert width(0) == 0                                   # nothing above the first note
    assert width(1) >= 1 and width(2) >= 1                 # a line above each note after it
    assert colour(1) not in ('rgba(0, 0, 0, 0)', 'transparent')
    ctx.close()
