"""DL-13: the battle is a durable, campaign-scoped record.

Participants carry their own campaign_id, familiars are self-contained (no hidden character row), and the round and
current turn live on the `battles` table.
"""
import glob
import os

import psycopg
import pytest

from conftest import BACKEND, PG_ADMIN, q

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='new-code tests')

MIGRATIONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'migrations')


def _familiar(u, cid, mid, name='Fang', x=3, y=4):
    r = u.json(f'/campaigns/{cid}/api/maps/{mid}/pins', {'pin_type': 'prop', 'icon_key': 'paw', 'custom_name': name, 'x': x, 'y': y})
    assert r.status_code == 200, r.status_code
    return r.get_json()


def _linked_map(u, cid):
    mid = u.new_map(cid)
    u.json(f'/campaigns/{cid}/api/maps/{mid}/settings', {'linked_to_battle': 1})
    return mid


def test_familiar_is_self_contained_and_leaves_no_hidden_character(make_user):
    u = make_user(); cid = u.new_campaign(); mid = _linked_map(u, cid)
    before = q('SELECT COUNT(*) AS n FROM characters WHERE campaign_id = ?', cid)[0]['n']
    made = _familiar(u, cid, mid)
    pid = made['participant_id']
    assert q('SELECT COUNT(*) AS n FROM characters WHERE campaign_id = ?', cid)[0]['n'] == before
    row = q('SELECT * FROM battle_participants WHERE id = ?', pid)[0]
    assert row['character_id'] is None and row['campaign_id'] == cid
    assert (row['snap_name'], row['snap_max_hp'], row['snap_ac'], row['snap_icon'], row['current_hp']) == ('Fang', 4, 10, 'paw', 4)
    shown = u.battle(cid)[0]
    assert (shown['char_name'], shown['is_temp_familiar'], shown['familiar_icon_key'], shown['char_max_hp'], shown['base_ac']) == ('Fang', 1, 'paw', 4, 10)
    assert 'snap_name' not in shown and 'campaign_id' not in shown                  # storage details stay out of the payload


def test_familiar_edits_and_removal_work_on_the_participant(make_user):
    u = make_user(); cid = u.new_campaign(); mid = _linked_map(u, cid)
    made = _familiar(u, cid, mid); pid = made['participant_id']
    P = f'/campaigns/{cid}/api/maps/{mid}/pins'
    assert u.json(f'/campaigns/{cid}/api/battle/{pid}/max-hp', {'value': 9}).status_code == 200
    assert u.json(f'/campaigns/{cid}/api/battle/{pid}/heal', {'amount': 50}).status_code == 200
    assert u.battle(cid)[0]['current_hp'] == 9                                         # healing stops at the familiar's own max
    assert u.json(f'/campaigns/{cid}/api/battle/{pid}/set_hp', {'value': 2}).status_code == 200
    assert u.battle(cid)[0]['current_hp'] == 2
    assert u.json(f'{P}/{made["id"]}/update', {'custom_name': 'Fangs'}).status_code == 200
    assert q('SELECT snap_name FROM battle_participants WHERE id = ?', pid)[0]['snap_name'] == 'Fangs'
    assert u.json(f'{P}/{made["id"]}/delete').status_code == 200
    assert q('SELECT COUNT(*) AS n FROM battle_participants WHERE id = ?', pid)[0]['n'] == 0


def test_long_rest_and_initiative_cover_familiars_without_a_character(make_user):
    u = make_user(); cid = u.new_campaign(); mid = _linked_map(u, cid)
    pid = _familiar(u, cid, mid)['participant_id']
    u.json(f'/campaigns/{cid}/api/battle/{pid}/set_hp', {'value': 1})
    assert u.json(f'/campaigns/{cid}/api/battle/long-rest').status_code == 200
    assert u.battle(cid)[0]['current_hp'] == 4                                         # snap_max_hp is the ceiling for a long rest
    assert u.json(f'/campaigns/{cid}/api/battle/roll-initiative', {}).status_code == 200
    assert q('SELECT initiative FROM battle_participants WHERE id = ?', pid)[0]['initiative'] is not None


def test_clearing_one_campaigns_battle_leaves_the_other_alone(make_user):
    u = make_user()
    a, b = u.new_campaign(), u.new_campaign()
    ma, mb = _linked_map(u, a), _linked_map(u, b)
    ca = u.add_to_battle(a, u.new_character(a, name='Alder'))
    cb = u.add_to_battle(b, u.new_character(b, name='Birch'))
    _familiar(u, a, ma, 'FamA'); fb = _familiar(u, b, mb, 'FamB')['participant_id']
    assert u.json(f'/campaigns/{a}/api/battle/clear').status_code == 200
    assert u.battle(a) == []
    assert q('SELECT COUNT(*) AS n FROM battle_participants WHERE campaign_id = ?', a)[0]['n'] == 0
    assert sorted(r['char_name'] for r in u.battle(b)) == ['Birch', 'FamB']
    assert {r['id'] for r in u.battle(b)} == {cb, fb}
    assert ca not in {r['id'] for r in u.battle(b)}


def test_sort_order_counts_per_campaign(make_user):
    u = make_user()
    a, b = u.new_campaign(), u.new_campaign()
    for n in range(3):
        u.add_to_battle(a, u.new_character(a, name=f'A{n}'))
    pb = u.add_to_battle(b, u.new_character(b, name='B0'))
    assert q('SELECT sort_order FROM battle_participants WHERE id = ?', pb)[0]['sort_order'] == 0   # not 3: it is b's first


