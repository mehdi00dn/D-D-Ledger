"""DL-148: the party treasury ledger - the purse is the sum of its entries; spending makes change; splitting is even."""
import pytest
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _setup(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    return dm, pl, cid


def _purse(cid):
    r = q('SELECT COALESCE(SUM(cp),0) cp, COALESCE(SUM(sp),0) sp, COALESCE(SUM(ep),0) ep, COALESCE(SUM(gp),0) gp, COALESCE(SUM(pp),0) pp '
          'FROM treasury_entries WHERE campaign_id = ? AND character_id IS NULL', cid)[0]
    return {c: int(r[c]) for c in ('cp', 'sp', 'ep', 'gp', 'pp')}


def _char_purse(char_id):
    r = q('SELECT COALESCE(SUM(cp),0) cp, COALESCE(SUM(sp),0) sp, COALESCE(SUM(ep),0) ep, COALESCE(SUM(gp),0) gp, COALESCE(SUM(pp),0) pp '
          'FROM treasury_entries WHERE character_id = ?', char_id)[0]
    return {c: int(r[c]) for c in ('cp', 'sp', 'ep', 'gp', 'pp')}


def _add(user, cid, kind, note='x', **coins):
    return user.post(f'/campaigns/{cid}/treasury/add', data={'kind': kind, 'note': note, **{k: str(v) for k, v in coins.items()}})


def _add_for(user, cid, holder, kind, note='x', **coins):
    return user.post(f'/campaigns/{cid}/treasury/add', data={'kind': kind, 'note': note, 'character_id': str(holder), **{k: str(v) for k, v in coins.items()}})


def test_settle_payment_pays_named_coins_then_makes_change():
    import app as appmod
    purse = {'cp': 0, 'sp': 0, 'ep': 0, 'gp': 10, 'pp': 1}
    assert appmod.settle_payment(purse, {'gp': 3}) == {'cp': 0, 'sp': 0, 'ep': 0, 'gp': -3, 'pp': 0}        # exact coins first
    d = appmod.settle_payment({'cp': 0, 'sp': 0, 'ep': 0, 'gp': 0, 'pp': 1}, {'gp': 1})                       # only a platinum: change in gold
    assert d == {'cp': 0, 'sp': 0, 'ep': 0, 'gp': 9, 'pp': -1}
    d = appmod.settle_payment({'cp': 0, 'sp': 0, 'ep': 0, 'gp': 1, 'pp': 0}, {'cp': 5})                       # a gold for 5 cp: 9 sp 5 cp back
    assert d == {'cp': 5, 'sp': 9, 'ep': 0, 'gp': -1, 'pp': 0}
    assert appmod.settle_payment({'cp': 99, 'sp': 0, 'ep': 0, 'gp': 0, 'pp': 0}, {'gp': 1}) is None          # 99 cp is not a gold
    for purse, cost in [(purse, {'gp': 4, 'sp': 7}), ({'cp': 3, 'sp': 4, 'ep': 1, 'gp': 2, 'pp': 1}, {'pp': 1, 'cp': 20})]:
        d = appmod.settle_payment(purse, cost)
        assert appmod.coins_value(d) == -appmod.coins_value(cost)                                             # value is conserved
        assert all(purse[c] + d[c] >= 0 for c in purse)                                                       # no coin goes below zero


def test_formatting():
    import app as appmod
    assert appmod.format_coins({'gp': 3, 'sp': 5}) == '3 gp 5 sp' and appmod.format_coins({}) == '0 gp'
    assert appmod.format_coins({'gp': 3, 'cp': -2}, signed=True) == '+3 gp -2 cp'
    assert [appmod.format_gold(x) for x in (0, 100, 1250, 5, -150)] == ['0 gp', '1 gp', '12.5 gp', '0.05 gp', '-1.5 gp']


def test_members_add_and_spend_and_the_purse_cannot_go_negative(make_user):
    dm, pl, cid = _setup(make_user)
    assert 'Nothing here yet' in dm.get(f'/campaigns/{cid}/treasury').data.decode()
    assert _add(pl, cid, 'income', 'Dragon hoard', gp=50, sp=3).status_code == 302                          # a Player may add too
    assert _purse(cid)['gp'] == 50
    page = pl.get(f'/campaigns/{cid}/treasury').data.decode()
    assert 'Dragon hoard' in page and '+50 gp +3 sp' in page and '50.3 gp' in page
    assert _add(dm, cid, 'expense', 'Inn', gp=2).status_code == 302 and _purse(cid)['gp'] == 48
    r = _add(dm, cid, 'expense', 'Castle', pp=100)
    assert r.status_code == 400 and 'does not have enough' in r.data.decode()
    assert _purse(cid)['gp'] == 48 and len(q('SELECT id FROM treasury_entries WHERE campaign_id = ?', cid)) == 2
    assert _add(dm, cid, 'expense', 'Bad', gp='abc').status_code == 400
    assert _add(dm, cid, 'expense', 'Nothing').status_code == 400
    assert _add(dm, cid, 'loan', 'Odd', gp=1).status_code == 400


def test_spending_a_small_amount_from_big_coins_gives_change(make_user):
    dm, _, cid = _setup(make_user)
    _add(dm, cid, 'income', 'Gem', pp=1)
    _add(dm, cid, 'expense', 'Rope', gp=1)
    assert _purse(cid) == {'cp': 0, 'sp': 0, 'ep': 0, 'gp': 9, 'pp': 0}


def test_split_evenly_between_chosen_characters(make_user):
    dm, pl, cid = _setup(make_user)
    a, b, c = (dm.new_character(cid, n) for n in ('Aria', 'Bo', 'Cy'))
    _add(dm, cid, 'income', 'Loot', gp=100)
    r = dm.post(f'/campaigns/{cid}/treasury/split', data={'to': [a, b, c]})                                  # whole party purse, three ways
    assert r.status_code == 302
    left = _purse(cid)
    assert left['gp'] * 100 + left['sp'] * 10 + left['cp'] == 1                                              # 1 cp left over in the party purse
    for ch in (a, b, c):
        assert _char_purse(ch) == {'cp': 3, 'sp': 3, 'ep': 0, 'gp': 3, 'pp': 3}                              # 3333 cp each
    assert '3 pp 3 gp 3 sp 3 cp each' in q('SELECT note FROM treasury_entries WHERE campaign_id = ? AND character_id IS NULL ORDER BY id DESC', cid)[0]['note']
    assert dm.post(f'/campaigns/{cid}/treasury/split', data={'to': [a], 'gp': '5'}).status_code == 400      # purse has 0.01 gp
    _add(dm, cid, 'income', 'More', gp=10)
    assert pl.post(f'/campaigns/{cid}/treasury/split', data={'to': [a, b], 'gp': '3'}).status_code == 302   # any member may share the party purse
    assert dm.post(f'/campaigns/{cid}/treasury/split', data={'gp': '1'}).status_code == 400                 # nobody chosen
    assert dm.post(f'/campaigns/{cid}/treasury/split', data={'to': ['999999'], 'gp': '1'}).status_code == 400
    assert dm.post(f'/campaigns/{cid}/treasury/split', data={'to': [a, b, c], 'cp': '2'}).status_code == 400  # too little for three


def test_character_purses_hand_over_and_who_may_use_them(make_user):
    dm, pl, cid = _setup(make_user)
    mine = pl.new_character(cid, 'Pia'); other = dm.new_character(cid, 'Dax')
    assert _add_for(pl, cid, mine, 'income', 'Found it', gp=10).status_code == 302
    assert _char_purse(mine)['gp'] == 10 and _purse(cid)['gp'] == 0                                          # the character's coins, not the party's
    assert _add_for(pl, cid, other, 'income', 'Not mine', gp=1).status_code == 403                           # someone else's purse
    assert _add_for(pl, cid, mine, 'expense', 'Potion', gp=4).status_code == 302 and _char_purse(mine)['gp'] == 6
    assert _add_for(pl, cid, mine, 'expense', 'Castle', gp=99).status_code == 400                            # cannot go below zero
    assert _add_for(dm, cid, mine, 'income', 'DM may', gp=1).status_code == 302                              # the DM can use any purse
    assert _add_for(dm, cid, 'abc', 'income', 'Odd', gp=1).status_code == 400
    # hand over: Pia gives Dax 2 gp; Dax's purse gains it, hers loses it, in one tied-together share
    assert pl.post(f'/campaigns/{cid}/treasury/split', data={'source': mine, 'to': [other], 'gp': '2'}).status_code == 302
    assert _char_purse(other)['gp'] == 2 and _char_purse(mine)['gp'] == 5
    assert pl.post(f'/campaigns/{cid}/treasury/split', data={'source': other, 'to': [mine], 'gp': '1'}).status_code == 403   # not hers to give
    assert pl.post(f'/campaigns/{cid}/treasury/split', data={'source': mine, 'to': [mine], 'gp': '1'}).status_code == 400     # not to itself
    # back into the party purse
    assert pl.post(f'/campaigns/{cid}/treasury/split', data={'source': mine, 'to': ['party'], 'whole': '1'}).status_code == 302
    assert _char_purse(mine)['gp'] == 0 and _purse(cid)['gp'] == 5


def test_removing_a_share_removes_every_entry_of_it(make_user):
    dm, pl, cid = _setup(make_user)
    a = dm.new_character(cid, 'Aria'); b = dm.new_character(cid, 'Bo')
    _add(dm, cid, 'income', 'Loot', gp=10)
    dm.post(f'/campaigns/{cid}/treasury/split', data={'to': [a, b], 'gp': '10'})
    assert len(q('SELECT id FROM treasury_entries WHERE campaign_id = ? AND batch IS NOT NULL', cid)) == 3
    one = q('SELECT id FROM treasury_entries WHERE campaign_id = ? AND batch IS NOT NULL ORDER BY id', cid)[0]['id']
    _add_for(dm, cid, a, 'expense', 'Spent it', gp=4)
    assert dm.post(f'/campaigns/{cid}/treasury/{one}/delete').status_code == 409                             # Aria already spent some
    assert len(q('SELECT id FROM treasury_entries WHERE campaign_id = ?', cid)) == 5
    q_spend = q('SELECT id FROM treasury_entries WHERE campaign_id = ? ORDER BY id DESC', cid)[0]['id']
    dm.post(f'/campaigns/{cid}/treasury/{q_spend}/delete')
    assert dm.post(f'/campaigns/{cid}/treasury/{one}/delete').status_code == 302
    assert len(q('SELECT id FROM treasury_entries WHERE campaign_id = ?', cid)) == 1 and _purse(cid)['gp'] == 10


def test_a_deleted_character_returns_its_coins_to_the_party_purse(make_user):
    dm, _, cid = _setup(make_user)
    a = dm.new_character(cid, 'Aria')
    _add_for(dm, cid, a, 'income', 'Hers', gp=7)
    dm.post(f'/campaigns/{cid}/characters/{a}/delete')
    assert _purse(cid)['gp'] == 7


def test_the_purse_shows_on_the_character_profile_and_the_treasury_page(make_user):
    dm, pl, cid = _setup(make_user)
    a = pl.new_character(cid, 'Aria')
    _add_for(pl, cid, a, 'income', 'Ruby sold', gp=12, sp=5)
    page = pl.get(f'/campaigns/{cid}/characters/{a}').data.decode()
    assert 'aria-label="Purse"' in page and '12.5 gp' in page and 'Ruby sold' in page and f'/treasury?who={a}' in page
    treasury = dm.get(f'/campaigns/{cid}/treasury').data.decode()
    assert 'Character purses' in treasury and 'Aria' in treasury and '12.5 gp' in treasury
    assert 'Ruby sold' in dm.get(f'/campaigns/{cid}/treasury?who={a}').data.decode()
    assert 'Ruby sold' not in dm.get(f'/campaigns/{cid}/treasury?who=party').data.decode()
    npc = dm.new_character(cid, 'Goblin', is_npc='on')
    assert 'aria-label="Purse"' not in dm.get(f'/campaigns/{cid}/characters/{npc}').data.decode()             # NPCs carry no purse


def test_who_can_remove_an_entry_and_removal_cannot_break_the_purse(make_user):
    dm, pl, cid = _setup(make_user)
    _add(pl, cid, 'income', 'Player coins', gp=10)
    _add(dm, cid, 'income', 'DM coins', gp=5)
    mine, dms = [r['id'] for r in q('SELECT id FROM treasury_entries WHERE campaign_id = ? ORDER BY id', cid)]
    assert pl.post(f'/campaigns/{cid}/treasury/{dms}/delete').status_code == 403                             # not theirs
    _add(dm, cid, 'expense', 'Spend', gp=12)
    assert dm.post(f'/campaigns/{cid}/treasury/{mine}/delete').status_code == 409                            # coins were already spent
    assert len(q('SELECT id FROM treasury_entries WHERE campaign_id = ?', cid)) == 3
    spend = q('SELECT id FROM treasury_entries WHERE campaign_id = ? ORDER BY id DESC', cid)[0]['id']
    assert dm.post(f'/campaigns/{cid}/treasury/{spend}/delete').status_code == 302
    assert pl.post(f'/campaigns/{cid}/treasury/{mine}/delete').status_code == 302 and _purse(cid)['gp'] == 5


def test_an_entry_of_another_campaign_cannot_be_touched_and_the_note_is_escaped(make_user):
    dm, _, cid = _setup(make_user)
    other = make_user(); ocid = other.new_campaign()
    _add(other, ocid, 'income', 'Theirs', gp=1)
    eid = q('SELECT id FROM treasury_entries WHERE campaign_id = ?', ocid)[0]['id']
    assert dm.post(f'/campaigns/{cid}/treasury/{eid}/delete').status_code == 404
    assert other.get(f'/campaigns/{cid}/treasury').status_code in (403, 404)
    _add(dm, cid, 'income', '<script>alert(1)</script>', gp=1)
    assert '<script>alert(1)' not in dm.get(f'/campaigns/{cid}/treasury').data.decode()


def test_entries_reach_the_activity_feed(make_user):
    dm, pl, cid = _setup(make_user)
    _add(dm, cid, 'income', 'Dragon hoard', gp=5)
    assert 'added to the purse: Dragon hoard' in pl.get(f'/campaigns/{cid}/activity').data.decode()
