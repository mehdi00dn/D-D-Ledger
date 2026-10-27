"""DL-147: the encounter builder - 2024 XP budget maths, DM-only saved lists, and running a list in the battle."""
import copy

import pytest

import encounters
import srd
from conftest import q, BACKEND
from test_reference import DETAILS, GOBLIN, LISTS

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')

BUGBEAR = dict(GOBLIN, index='bugbear-warrior', name='Bugbear Warrior', challenge_rating=3, xp=700, hit_points=65, hit_points_roll='10d8+20')


@pytest.fixture(autouse=True)
def fake_api(monkeypatch):
    calls = []
    lists = copy.deepcopy(LISTS)
    lists['/monsters']['results'].append({'index': 'bugbear-warrior', 'name': 'Bugbear Warrior'})
    details = dict(DETAILS, **{'/monsters/bugbear-warrior': BUGBEAR})

    def fake(path):
        calls.append(path)
        if path in lists:
            return lists[path]
        if path in details:
            return details[path]
        raise srd.SrdError('not found')
    monkeypatch.setattr(srd, '_get_json', fake)
    return calls


def _setup(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    return dm, pl, cid


def _new(dm, cid, name='Ambush'):
    dm.post(f'/campaigns/{cid}/encounters/new', data={'name': name})
    return q('SELECT id FROM encounters WHERE campaign_id = ? ORDER BY id DESC', cid)[0]['id']


def _save(user, cid, eid, monsters, name='Ambush'):
    return user.json(f'/campaigns/{cid}/encounters/{eid}/save', {'name': name, 'monsters': monsters})


def test_the_budget_follows_the_2024_table():
    assert encounters.XP_BUDGET[1] == (50, 75, 100) and encounters.XP_BUDGET[20] == (6400, 13200, 22000)
    assert encounters.budget([3, 3, 3, 3]) == {'low': 600, 'moderate': 900, 'high': 1600}
    assert encounters.budget([1, 5]) == {'low': 550, 'moderate': 825, 'high': 1200}
    assert encounters.budget([99, 0])['high'] == 22000 + 100                                  # levels are kept inside 1-20
    assert sorted(encounters.XP_BUDGET) == list(range(1, 21))
    assert all(a < b < c for a, b, c in encounters.XP_BUDGET.values())
    assert all(encounters.XP_BUDGET[n][i] < encounters.XP_BUDGET[n + 1][i] for n in range(1, 20) for i in range(3))


def test_difficulty_is_the_lowest_budget_that_holds_the_total():
    b = encounters.budget([3, 3, 3, 3])
    assert [encounters.difficulty(x, b) for x in (0, 1, 600, 601, 900, 901, 1600, 1601)] == ['none', 'low', 'low', 'moderate', 'moderate', 'high', 'high', 'over']
    assert encounters.total_xp([{'xp': 50, 'count': 3}, {'xp': 700, 'count': 1}]) == 850
    assert encounters.difficulty(50, encounters.budget([])) == 'over'                          # nobody to fight it


def test_a_monster_detail_carries_its_challenge_and_xp():
    d = srd.detail('monsters', 'monsters/goblin-warrior')
    assert d['cr'] == '1/4' and d['xp'] == 50


def test_only_the_dm_may_use_encounters(make_user):
    dm, pl, cid = _setup(make_user)
    eid = _new(dm, cid)
    base = f'/campaigns/{cid}/encounters'
    assert pl.get(base).status_code == 403 and pl.get(f'{base}/{eid}').status_code == 403
    assert pl.post(f'{base}/new', data={'name': 'x'}).status_code == 403
    assert _save(pl, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 1}]).status_code == 403
    assert pl.json(f'{base}/{eid}/run', {}).status_code == 403
    assert pl.post(f'{base}/{eid}/delete').status_code == 403
    assert 'href="/campaigns/%d/encounters"' % cid not in pl.get(f'/campaigns/{cid}/home').data.decode()
    assert 'href="/campaigns/%d/encounters"' % cid in dm.get(f'/campaigns/{cid}/home').data.decode()


def test_saving_a_list_copies_name_cr_and_xp_and_reuses_them_later(make_user, fake_api):
    dm, _, cid = _setup(make_user)
    eid = _new(dm, cid)
    r = _save(dm, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 3}, {'ref': 'monsters/bugbear-warrior', 'count': 1}, {'ref': 'monsters/goblin-warrior', 'count': 2}], 'Cave mouth')
    assert r.status_code == 200
    rows = q('SELECT ref, name, cr, xp, count FROM encounter_monsters WHERE encounter_id = ? ORDER BY position', eid)
    assert [(x['ref'], x['name'], x['cr'], x['xp'], x['count']) for x in rows] == [
        ('monsters/goblin-warrior', 'Goblin Warrior', '1/4', 50, 5), ('monsters/bugbear-warrior', 'Bugbear Warrior', '3', 700, 1)]
    assert q('SELECT name FROM encounters WHERE id = ?', eid)[0]['name'] == 'Cave mouth'
    fake_api.clear()
    _save(dm, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 2}])                     # already saved: no lookup, count replaced
    assert fake_api == [] and q('SELECT count FROM encounter_monsters WHERE encounter_id = ?', eid)[0]['count'] == 2
    page = dm.get(f'/campaigns/{cid}/encounters/{eid}').data.decode()
    assert 'Goblin Warrior' in page and 'data-budget=' in page
    assert 'Goblin Warrior' in dm.get(f'/campaigns/{cid}/encounters').data.decode()


