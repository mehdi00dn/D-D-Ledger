"""Shared dice (DL-107) and click-to-roll (DL-116): rolls are made and stored on the server, and everyone in the
campaign sees them -- except a DM's private rolls, which only DMs see."""
import pytest
from conftest import q


def roll(u, cid, **body):
    return u.json(f'/campaigns/{cid}/api/dice/roll', body)


def feed(u, cid, **params):
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    return u.get(f'/campaigns/{cid}/api/dice/rolls' + (f'?{qs}' if qs else '')).get_json()


@pytest.fixture
def table(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, pl)
    return dm, pl, cid


def test_a_roll_is_made_on_the_server_stored_and_totalled(table):
    dm, pl, cid = table
    r = roll(pl, cid, dice={'20': 1, '6': 2}, modifier=3, label='Fireball')
    assert r.status_code == 200
    out = r.get_json()
    values = [d['value'] for d in out['dice']]
    assert sorted(d['sides'] for d in out['dice']) == [6, 6, 20]
    assert all(1 <= d['value'] <= d['sides'] for d in out['dice'])
    assert out['total'] == sum(values) + 3 and out['modifier'] == 3
    assert out['label'] == 'Fireball' and out['mine'] is True and out['who'] == pl.name
    assert q('SELECT COUNT(*) AS n FROM dice_rolls WHERE campaign_id = ?', cid)[0]['n'] == 1


def test_everyone_in_the_campaign_sees_the_roll(table):
    dm, pl, cid = table
    roll(pl, cid, dice={'20': 1}, label='Stealth')
    rolls = feed(dm, cid)['rolls']
    assert [x['label'] for x in rolls] == ['Stealth']
    assert rolls[0]['who'] == pl.name and rolls[0]['mine'] is False


def test_feed_after_and_init_never_replay_old_rolls(table):
    dm, pl, cid = table
    first = roll(dm, cid, dice={'6': 1}).get_json()
    init = feed(pl, cid, init=1)
    assert init['rolls'] == [] and init['latest'] == first['id']
    second = roll(dm, cid, dice={'6': 1}).get_json()
    got = feed(pl, cid, after=first['id'])
    assert [x['id'] for x in got['rolls']] == [second['id']] and got['latest'] == second['id']
    assert feed(pl, cid, after=second['id'])['rolls'] == []


def test_advantage_and_disadvantage_keep_the_right_d20(table):
    dm, pl, cid = table
    for mode in ('advantage', 'disadvantage'):
        for _ in range(15):
            out = roll(pl, cid, dice={'20': 1}, modifier=2, mode=mode).get_json()
            assert len(out['dice']) == 2 and sum(d['dropped'] for d in out['dice']) == 1
            vals = [d['value'] for d in out['dice']]
            kept = max(vals) if mode == 'advantage' else min(vals)
            assert out['total'] == kept + 2 and out['mode'] == mode
    assert roll(pl, cid, dice={'20': 2}, mode='advantage').status_code == 400       # only a single d20
    assert roll(pl, cid, dice={'6': 1}, mode='advantage').status_code == 400
    assert roll(pl, cid, dice={'20': 1}, mode='lucky').status_code == 400


@pytest.mark.parametrize('body', [
    {}, {'dice': {}}, {'dice': {'7': 1}}, {'dice': {'20': -1}}, {'dice': {'20': 'x'}}, {'dice': {'20': 0}},
    {'dice': {'20': 41}}, {'dice': {'6': 30, '8': 30}}, {'dice': {'20': 1}, 'modifier': 5000},
    {'dice': {'20': 1}, 'modifier': 'abc'}, {'dice': [20]}, {'dice': {'20': 1}, 'character_id': 'x'},
])
def test_bad_rolls_are_refused_and_nothing_is_stored(table, body):
    dm, pl, cid = table
    assert roll(pl, cid, **body).status_code in (400, 404)
    assert q('SELECT COUNT(*) AS n FROM dice_rolls WHERE campaign_id = ?', cid)[0]['n'] == 0


def test_label_is_trimmed_and_capped(table):
    dm, pl, cid = table
    out = roll(pl, cid, dice={'4': 1}, label='  ' + 'x' * 200).get_json()
    assert out['label'] == 'x' * 80


