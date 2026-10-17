"""The battle log: damage, healing, temp HP, conditions, downed / died / revived, rounds and arrivals are recorded,
Players never see an enemy's numbers, it keeps only the latest entries, and clearing the battle starts it afresh."""
import pytest
import app as appmod
from conftest import q


def log(u, cid):
    r = u.get(f'/campaigns/{cid}/api/battle/log')
    assert r.status_code == 200
    return [e['text'] for e in r.get_json()]            # newest first


@pytest.fixture
def table(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, pl)
    return dm, pl, cid


def test_hp_changes_conditions_and_states_are_logged(table):
    dm, pl, cid = table
    pid = dm.add_to_battle(cid, dm.new_character(cid, name='Aria', max_hp=20))
    B = f'/campaigns/{cid}/api/battle/{pid}'
    assert log(dm, cid) == ['Aria joined the battle']
    dm.json(f'{B}/temphp', {'value': 5})
    dm.json(f'{B}/damage', {'amount': 8})
    dm.json(f'{B}/damage', {'amount': 50})
    dm.json(f'{B}/heal', {'amount': 4})
    dm.json(f'{B}/set_hp', {'value': 10})
    dm.json(f'{B}/conditions', {'conditions': ['prone'], 'custom_conditions': ['Hexed']})
    dm.json(f'{B}/conditions', {'conditions': [], 'custom_conditions': ['Hexed']})
    dm.json(f'{B}/die')
    dm.json(f'{B}/revive')
    assert log(dm, cid) == [
        'Aria was revived', 'Aria died',
        'Aria is no longer Prone', 'Aria is now Hexed', 'Aria is now Prone',
        "Aria's HP set to 10 (+6)",
        'Aria is back on their feet', 'Aria healed 4 HP',
        'Aria is down', 'Aria took 17 damage',                      # 50 asked for, only the 17 HP that was left counts
        'Aria took 8 damage (5 absorbed by temp HP)', 'Aria has 5 temp HP', 'Aria joined the battle']


def test_healing_a_dead_creature_logs_a_revival(table):
    dm, pl, cid = table
    pid = dm.add_to_battle(cid, dm.new_character(cid, name='Bram', max_hp=10))
    B = f'/campaigns/{cid}/api/battle/{pid}'
    dm.json(f'{B}/damage', {'amount': 10}); dm.json(f'{B}/die'); dm.json(f'{B}/heal', {'amount': 3})
    texts = log(dm, cid)
    assert texts[:2] == ['Bram healed 3 HP', 'Bram was revived'] or texts[:2] == ['Bram was revived', 'Bram healed 3 HP']
    assert texts.count('Bram died') == 1


def test_players_see_enemy_events_without_numbers(table):
    dm, pl, cid = table
    goblin = dm.add_to_battle(cid, dm.new_character(cid, name='Goblin', max_hp=12, is_npc='on'))
    hero = dm.add_to_battle(cid, dm.new_character(cid, name='Hero', max_hp=30))
    dm.json(f'/campaigns/{cid}/api/battle/{goblin}/damage', {'amount': 5})
    dm.json(f'/campaigns/{cid}/api/battle/{goblin}/temphp', {'value': 7})
    dm.json(f'/campaigns/{cid}/api/battle/{goblin}/heal', {'amount': 2})
    dm.json(f'/campaigns/{cid}/api/battle/{goblin}/conditions', {'conditions': ['poisoned'], 'custom_conditions': []})
    dm.json(f'/campaigns/{cid}/api/battle/{hero}/damage', {'amount': 4})
    seen = log(pl, cid)
    assert 'Goblin was hurt' in seen and 'Goblin was healed' in seen and 'Goblin is now Poisoned' in seen
    assert 'Hero took 4 damage' in seen                                   # a party member's numbers are open
    assert not any('Goblin' in t and any(ch.isdigit() for ch in t) for t in seen), seen
    assert not any('temp HP' in t and 'Goblin' in t for t in seen)        # an enemy's temp HP is hidden entirely
    assert 'Goblin took 5 damage' in log(dm, cid) and 'Goblin has 7 temp HP' in log(dm, cid)


def test_rounds_initiative_and_leaving_are_logged_and_only_the_latest_are_kept(table):
    dm, pl, cid = table
    a = dm.add_to_battle(cid, dm.new_character(cid, name='A', max_hp=10))
    b = dm.add_to_battle(cid, dm.new_character(cid, name='B', max_hp=10))
    dm.json(f'/campaigns/{cid}/api/battle/roll-initiative')
    for _ in range(3):                         # start, then B's turn, then round 2
        dm.json(f'/campaigns/{cid}/api/battle/next-turn')
    texts = log(dm, cid)
    assert 'Initiative rolled for everyone' in texts and 'Combat started: round 1' in texts and 'Round 2 begins' in texts
    dm.json(f'/campaigns/{cid}/api/battle/{b}/remove')
    assert log(dm, cid)[0] == 'B left the battle'
    for i in range(appmod.BATTLE_LOG_KEEP + 20):
        dm.json(f'/campaigns/{cid}/api/battle/{a}/set_hp', {'value': 5 + i % 2})
    assert len(log(dm, cid)) == appmod.BATTLE_LOG_KEEP
    assert q('SELECT COUNT(*) AS n FROM battle_log WHERE campaign_id = ?', cid)[0]['n'] == appmod.BATTLE_LOG_KEEP


def test_clearing_the_battle_starts_a_fresh_log_and_other_campaigns_are_untouched(table):
    dm, pl, cid = table
    other = dm.new_campaign()
    dm.add_to_battle(other, dm.new_character(other, name='Elsewhere', max_hp=5))
    pid = dm.add_to_battle(cid, dm.new_character(cid, name='Aria', max_hp=20))
    dm.json(f'/campaigns/{cid}/api/battle/{pid}/damage', {'amount': 3})
    dm.json(f'/campaigns/{cid}/api/battle/clear')
    assert log(dm, cid) == ['Battle cleared']
    assert log(dm, other) == ['Elsewhere joined the battle']


def test_a_participant_from_another_campaign_cannot_be_touched(table):
    dm, pl, cid = table
    other = dm.new_campaign()
    foreign = dm.add_to_battle(other, dm.new_character(other, name='Elsewhere', max_hp=9))
    for action, body in (('damage', {'amount': 3}), ('heal', {'amount': 3}), ('temphp', {'value': 3}), ('set_hp', {'value': 1}),
                         ('die', {}), ('revive', {}), ('conditions', {'conditions': ['prone']})):
        assert dm.json(f'/campaigns/{cid}/api/battle/{foreign}/{action}', body).status_code == 404, action
    assert next(r for r in dm.battle(other) if r['id'] == foreign)['current_hp'] == 9
    assert log(dm, cid) == []