def test_a_bad_list_is_refused_and_changes_nothing(make_user):
    dm, _, cid = _setup(make_user)
    eid = _new(dm, cid)
    _save(dm, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 1}])
    for bad in ([{'ref': 'monsters/goblin-warrior', 'count': 0}], [{'ref': 'monsters/goblin-warrior', 'count': 31}],
                [{'ref': 'spells/fireball', 'count': 1}], [{'ref': 'monsters/../x', 'count': 1}], [{'ref': 5, 'count': 1}], [{'count': 1}],
                [{'ref': 'monsters/goblin-warrior', 'count': 'many'}], [{'ref': 'monsters/goblin-warrior', 'count': 1}] * 31, 'nope'):
        assert _save(dm, cid, eid, bad).status_code == 400, bad
    assert _save(dm, cid, eid, [{'ref': 'monsters/not-real', 'count': 1}]).status_code == 404
    assert len(q('SELECT id FROM encounter_monsters WHERE encounter_id = ?', eid)) == 1
    assert dm.post(f'/campaigns/{cid}/encounters/{eid}/save', data='x', content_type='text/plain').status_code == 400


def test_running_an_encounter_puts_every_monster_in_the_battle(make_user):
    dm, _, cid = _setup(make_user)
    eid = _new(dm, cid)
    assert dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {}).status_code == 400            # nothing to run
    _save(dm, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 3}, {'ref': 'monsters/bugbear-warrior', 'count': 1}])
    r = dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {'hp': 'average'})
    assert r.status_code == 200 and r.get_json()['url'] == f'/campaigns/{cid}/battle'
    names = [x['name'] for x in q('SELECT c.name FROM battle_participants bp JOIN characters c ON c.id = bp.character_id '
                                  'WHERE c.campaign_id = ? ORDER BY bp.id', cid)]
    assert names == ['Goblin Warrior'] * 3 + ['Bugbear Warrior']                      # three goblins, one character
    assert all(x['is_npc'] for x in q('SELECT is_npc FROM characters WHERE campaign_id = ?', cid))
    assert dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {'hp': 'rolled'}).status_code == 200      # the list stays and can run again
    assert len(q('SELECT id FROM encounter_monsters WHERE encounter_id = ?', eid)) == 2


def test_an_encounter_with_too_many_creatures_does_not_run(make_user):
    dm, _, cid = _setup(make_user)
    eid = _new(dm, cid)
    _save(dm, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 30}, {'ref': 'monsters/bugbear-warrior', 'count': 11}])
    r = dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {})
    assert r.status_code == 400 and '40' in r.get_json()['error']
    assert q('SELECT id FROM characters WHERE campaign_id = ? AND is_npc = 1', cid) == []


def test_an_encounter_of_another_campaign_cannot_be_touched(make_user):
    dm, _, cid = _setup(make_user)
    other = make_user(); ocid = other.new_campaign()
    oid = _new(other, ocid, 'Theirs')
    base = f'/campaigns/{cid}/encounters/{oid}'
    assert dm.get(base).status_code == 404
    assert _save(dm, cid, oid, []).status_code == 404 and dm.json(f'{base}/run', {}).status_code == 404 and dm.post(f'{base}/delete').status_code == 404
    assert q('SELECT name FROM encounters WHERE id = ?', oid)[0]['name'] == 'Theirs'


def test_delete_removes_the_list_and_names_are_escaped(make_user):
    dm, _, cid = _setup(make_user)
    eid = _new(dm, cid, '<script>alert(1)</script>')
    _save(dm, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 1}], '<script>alert(1)</script>')
    for url in (f'/campaigns/{cid}/encounters', f'/campaigns/{cid}/encounters/{eid}'):
        assert '<script>alert(1)' not in dm.get(url).data.decode()
    assert dm.post(f'/campaigns/{cid}/encounters/{eid}/delete').status_code == 302
    assert q('SELECT id FROM encounters WHERE campaign_id = ?', cid) == [] and q('SELECT id FROM encounter_monsters WHERE encounter_id = ?', eid) == []


def _npc_names(cid):
    return [r['name'] for r in q('SELECT name FROM characters WHERE campaign_id = ? AND is_npc = 1 ORDER BY id', cid)]


def _edit(cid, sql, *args):
    import database
    db = database.get_db()
    db.execute(sql, args)
    db.commit(); db.close()


def _battle_names(dm, cid):
    return [r['display_name'] for r in dm.get(f'/campaigns/{cid}/api/battle').get_json()]