def test_private_rolls_are_for_dms_only(table):
    dm, pl, cid = table
    mine = roll(dm, cid, dice={'20': 1}, label='Secret check', private=True).get_json()
    assert mine['private'] is True
    assert [x['label'] for x in feed(dm, cid)['rolls']] == ['Secret check']
    assert feed(pl, cid)['rolls'] == []                                  # never sent to a player
    assert feed(pl, cid, after=0)['rolls'] == []
    # a Player asking for "private" just gets an ordinary public roll
    out = roll(pl, cid, dice={'20': 1}, private=True).get_json()
    assert out['private'] is False
    assert len(feed(dm, cid)['rolls']) == 2


def test_rolling_for_a_character(table):
    dm, pl, cid = table
    hero = dm.new_character(cid, name='Hero')
    ogre = dm.new_character(cid, name='Ogre', is_npc='on')
    out = roll(pl, cid, dice={'20': 1}, character_id=hero, label='Athletics').get_json()
    assert out['character'] == 'Hero' and out['character_id'] == hero
    assert roll(pl, cid, dice={'20': 1}, character_id=ogre).status_code == 403     # the DM rolls for NPCs
    assert roll(dm, cid, dice={'20': 1}, character_id=ogre).get_json()['character'] == 'Ogre'
    assert roll(pl, cid, dice={'20': 1}, character_id=999999).status_code == 404


def test_rolls_do_not_cross_campaigns(make_user):
    a, b = make_user(), make_user()
    ca, cb = a.new_campaign(), b.new_campaign()
    mine = roll(a, ca, dice={'20': 1}).get_json()
    assert feed(b, cb)['rolls'] == []
    foreign = b.new_character(cb, name='Theirs')
    assert roll(a, ca, dice={'20': 1}, character_id=foreign).status_code == 404      # a character from another campaign
    assert a.get(f'/campaigns/{cb}/api/dice/rolls').status_code == 404               # not a member
    assert roll(a, cb, dice={'20': 1}).status_code == 404
    assert feed(a, ca)['rolls'][0]['id'] == mine['id']


def test_dice_page_and_details_page_offer_rolling(table):
    dm, pl, cid = table
    page = pl.get(f'/campaigns/{cid}/dice').get_data(as_text=True)
    assert 'id="roll-label"' in page and 'name="roll-mode"' in page and 'roll-log-list' in page
    assert 'id="roll-private"' not in page                                # DM-only switch
    assert 'id="roll-private"' in dm.get(f'/campaigns/{cid}/dice').get_data(as_text=True)
    hero = dm.new_character(cid, name='Hero')
    html = pl.get(f'/campaigns/{cid}/characters/{hero}').get_data(as_text=True)
    assert html.count('data-roll-bonus=') >= 6 + 18 + 1 and f'data-roll-character="{hero}"' in html
    assert 'js/character-rolls.js' in html and 'js/dice-feed.js' in html
    ogre = dm.new_character(cid, name='Ogre', is_npc='on')
    hidden = pl.get(f'/campaigns/{cid}/characters/{ogre}').get_data(as_text=True)
    assert 'data-roll-bonus' not in hidden and 'character-rolls.js' not in hidden   # fogged stats are not rollable


def test_realtime_scope_for_dice():
    import realtime
    assert realtime.scope_for('/campaigns/3/api/dice/roll') == 'dice'
    assert realtime.scope_for('/campaigns/3/api/battle/1/damage') == 'battle'


def test_a_database_without_the_dice_table_says_what_to_do(table):
    """A deployment that has not run migration 0018 must not show a mystery error on every page."""
    import database
    dm, pl, cid = table
    def rename(a, b):
        db = database.get_db(); db.execute(f'ALTER TABLE {a} RENAME TO {b}'); db.commit(); db.close()
    rename('dice_rolls', 'dice_rolls_off')
    try:
        r = roll(pl, cid, dice={'20': 1})
        assert r.status_code == 503 and 'migrations' in r.get_json()['error']
        got = pl.get(f'/campaigns/{cid}/api/dice/rolls?after=0')
        assert got.status_code == 200 and got.get_json()['rolls'] == [] and got.get_json()['unavailable'] is True
    finally:
        rename('dice_rolls_off', 'dice_rolls')
    assert roll(pl, cid, dice={'20': 1}).status_code == 200
