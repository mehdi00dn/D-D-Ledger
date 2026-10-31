"""DL-169: battle rows on phones and tablets: INIT and AC are small, and the conditions sit on the bottom row."""
import pytest
from conftest import BACKEND
from test_e2e_browser import Api, pw  # noqa: F401

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='browser tests need Postgres')

SIZES = {'phone sideways': (780, 360), 'big phone sideways': (915, 412), 'tablet upright': (768, 1024), 'phone upright': (390, 844),
         'small phone upright': (360, 740), 'tablet sideways': (1024, 768)}


def _box(loc):
    return loc.bounding_box()


def test_battle_rows_keep_conditions_on_the_bottom_row_at_every_small_size(shared_server, pw):
    base = shared_server.url
    dm = Api(base)
    cid = dm.campaign('Small screens')
    hero = dm.character(cid, 'Maedrai De Seras Edmah Rohkh', max_hp=50)
    pid = dm.post(f'/campaigns/{cid}/api/battle/add', json={'character_id': hero}).json()[-1]['id']
    assert dm.post(f'/campaigns/{cid}/api/battle/{pid}/conditions', json={'conditions': ['blinded', 'poisoned', 'prone']}).status_code == 200
    ctx = dm.context(pw, base)
    page = ctx.new_page()
    errors = []
    page.on('pageerror', lambda x: errors.append(str(x)))
    for label, (w, h) in SIZES.items():
        page.set_viewport_size({'width': w, 'height': h})
        page.goto(f'{base}/campaigns/{cid}/battle')
        page.wait_for_selector('.battle-row .cond-chip')
        row = page.locator('.battle-row').first
        conds, name = _box(row.locator('.cond-row')), _box(row.locator('.battle-name'))
        init, ac = row.locator('[data-action=initiative]'), row.locator('[data-action=ac]')
        assert _box(init)['height'] <= 32 and _box(ac)['height'] <= 32, (label, _box(init))      # was 36-40 px tall boxes
        assert _box(init)['width'] <= 44, (label, _box(init))
        assert conds['y'] > name['y'] + name['height'], f'{label}: the conditions belong under the name line'
        if w >= 761:                                                                        # sideways: the conditions share the HP buttons' line
            ctl = _box(row.locator('.hp-controls'))
            assert conds['y'] + conds['height'] / 2 > _box(init)['y'] + _box(init)['height'], f'{label}: conditions below INIT and AC'
            assert abs((ctl['y'] + ctl['height'] / 2) - (conds['y'] + conds['height'] / 2)) < 24, f'{label}: conditions and HP buttons share a line'
        else:                                                                               # upright: the very last line of the card
            ctl = _box(row.locator('.hp-controls'))
            assert conds['y'] >= ctl['y'] + ctl['height'] - 1, f'{label}: conditions come after the HP buttons'
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), f'{label}: page scrolls sideways'
    ctx.close()
    assert not errors, errors