def test_round_and_turn_live_on_the_battles_table(make_user):
    u = make_user(); cid = u.new_campaign()
    p1 = u.add_to_battle(cid, u.new_character(cid, name='One')); p2 = u.add_to_battle(cid, u.new_character(cid, name='Two'))
    u.json(f'/campaigns/{cid}/api/battle/{p1}/initiative', {'value': 15}); u.json(f'/campaigns/{cid}/api/battle/{p2}/initiative', {'value': 10})
    for _ in range(3):
        assert u.json(f'/campaigns/{cid}/api/battle/next-turn').status_code == 200
    b = q('SELECT round, turn_participant_id FROM battles WHERE campaign_id = ?', cid)[0]
    assert (b['round'], b['turn_participant_id']) == (2, p1)
    assert [r['id'] for r in u.battle(cid) if r['is_active']] == [p1]
    assert u.json(f'/campaigns/{cid}/api/battle/clear').status_code == 200
    b = q('SELECT round, turn_participant_id FROM battles WHERE campaign_id = ?', cid)[0]
    assert (b['round'], b['turn_participant_id']) == (1, None)


def test_a_participant_belongs_to_exactly_one_campaign(make_user):
    dm, other = make_user(), make_user()
    a, b = dm.new_campaign(), other.new_campaign()
    pid = dm.add_to_battle(a, dm.new_character(a, name='Mine', max_hp=10))
    # another campaign's DM can neither reach it through his own campaign URL nor through the owner's
    assert other.json(f'/campaigns/{b}/api/battle/{pid}/heal', {'amount': 5}).status_code in (403, 404)
    assert other.json(f'/campaigns/{a}/api/battle/{pid}/heal', {'amount': 5}).status_code in (403, 404)
    assert other.battle(b) == []


def test_deleting_a_campaign_removes_its_battle_rows(make_user):
    u = make_user(); cid = u.new_campaign(); mid = _linked_map(u, cid)
    u.add_to_battle(cid, u.new_character(cid, name='Gone')); fam = _familiar(u, cid, mid)['participant_id']
    r = u.post(f'/campaigns/{cid}/delete', data={'confirm_name': q('SELECT name FROM campaigns WHERE id = ?', cid)[0]['name']})
    assert r.status_code in (200, 302)
    assert q('SELECT COUNT(*) AS n FROM battle_participants WHERE campaign_id = ?', cid)[0]['n'] == 0
    assert q('SELECT COUNT(*) AS n FROM battle_participants WHERE id = ?', fam)[0]['n'] == 0
    assert q('SELECT COUNT(*) AS n FROM battles WHERE campaign_id = ?', cid)[0]['n'] == 0


# ---------------------------------------------------------------------------------------------------------------- migration

def test_migration_0058_carries_live_battles_over():
    """Apply everything up to 0057 on a scratch database with a live battle, then 0058, and check what came out."""
    name = f'oaken_mig58_{os.getpid()}'
    admin = psycopg.connect(PG_ADMIN, autocommit=True)
    admin.execute(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)'); admin.execute(f'CREATE DATABASE {name}')
    try:
        c = psycopg.connect(PG_ADMIN.replace('dbname=postgres', f'dbname={name}'), autocommit=True)
        files = sorted(glob.glob(os.path.join(MIGRATIONS, '*.sql')))
        before = [f for f in files if '0058_battles' not in f]
        for f in before:
            c.execute(open(f).read())
        c.execute("INSERT INTO users (id, username) VALUES (1, 'dm'), (2, 'dm2')")
        c.execute("INSERT INTO campaigns (id, name) VALUES (1, 'One'), (2, 'Two')")
        c.execute("INSERT INTO characters (id, name, campaign_id, max_hp, armor_class, is_npc) VALUES (10, 'Hero', 1, 20, 15, 0), (11, 'Other', 2, 8, 11, 0)")
        c.execute("INSERT INTO characters (id, name, campaign_id, max_hp, armor_class, is_npc, is_temp_familiar, familiar_icon_key, created_by) "
                  "VALUES (12, 'Fang', 1, 4, 10, 0, 1, 'paw', 1)")
        c.execute("INSERT INTO battle_participants (id, character_id, current_hp, sort_order) VALUES (100, 10, 17, 0), (101, 12, 3, 1), (102, 11, 8, 2)")
        c.execute('UPDATE campaigns SET battle_round = 4, battle_turn_id = 101 WHERE id = 1')

        c.execute(open(os.path.join(MIGRATIONS, '0058_battles.sql')).read())

        rows = {r[0]: r[1:] for r in c.execute('SELECT id, campaign_id, character_id, snap_name, snap_max_hp, snap_ac, snap_icon, snap_owner_id, current_hp '
                                               'FROM battle_participants').fetchall()}
        assert rows[100][:2] == (1, 10) and rows[100][8 - 1] == 17                      # PC: linked, hit points kept
        assert rows[102][:2] == (2, 11)                                                 # other campaign keeps its own
        assert rows[101] == (1, None, 'Fang', 4, 10, 'paw', 1, 3)                       # familiar: snapshot, no character, hit points kept
        assert c.execute('SELECT COUNT(*) FROM characters WHERE is_temp_familiar = 1').fetchone()[0] == 0
        assert c.execute('SELECT round, turn_participant_id FROM battles WHERE campaign_id = 1').fetchone() == (4, 101)
        assert c.execute('SELECT round, turn_participant_id FROM battles WHERE campaign_id = 2').fetchone() == (1, None)
        # a participant needs a character or a snapshot
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute('INSERT INTO battle_participants (campaign_id, character_id, current_hp) VALUES (1, NULL, 1)')
        # the transitional trigger: an old-style insert (no campaign_id) still lands in the right campaign
        c.execute('INSERT INTO battle_participants (id, character_id, current_hp, sort_order) VALUES (103, 10, 1, 5)')
        assert c.execute('SELECT campaign_id FROM battle_participants WHERE id = 103').fetchone()[0] == 1
        c.close()
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')
        admin.close()
