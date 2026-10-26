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
          'FROM treasury_entries WHERE campaign_id = ?', cid)[0]
    return {c: int(r[c]) for c in ('cp', 'sp', 'ep', 'gp', 'pp')}


def _add(user, cid, kind, note='x', **coins):
    return user.post(f'/campaigns/{cid}/treasury/add', data={'kind': kind, 'note': note, **{k: str(v) for k, v in coins.items()}})


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
    assert 'Nothing in the purse yet' in dm.get(f'/campaigns/{cid}/treasury').data.decode()
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
    r = dm.post(f'/campaigns/{cid}/treasury/split', data={'characters': [a, b, c]})                          # whole purse, three ways
    assert r.status_code == 302
    assert _purse(cid)['gp'] * 100 + _purse(cid)['sp'] * 10 + _purse(cid)['cp'] == 1                         # 1 cp left over
    note = q('SELECT note FROM treasury_entries WHERE campaign_id = ? ORDER BY id DESC', cid)[0]['note']
    assert 'Aria, Bo, Cy' in note and '3 pp 3 gp 3 sp 3 cp each' in note
    assert dm.post(f'/campaigns/{cid}/treasury/split', data={'characters': [a], 'gp': '5'}).status_code == 400      # purse has 0.01 gp
    _add(dm, cid, 'income', 'More', gp=10)
    assert pl.post(f'/campaigns/{cid}/treasury/split', data={'characters': [a, b], 'gp': '3'}).status_code == 302
    assert 'Aria, Bo' in q('SELECT note FROM treasury_entries WHERE campaign_id = ? ORDER BY id DESC', cid)[0]['note']
    assert dm.post(f'/campaigns/{cid}/treasury/split', data={'gp': '1'}).status_code == 400                 # nobody chosen
    assert dm.post(f'/campaigns/{cid}/treasury/split', data={'characters': ['999999'], 'gp': '1'}).status_code == 400
    assert dm.post(f'/campaigns/{cid}/treasury/split', data={'characters': [a, b, c], 'cp': '2'}).status_code == 400  # too little for three


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
    assert 'added to the party purse: Dragon hoard' in pl.get(f'/campaigns/{cid}/activity').data.decode()
