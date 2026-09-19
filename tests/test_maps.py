import io, re
import pytest
from conftest import q, png_bytes


def _drawing(cx=10.5, cy=20.25, **kw):
    d = {'kind': 'rect', 'data': {'pts': [1, 2]}, 'cx': cx, 'cy': cy, 'w': 30.123456789, 'h': 40, 'color': '#123456'}
    d.update(kw); return d


def test_blank_map_create_settings_state_delete(camp):
    u, cid = camp
    mid = u.new_map(cid, 'Dungeon', 500, 350)
    row = q('SELECT name, image_width, image_height FROM maps WHERE id = ?', mid)[0]
    assert row['name'] == 'Dungeon' and (row['image_width'], row['image_height']) == (500, 350)
    assert u.get(f'/campaigns/{cid}/maps/{mid}').status_code == 200
    assert u.get(f'/campaigns/{cid}/maps?browse=1').status_code == 200
    r = u.json(f'/campaigns/{cid}/api/maps/{mid}/settings', {'grid_size': 64, 'grid_visible': 1, 'grid_offset_x': 3.5, 'grid_offset_y': 7.25})
    assert r.status_code == 200
    state = u.get(f'/campaigns/{cid}/api/maps/{mid}/state')
    assert state.status_code == 200
    r = u.post(f'/campaigns/{cid}/maps/{mid}/delete')
    assert r.status_code == 302
    assert u.get(r.headers['Location']).status_code == 200            # maps list, not a 404
    assert q('SELECT COUNT(*) AS n FROM maps WHERE id = ?', mid)[0]['n'] == 0


def test_map_from_uploaded_image_and_file_served(camp):
    u, cid = camp
    r = u.post(f'/campaigns/{cid}/maps/new', data={'name': 'Img', 'image': (io.BytesIO(png_bytes(800, 600)), 'map.png')},
               content_type='multipart/form-data')
    assert r.status_code == 302
    mid = int(re.search(r'/maps/(\d+)', r.headers['Location']).group(1))
    row = q('SELECT image_path, image_width, image_height FROM maps WHERE id = ?', mid)[0]
    assert (row['image_width'], row['image_height']) == (800, 600)
    img = u.get('/uploads/' + row['image_path'])
    assert img.status_code == 200 and img.data[:4] == b'\x89PNG'


def test_drawings_crud_and_float_precision(camp):
    u, cid = camp
    mid = u.new_map(cid)
    A = f'/campaigns/{cid}/api/maps/{mid}/drawings'
    did = u.json(A, _drawing()).get_json()['id']
    rows = u.get(A).get_json()
    assert len(rows) == 1 and rows[0]['data'] == {'pts': [1, 2]}
    assert rows[0]['w'] == pytest.approx(30.123456789, abs=1e-9)   # double precision, not float4
    assert u.json(f'{A}/{did}/update', {'cx': 99.5, 'locked': 1}).status_code == 200
    r0 = u.get(A).get_json()[0]
    assert r0['cx'] == 99.5 and r0['locked'] == 1
    assert u.json(f'{A}/{did}/delete').status_code == 200 and u.get(A).get_json() == []
    u.json(A, _drawing()); u.json(A, _drawing())
    assert u.json(f'{A}/clear').status_code == 200 and u.get(A).get_json() == []


def test_pins_and_battle_link(camp):
    u, cid = camp
    mid = u.new_map(cid)
    ch = u.new_character(cid, name='Rogue'); pid = u.add_to_battle(cid, ch)
    P = f'/campaigns/{cid}/api/maps/{mid}/pins'
    prop = u.json(P, {'pin_type': 'prop', 'icon_key': 'skull', 'x': 5, 'y': 6}).get_json()['id']
    assert u.json(f'{P}/{prop}/update', {'x': 50, 'y': 60}).status_code == 200
    pins = u.get(P).get_json()
    assert any(p['id'] == prop and p['x'] == 50 for p in pins)
    # link the map to the battle -> a character pin appears for each participant
    u.json(f'/campaigns/{cid}/api/maps/{mid}/settings', {'linked_to_battle': 1})
    pins = u.get(P).get_json()
    assert any(p.get('participant_id') == pid for p in pins)
    assert u.json(f'{P}/{prop}/delete').status_code == 200


def test_only_one_map_linked_per_campaign(camp):
    u, cid = camp
    m1, m2 = u.new_map(cid, 'A'), u.new_map(cid, 'B')
    u.json(f'/campaigns/{cid}/api/maps/{m1}/settings', {'linked_to_battle': 1})
    u.json(f'/campaigns/{cid}/api/maps/{m2}/settings', {'linked_to_battle': 1})
    linked = q('SELECT id FROM maps WHERE campaign_id = ? AND linked_to_battle = 1', cid)
    assert [r['id'] for r in linked] == [m2]


def test_familiar_prop_pin_lifecycle_when_map_is_linked(camp):
    u, cid = camp
    mid = u.new_map(cid)
    u.json(f'/campaigns/{cid}/api/maps/{mid}/settings', {'linked_to_battle': 1})
    P = f'/campaigns/{cid}/api/maps/{mid}/pins'
    made = u.json(P, {'pin_type': 'prop', 'icon_key': 'wolf', 'custom_name': 'Fang', 'x': 3, 'y': 4}).get_json()
    assert made['participant_id']
    rows = u.battle(cid)
    assert [r['char_name'] for r in rows] == ['Fang'] and rows[0]['is_temp_familiar'] == 1
    assert u.json(f'/campaigns/{cid}/api/battle/{made["participant_id"]}/max-hp', {'value': 12}).status_code == 200
    assert u.battle(cid)[0]['char_max_hp'] == 12
    assert 'Fang' not in u.get(f'/campaigns/{cid}/characters').data.decode()       # never shown in the roster
    assert u.json(f'{P}/{made["id"]}/delete').status_code == 200
    assert u.battle(cid) == []                                                   # familiar + participant cleaned up


def test_player_map_lock(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.post(f'/campaigns/{cid}/members/add', data={'username': pl.name, 'status': 'player'})
    mid = dm.new_map(cid)
    D = f'/campaigns/{cid}/api/maps/{mid}/drawings'
    assert pl.json(D, {'kind': 'rect', 'data': {}, 'w': 1, 'h': 1}).status_code == 200            # unlocked: players may draw
    assert pl.json(f'/campaigns/{cid}/api/maps/{mid}/settings', {'locked_for_players': 1}).status_code == 403   # DM-only
    assert dm.json(f'/campaigns/{cid}/api/maps/{mid}/settings', {'locked_for_players': 1}).status_code == 200
    assert pl.json(D, {'kind': 'rect', 'data': {}, 'w': 1, 'h': 1}).status_code == 403            # locked
    assert pl.get(D).status_code == 200                                                            # but can still look
