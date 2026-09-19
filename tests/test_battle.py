import concurrent.futures as cf
import pytest
from conftest import q, BACKEND


def _hp(u, cid, pid):
    return next(r for r in u.battle(cid) if r['id'] == pid)


def test_battle_flow_damage_heal_temp_initiative_dice_revive(camp):
    u, cid = camp
    ch = u.new_character(cid, name='Knight', max_hp=30, armor_class=15)
    pid = u.add_to_battle(cid, ch)
    B = f'/campaigns/{cid}/api/battle/{pid}'
    assert _hp(u, cid, pid)['current_hp'] == 30

    u.json(f'{B}/temphp', {'value': 5})
    u.json(f'{B}/damage', {'amount': 8})           # 5 absorbed by temp, 3 hits real HP
    row = _hp(u, cid, pid)
    assert (row['current_hp'], row['temp_hp']) == (27, 0)
    u.json(f'{B}/damage', {'amount': 100})
    assert _hp(u, cid, pid)['current_hp'] == 0    # floors at zero
    u.json(f'{B}/heal', {'amount': 5})
    assert _hp(u, cid, pid)['current_hp'] == 5
    u.json(f'{B}/heal', {'amount': 999})
    assert _hp(u, cid, pid)['current_hp'] == 30   # capped at max HP
    u.json(f'{B}/set_hp', {'value': 12}); assert _hp(u, cid, pid)['current_hp'] == 12
    u.json(f'{B}/initiative', {'value': 17}); assert _hp(u, cid, pid)['initiative'] == 17
    u.json(f'{B}/ac', {'value': 19}); assert _hp(u, cid, pid)['effective_ac'] == 19
    u.json(f'{B}/die'); assert _hp(u, cid, pid)['is_dead'] == 1
    u.json(f'{B}/revive'); assert _hp(u, cid, pid)['is_dead'] == 0
    u.json(f'{B}/damage', {'amount': 12}); u.json(f'{B}/die'); u.json(f'{B}/heal', {'amount': 3})
    row = _hp(u, cid, pid)
    assert row['current_hp'] == 3 and row['is_dead'] == 0      # healing a dead-flagged creature revives it
    assert u.json(f'{B}/remove').status_code == 200
    assert u.battle(cid) == []


def test_unknown_participant_is_404(camp):
    u, cid = camp
    assert u.json(f'/campaigns/{cid}/api/battle/999999/damage', {'amount': 1}).status_code == 404
    assert u.json(f'/campaigns/{cid}/api/battle/999999/heal', {'amount': 1}).status_code == 404


def test_add_group_and_clear_and_duplicate_names(camp):
    u, cid = camp
    gid = u.new_group(cid)
    for n in ('Goblin', 'Goblin', 'Orc'):
        u.new_character(cid, name=n, group_id=gid, is_npc='on')
    r = u.json(f'/campaigns/{cid}/api/battle/add-group', {'group_id': gid})
    rows = r.get_json()
    assert len(rows) == 3
    assert sorted(x['display_name'] for x in rows) == ['Goblin #1', 'Goblin #2', 'Orc']
    assert u.json(f'/campaigns/{cid}/api/battle/clear').get_json() == []
    assert u.battle(cid) == []


def test_player_sees_redacted_enemy_stats(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign()
    dm.post(f'/campaigns/{cid}/members/add', data={'username': pl.name, 'status': 'player'})
    pc = dm.new_character(cid, name='Hero', max_hp=20)
    npc = dm.new_character(cid, name='Ogre', max_hp=59, is_npc='on')
    dm.add_to_battle(cid, pc); dm.add_to_battle(cid, npc)
    rows = {r['char_name']: r for r in pl.battle(cid)}
    assert rows['Hero']['current_hp'] == 20
    assert rows['Ogre']['current_hp'] is None and rows['Ogre']['hidden_stats'] is True


def test_battle_is_campaign_scoped_on_list(make_user):
    a, b = make_user(), make_user()
    ca, cb = a.new_campaign(), b.new_campaign()
    a.add_to_battle(ca, a.new_character(ca))
    assert b.battle(cb) == []


@pytest.mark.xfail(BACKEND == 'sqlite', reason='legacy read-modify-write loses updates under concurrency; fixed by atomic UPDATE in Phase 1', strict=False)
def test_concurrent_damage_is_atomic(user):
    cid = user.new_campaign()
    ch = user.new_character(cid, name='Tank', max_hp=500)
    pid = user.add_to_battle(cid, ch)
    clients = [user.fresh_client() for _ in range(8)]
    def hit(c):
        codes = []
        for _ in range(6):
            codes.append(c.post(f'/campaigns/{cid}/api/battle/{pid}/damage', json={'amount': 1}).status_code)
        return codes
    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(hit, clients))
    assert all(code == 200 for codes in results for code in codes)
    assert _hp(user, cid, pid)['current_hp'] == 500 - 8 * 6