def test_four_goblins_are_one_character_and_it_is_reused(make_user):
    dm, _, cid = _setup(make_user)
    eid = _new(dm, cid)
    _save(dm, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 4}])
    dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {'hp': 'average'})
    assert _npc_names(cid) == ['Goblin Warrior']                                              # one card in the character list
    assert q('SELECT added_from FROM characters WHERE campaign_id = ?', cid)[0]['added_from'] == 'encounter'
    assert _battle_names(dm, cid) == ['Goblin Warrior #1', 'Goblin Warrior #2', 'Goblin Warrior #3', 'Goblin Warrior #4']
    dm.json(f'/campaigns/{cid}/api/battle/clear')
    dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {'hp': 'average'})                      # running it again uses the same one
    dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {'hp': 'average'})                      # even while the first group still fights
    assert _npc_names(cid) == ['Goblin Warrior'] and len(_battle_names(dm, cid)) == 8
    page = dm.get(f'/campaigns/{cid}/characters').data.decode()
    assert 'Added from encounter' in page


def test_an_edited_monster_character_is_left_alone(make_user):
    dm, _, cid = _setup(make_user)
    eid = _new(dm, cid)
    _save(dm, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 1}])
    dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {})
    dm.json(f'/campaigns/{cid}/api/battle/clear')
    first = q('SELECT id FROM characters WHERE campaign_id = ?', cid)[0]['id']
    _edit(cid, 'UPDATE characters SET notes = ? WHERE id = ?', '["my own note about this goblin"]', first)         # the DM changed its notes
    dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {})
    assert _npc_names(cid) == ['Goblin Warrior', 'Goblin Warrior']                            # the edited one is untouched, a fresh one is made
    assert q('SELECT notes FROM characters WHERE id = ?', first)[0]['notes'] == '["my own note about this goblin"]'
    second = max(r['id'] for r in q('SELECT id FROM characters WHERE campaign_id = ?', cid))
    _edit(cid, 'UPDATE characters SET max_hp = max_hp + 5 WHERE id = ?', second)               # stats changed too
    dm.json(f'/campaigns/{cid}/api/battle/clear')
    dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {})
    assert len(_npc_names(cid)) == 3


def test_deleting_an_encounter_can_remove_its_creatures_too(make_user):
    dm, _, cid = _setup(make_user)
    eid = _new(dm, cid)
    _save(dm, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 2}, {'ref': 'monsters/bugbear-warrior', 'count': 1}])
    dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {})
    dm.json(f'/campaigns/{cid}/api/battle/clear')
    assert dm.post(f'/campaigns/{cid}/encounters/{eid}/delete', data={'remove_characters': '0'}).status_code == 302
    assert sorted(_npc_names(cid)) == ['Bugbear Warrior', 'Goblin Warrior']                   # "encounter only": the creatures stay
    eid = _new(dm, cid, 'Again')
    _save(dm, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 2}, {'ref': 'monsters/bugbear-warrior', 'count': 1}], 'Again')
    goblin = q("SELECT id FROM characters WHERE campaign_id = ? AND name = 'Goblin Warrior'", cid)[0]['id']
    _edit(cid, 'UPDATE characters SET armor_class = armor_class + 1 WHERE id = ?', goblin)       # an edited one is never removed
    r = dm.post(f'/campaigns/{cid}/encounters/{eid}/delete', data={'remove_characters': '1'})
    assert r.status_code == 302 and 'removed=1' in r.headers['Location']
    assert _npc_names(cid) == ['Goblin Warrior']
    assert 'also removed from the character list' in dm.get(r.headers['Location']).data.decode()
    eid = _new(dm, cid, 'Busy')
    _save(dm, cid, eid, [{'ref': 'monsters/bugbear-warrior', 'count': 1}], 'Busy')
    dm.json(f'/campaigns/{cid}/encounters/{eid}/run', {})                                    # fighting right now: kept
    dm.post(f'/campaigns/{cid}/encounters/{eid}/delete', data={'remove_characters': '1'})
    assert sorted(_npc_names(cid)) == ['Bugbear Warrior', 'Goblin Warrior']


def test_rename_from_the_list_and_run_button_on_it(make_user):
    dm, pl, cid = _setup(make_user)
    eid = _new(dm, cid)
    _save(dm, cid, eid, [{'ref': 'monsters/goblin-warrior', 'count': 1}])
    assert dm.json(f'/campaigns/{cid}/encounters/{eid}/rename', {'name': '  Cave   mouth '}).get_json()['name'] == 'Cave mouth'
    assert q('SELECT name FROM encounters WHERE id = ?', eid)[0]['name'] == 'Cave mouth'
    assert dm.json(f'/campaigns/{cid}/encounters/{eid}/rename', {'name': '   '}).status_code == 400
    assert dm.json(f'/campaigns/{cid}/encounters/{eid}/rename', {'name': 'x' * 200}).get_json()['name'] == 'x' * 80
    page = dm.get(f'/campaigns/{cid}/encounters').data.decode()
    assert 'data-enc-run' in page and f'/encounters/{eid}/run' in page and f'/encounters/{eid}/rename' in page
    assert pl.json(f'/campaigns/{cid}/encounters/{eid}/rename', {'name': 'Mine'}).status_code in (403, 404)
