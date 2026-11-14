"""DL-183: export a whole campaign as one ZIP, import it as a brand-new campaign."""
import base64
import io
import json
import zipfile

import pytest
from conftest import q, png_bytes
from test_direct_upload import direct_put

SECRET_NOTE = '<b>The baron is the traitor</b>'


def ex(sql, *params):
    import database
    db = database.get_db()
    try:
        db.execute(sql, params)
        db.commit()
    finally:
        db.close()


def mine(u, name=None):
    """The ids of the campaigns this person is a member of (optionally with this name), oldest first."""
    sql = 'SELECT c.id FROM campaigns c JOIN campaign_members cm ON cm.campaign_id = c.id JOIN users x ON x.id = cm.user_id WHERE x.username = ?'
    return [r['id'] for r in q(sql + (' AND c.name = ?' if name else '') + ' ORDER BY c.id', *((u.name, name) if name else (u.name,)))]


def zip_of(manifest, files=None, raw_manifest=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as zf:
        zf.writestr('manifest.json', raw_manifest if raw_manifest is not None else json.dumps(manifest))
        for name, blob in (files or {}).items():
            zf.writestr(name, blob)
    return b.getvalue()


def do_import(u, data, **kw):
    return u.post('/campaigns/import', data={'import_file': (io.BytesIO(data), 'campaign.zip')}, content_type='multipart/form-data', **kw)


def build_source(u):
    """A campaign with something of everything the export is meant to carry.  Returns (campaign id, handles)."""
    cid = u.new_campaign('The Sunken Crown')
    banner = png_bytes(300, 180, (10, 90, 40))
    r = u.post(f'/campaigns/{cid}/edit', data={'name': 'The Sunken Crown', 'description': '["A tale of tides"]', 'setting_preset': '__custom__',
                                                'setting_custom': 'Saltmarch', 'status': 'on_hold', 'access_mode': 'invite_link',
                                                'notes': json.dumps(['Campaign plan', {'t': SECRET_NOTE, 'h': 1}]),
                                                'avatar': (io.BytesIO(banner), 'banner.png')}, content_type='multipart/form-data')
    assert r.status_code == 302
    ex('UPDATE campaigns SET hide_npcs = TRUE WHERE id = ?', cid)

    rebels = u.new_group(cid, 'Rebels', '#aa3322')
    guild = u.new_group(cid, 'Guild', '#2233aa')
    ex('UPDATE groups SET bio = ? WHERE id = ?', 'Sworn against the crown', rebels)
    r = u.post(f'/campaigns/{cid}/characters/new',
               data={'name': 'Ayla', 'level': '4', 'max_hp': '33', 'armor_class': '15', 'str_score': '16', 'class_key': 'fighter',
                     'group_ids': [str(rebels), str(guild)], 'group_id': str(guild),
                     'notes': json.dumps(['Brave', {'t': 'Fears the sea', 'h': 1}]),
                     'avatar': (io.BytesIO(png_bytes(200, 200)), 'ayla.png'),
                     'sheets': [(io.BytesIO(png_bytes(300, 400)), 's1.png'), (io.BytesIO(png_bytes(300, 400, (1, 2, 3))), 's2.png')],
                     'attack_name': ['Longsword'], 'attack_bonus': ['5'], 'attack_damage': ['1d8+3'], 'attack_type': ['slashing']},
               content_type='multipart/form-data')
    assert r.status_code == 302
    ayla = q('SELECT id FROM characters WHERE campaign_id = ? AND name = ?', cid, 'Ayla')[0]['id']
    baron = u.new_character(cid, 'Baron Voss', is_npc='on', level=9, max_hp=80)
    ex('UPDATE characters SET notes = ? WHERE id = ?', json.dumps([{'t': SECRET_NOTE, 'h': 1}]), baron)
    ex('INSERT INTO character_attacks (character_id, position, name, attack_bonus, damage, damage_type) VALUES (?, 0, ?, ?, ?, ?) RETURNING id',
       baron, 'Rapier', 7, '1d8+4', 'piercing')
    ex('INSERT INTO character_levels (character_id, level, max_hp, armor_class, str_score, dex_score, con_score, int_score, wis_score, cha_score) '
       'VALUES (?, 2, 18, 14, 10, 12, 11, 10, 10, 10) ON CONFLICT (character_id, level) DO NOTHING RETURNING character_id', ayla)

    mid = u.new_map(cid, 'Harbour', 400, 300)
    ex('UPDATE maps SET grid_size = 40, grid_color = ?, feet_per_square = 10, locked_for_players = 1 WHERE id = ?', 'white', mid)
    token = u.json(f'/campaigns/{cid}/api/maps/{mid}/pins', {'pin_type': 'character', 'character_id': ayla, 'x': 120, 'y': 90}).get_json()['id']
    prop = u.json(f'/campaigns/{cid}/api/maps/{mid}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'custom_name': 'Dead sailor', 'x': 200, 'y': 150}).get_json()['id']
    ex('UPDATE map_pins SET dm_only = 1 WHERE id = ?', prop)
    u.json(f'/campaigns/{cid}/api/maps/{mid}/drawings', {'kind': 'rect', 'data': {}, 'cx': 60, 'cy': 60, 'w': 40, 'h': 30, 'color': '#112233',
                                                        'fill': True, 'fill_opacity': 0.5, 'attached_pin_id': token})
    u.json(f'/campaigns/{cid}/api/maps/{mid}/drawings', {'kind': 'pen', 'data': {'points': [[-0.5, -0.5], [0.5, 0.5]]}, 'cx': 300, 'cy': 200, 'w': 50, 'h': 50})
    mask = base64.b64encode(bytes([0xff]) + bytes(265)).decode()          # 50 x 38 cells: the top-left eight are fogged
    assert u.json(f'/campaigns/{cid}/api/maps/{mid}/fog', {'mask': mask}).status_code == 200

    u.post(f'/campaigns/{cid}/quests/add', data={'title': 'Find the crown', 'details': 'Under the harbour'})
    u.post(f'/campaigns/{cid}/quests/add', data={'title': 'Expose the baron', 'hidden': 'on'})
    u.post(f'/campaigns/{cid}/journal/new', data={'title': 'Session one', 'played_on': '2026-10-31',
                                                   'body': json.dumps(['The party met', {'t': 'The baron smirked', 'h': 1}])})
    u.post(f'/campaigns/{cid}/treasury/add', data={'kind': 'income', 'gp': '50', 'note': 'Reward'})
    u.post(f'/campaigns/{cid}/treasury/add', data={'kind': 'income', 'gp': '7', 'character_id': str(ayla), 'note': 'Pocket money'})
    u.post(f'/campaigns/{cid}/treasury/add', data={'kind': 'expense', 'gp': '5', 'note': 'Rope'})
    return cid, {'ayla': ayla, 'baron': baron, 'map': mid}


@pytest.fixture
def source(make_user):
    u = make_user()
    cid, h = build_source(u)
    return u, cid, h


def export(u, cid):
    r = u.get(f'/campaigns/{cid}/export/campaign')
    assert r.status_code == 200 and r.data[:2] == b'PK'
    return r.data


# ------------------------------------------------------------------ export
def test_export_is_for_the_dm_only(source, make_user):
    u, cid, _ = source
    player, outsider = make_user(), make_user()
    u.add_member(cid, player, status='player')
    assert player.get(f'/campaigns/{cid}/export/campaign').status_code == 403
    assert outsider.get(f'/campaigns/{cid}/export/campaign').status_code in (403, 404)
    co_dm = make_user()
    u.add_member(cid, co_dm, status='dm')
    assert co_dm.get(f'/campaigns/{cid}/export/campaign').status_code == 200          # any DM may, not only the owner


def test_the_export_holds_what_the_dm_built_and_nothing_about_people(source, make_user):
    u, cid, _ = source
    other = make_user()
    u.add_member(cid, other)
    ex('UPDATE campaigns SET next_session_note = ? WHERE id = ?', 'secret meetup', cid)
    z = zipfile.ZipFile(io.BytesIO(export(u, cid)))
    raw = z.read('manifest.json').decode()
    m = json.loads(raw)
    assert (m['format'], m['version']) == ('oaken-campaign', 2)
    assert m['campaign']['name'] == 'The Sunken Crown' and m['campaign']['setting'] == 'Saltmarch' and m['campaign']['status'] == 'on_hold'
    assert m['campaign']['hide_npcs'] == 1 and SECRET_NOTE in m['campaign']['notes']
    assert [f['name'] for f in m['factions']] == ['Rebels', 'Guild']
    assert [c['name'] for c in m['characters']] == ['Ayla', 'Baron Voss']
    assert SECRET_NOTE in m['characters'][1]['notes']                                  # the DM's file carries the hidden notes
    assert [q_['title'] for q_ in m['quests']] == ['Find the crown', 'Expose the baron'] and m['quests'][1]['hidden'] == 1
    assert len(m['maps']) == 1 and len(m['maps'][0]['pins']) == 2 and len(m['maps'][0]['drawings']) == 2 and m['maps'][0]['fog']
    assert [t['gp'] for t in m['treasury']] == [50, 7, -5]
    assert any(n.startswith('images/avatars/') for n in z.namelist()) and any(n.startswith('images/maps/') for n in z.namelist())
    for left_out in (other.name, u.name, 'secret meetup', 'invite_link', 'campaign_members', 'invitation', 'player_id'):
        assert left_out not in raw, left_out
    assert not [k for k in m['campaign'] if k in ('access_mode', 'next_session_at', 'members')]


def test_the_export_leaves_out_the_live_battle_and_temporary_familiars(source):
    u, cid, h = source
    u.add_to_battle(cid, h['ayla'])
    ex('INSERT INTO characters (campaign_id, name, is_temp_familiar) VALUES (?, ?, 1) RETURNING id', cid, 'Passing owl')
    m = json.loads(zipfile.ZipFile(io.BytesIO(export(u, cid))).read('manifest.json'))
    assert [c['name'] for c in m['characters']] == ['Ayla', 'Baron Voss'] and 'battle' not in m


def test_a_campaign_with_nothing_in_it_still_exports_and_imports(make_user):
    u = make_user()
    cid = u.new_campaign('Empty')
    r = do_import(u, export(u, cid))
    assert r.status_code == 302 and '/campaigns/' in r.headers['Location']
    assert len(mine(u, 'Empty')) == 2


# ------------------------------------------------------------------ import: a faithful, separate copy
def test_import_makes_a_new_campaign_owned_by_the_importer_with_everything_in_it(source, make_user):
    u, cid, h = source
    z = export(u, cid)
    friend = make_user()                                    # somebody else, holding the file
    r = do_import(friend, z)
    assert r.status_code == 302
    page = friend.get(r.headers['Location'])
    assert page.status_code == 200
    assert len(mine(friend)) == 1
    nid = mine(friend)[0]
    new = q('SELECT * FROM campaigns WHERE id = ?', nid)[0]
    assert nid != cid
    member = q('SELECT role, status FROM campaign_members WHERE campaign_id = ?', nid)
    assert [(m['role'], m['status']) for m in member] == [('owner', 'dm')]            # one person: the importer, as owner and DM

    assert (new['name'], new['setting'], new['status'], new['hide_npcs']) == ('The Sunken Crown', 'Saltmarch', 'on_hold', True)
    assert new['access_mode'] == 'private'                                              # never an open invite link by surprise
    assert 'A tale of tides' in new['description'] and SECRET_NOTE in new['notes']
    assert new['avatar_path'] and friend.get('/uploads/' + new['avatar_path']).status_code == 200

    groups = {g['name']: g for g in q('SELECT * FROM groups WHERE campaign_id = ?', nid)}
    assert set(groups) == {'Rebels', 'Guild'} and groups['Rebels']['color'] == '#aa3322' and groups['Rebels']['bio'] == 'Sworn against the crown'
    chars = {c['name']: c for c in q('SELECT * FROM characters WHERE campaign_id = ?', nid)}
    assert set(chars) == {'Ayla', 'Baron Voss'}
    ayla, baron = chars['Ayla'], chars['Baron Voss']
    assert (ayla['level'], ayla['max_hp'], ayla['armor_class'], ayla['str_score'], ayla['class_key']) == (4, 33, 15, 16, 'fighter')
    assert baron['is_npc'] and not ayla['is_npc'] and SECRET_NOTE in baron['notes']
    assert ayla['created_by'] == q('SELECT id FROM users WHERE username = ?', friend.name)[0]['id'] and ayla['player_id'] is None
    assert [g['name'] for g in q('SELECT gr.name FROM character_groups cg JOIN groups gr ON gr.id = cg.group_id WHERE cg.character_id = ? ORDER BY cg.position', ayla['id'])] \
        == [g['name'] for g in q('SELECT gr.name FROM character_groups cg JOIN groups gr ON gr.id = cg.group_id WHERE cg.character_id = ? ORDER BY cg.position', h['ayla'])]
    assert q('SELECT name FROM groups WHERE id = ?', ayla['group_id'])[0]['name'] == q('SELECT gr.name FROM characters c JOIN groups gr ON gr.id = c.group_id WHERE c.id = ?', h['ayla'])[0]['name']
    assert [a['name'] for a in q('SELECT name FROM character_attacks WHERE character_id = ?', baron['id'])] == ['Rapier']
    assert [a['name'] for a in q('SELECT name FROM character_attacks WHERE character_id = ?', ayla['id'])] == [a['name'] for a in q('SELECT name FROM character_attacks WHERE character_id = ?', h['ayla'])]
    assert len(q('SELECT 1 FROM character_levels WHERE character_id = ?', ayla['id'])) == len(q('SELECT 1 FROM character_levels WHERE character_id = ?', h['ayla'])) >= 1
    assert ayla['avatar_path'] and friend.get('/uploads/' + ayla['avatar_path']).status_code == 200
    sheets = q('SELECT image_path FROM character_sheets WHERE character_id = ? ORDER BY sort_order', ayla['id'])
    assert len(sheets) == 2 and all(friend.get('/uploads/' + s['image_path']).status_code == 200 for s in sheets)

    gm = q('SELECT * FROM maps WHERE campaign_id = ?', nid)
    assert len(gm) == 1
    m = gm[0]
    assert (m['name'], m['grid_size'], m['grid_color'], m['feet_per_square'], m['locked_for_players'], m['linked_to_battle']) == ('Harbour', 40, 'white', 10, 1, 0)
    assert (m['image_width'], m['image_height']) == (400, 300) and friend.get('/uploads/' + m['image_path']).status_code == 200
    assert m['fog_mask'] and (m['fog_cols'], m['fog_rows']) == (50, 38)
    pins = q('SELECT * FROM map_pins WHERE map_id = ? ORDER BY id', m['id'])
    assert [p['pin_type'] for p in pins] == ['character', 'prop']
    assert pins[0]['character_id'] == ayla['id'] and (pins[0]['x'], pins[0]['y']) == (120, 90) and pins[0]['dm_only'] == 0
    assert (pins[1]['icon_key'], pins[1]['custom_name'], pins[1]['dm_only'], pins[1]['participant_id'], pins[1]['owner_user_id']) == ('skull', 'Dead sailor', 1, None, None)
    drawings = q('SELECT * FROM map_drawings WHERE map_id = ? ORDER BY sort_order', m['id'])
    assert [d['kind'] for d in drawings] == ['rect', 'pen'] and drawings[0]['attached_pin_id'] == pins[0]['id'] and drawings[0]['color'] == '#112233'
    assert json.loads(drawings[1]['data']) == {'points': [[-0.5, -0.5], [0.5, 0.5]]}

    quests = q('SELECT title, details, hidden FROM quests WHERE campaign_id = ? ORDER BY id', nid)
    assert [(x['title'], x['hidden']) for x in quests] == [('Find the crown', False), ('Expose the baron', True)] and quests[0]['details'] == 'Under the harbour'
    j = q('SELECT title, played_on, body, created_by FROM session_journal WHERE campaign_id = ?', nid)
    assert len(j) == 1 and j[0]['title'] == 'Session one' and str(j[0]['played_on']) == '2026-10-31' and 'The baron smirked' in j[0]['body']
    t = q('SELECT character_id, cp, sp, ep, gp, pp, note FROM treasury_entries WHERE campaign_id = ? ORDER BY id', nid)
    assert [(x['gp'], x['note'], x['character_id']) for x in t] == [(50, 'Reward', None), (7, 'Pocket money', ayla['id']), (-5, 'Rope', None)]

    # nothing about people or history came along, and the original is untouched
    assert not q('SELECT 1 FROM campaign_invite_links WHERE campaign_id = ?', nid) and not q('SELECT 1 FROM dice_rolls WHERE campaign_id = ?', nid)
    assert len(q('SELECT 1 FROM characters WHERE campaign_id = ?', cid)) == 2 and len(q('SELECT 1 FROM campaign_members WHERE campaign_id = ?', cid)) == 1
    assert q('SELECT avatar_path FROM characters WHERE id = ?', h['ayla'])[0]['avatar_path'] != ayla['avatar_path']   # its own files, not shared ones


def test_the_importer_can_use_the_new_campaign_straight_away(source):
    u, cid, _ = source
    r = do_import(u, export(u, cid))
    nid = mine(u, 'The Sunken Crown')[-1]
    assert nid != cid
    for path in ('/home', '/characters', '/factions', '/maps', '/journal', '/treasury', '/edit'):
        assert u.get(f'/campaigns/{nid}{path}').status_code == 200, path
    mid = q('SELECT id FROM maps WHERE campaign_id = ?', nid)[0]['id']
    assert u.get(f'/campaigns/{nid}/maps/{mid}').status_code == 200 and u.get(f'/campaigns/{nid}/api/maps/{mid}/fog').status_code == 200
    # and exporting the copy gives the same thing again
    a = json.loads(zipfile.ZipFile(io.BytesIO(export(u, cid))).read('manifest.json'))
    b = json.loads(zipfile.ZipFile(io.BytesIO(export(u, nid))).read('manifest.json'))
    for key in ('factions', 'characters', 'quests', 'journal'):
        assert [x.get('name') or x.get('title') for x in a[key]] == [x.get('name') or x.get('title') for x in b[key]]
    assert [(t['gp'], t['note']) for t in a['treasury']] == [(t['gp'], t['note']) for t in b['treasury']]
    assert len(a['maps'][0]['pins']) == len(b['maps'][0]['pins']) and a['maps'][0]['fog'] == b['maps'][0]['fog']


def test_importing_twice_makes_two_campaigns_and_touches_no_existing_one(source):
    u, cid, _ = source
    z = export(u, cid)
    before = len(q('SELECT 1 FROM characters'))
    do_import(u, z)
    do_import(u, z)
    assert len(mine(u, 'The Sunken Crown')) == 3
    assert len(q('SELECT 1 FROM characters')) == before + 4
    assert len(q('SELECT 1 FROM characters WHERE campaign_id = ?', cid)) == 2
    keys = [r['avatar_path'] for r in q('SELECT avatar_path FROM characters WHERE name = ?', 'Ayla')]
    assert len(set(keys)) == len(keys)                         # no two characters share a file


def test_deleting_the_copy_does_not_break_the_original_images(source):
    u, cid, h = source
    do_import(u, export(u, cid))
    nid = mine(u, 'The Sunken Crown')[-1]
    assert u.post(f'/campaigns/{nid}/delete').status_code == 302
    for row in q('SELECT avatar_path FROM characters WHERE campaign_id = ? AND avatar_path IS NOT NULL', cid):
        assert u.get('/uploads/' + row['avatar_path']).status_code == 200


def test_a_map_whose_picture_is_missing_keeps_its_tokens_on_a_blank_canvas(source):
    u, cid, _ = source
    z = zipfile.ZipFile(io.BytesIO(export(u, cid)))
    manifest = json.loads(z.read('manifest.json'))
    files = {n: z.read(n) for n in z.namelist() if n.startswith('images/') and '/maps/' not in n}
    do_import(u, zip_of(manifest, files))
    nid = mine(u, 'The Sunken Crown')[-1]
    m = q('SELECT * FROM maps WHERE campaign_id = ?', nid)[0]
    assert (m['image_width'], m['image_height']) == (400, 300) and u.get('/uploads/' + m['image_path']).status_code == 200
    assert len(q('SELECT 1 FROM map_pins WHERE map_id = ?', m['id'])) == 2 and m['fog_mask']


def test_a_missing_character_picture_does_not_block_the_import(source):
    u, cid, _ = source
    z = zipfile.ZipFile(io.BytesIO(export(u, cid)))
    manifest = json.loads(z.read('manifest.json'))
    files = {n: z.read(n) for n in z.namelist() if n.startswith('images/') and '/sheets/' not in n}
    do_import(u, zip_of(manifest, files))
    nid = mine(u, 'The Sunken Crown')[-1]
    ayla = q('SELECT id FROM characters WHERE campaign_id = ? AND name = ?', nid, 'Ayla')[0]['id']
    assert not q('SELECT 1 FROM character_sheets WHERE character_id = ?', ayla)


def test_import_by_direct_upload(source):
    u, cid, _ = source
    key = direct_put(u, 'import', export(u, cid), 'application/zip')
    r = u.post('/campaigns/import', data={'import_file__key': key}, content_type='multipart/form-data')
    assert r.status_code == 302 and '/campaigns/' in r.headers['Location']
    assert len(mine(u, 'The Sunken Crown')) == 2
    again = u.post('/campaigns/import', data={'import_file__key': key}, content_type='multipart/form-data')      # the staged file was cleaned up
    assert again.status_code == 302 and len(mine(u, 'The Sunken Crown')) == 2


# ------------------------------------------------------------------ import: files it must turn away
def test_a_characters_export_is_not_a_campaign_and_the_other_way_round(source):
    u, cid, _ = source
    chars_zip = u.get(f'/campaigns/{cid}/export/data').data
    n = len(q('SELECT 1 FROM campaigns'))
    r = do_import(u, chars_zip)
    assert r.status_code == 302 and r.headers['Location'].endswith('/campaigns?import_error=characters')
    assert 'holds characters and factions' in u.get(r.headers['Location']).get_data(as_text=True)
    assert len(q('SELECT 1 FROM campaigns')) == n
    dst = u.new_campaign('Target')
    r = u.post(f'/campaigns/{dst}/import/data', data={'import_file': (io.BytesIO(export(u, cid)), 'c.zip')}, content_type='multipart/form-data')
    assert r.status_code == 302 and 'import_error=campaign' in r.headers['Location']
    assert 'Import it from the Campaigns page' in u.get(r.headers['Location']).get_data(as_text=True)
    assert not q('SELECT 1 FROM characters WHERE campaign_id = ?', dst)


@pytest.mark.parametrize('payload', [
    b'not a zip at all',
    zip_of({}, raw_manifest='{"format": "oaken-campaign", "version": 2'),                 # cut off
    zip_of({}, raw_manifest='[1, 2, 3]'),
    zip_of({'format': 'something-else', 'version': 2}),
    zip_of({'format': 'oaken-campaign', 'version': 99}),                                    # from the future
    zip_of({}, raw_manifest='\xff\xfe'),
])
def test_unusable_files_make_no_campaign_and_never_500(make_user, payload):
    u = make_user()
    n = len(q('SELECT 1 FROM campaigns'))
    r = do_import(u, payload)
    assert r.status_code == 302 and r.headers['Location'].endswith('/campaigns?import_error=file')
    assert len(q('SELECT 1 FROM campaigns')) == n
    assert 'could not be read' in u.get(r.headers['Location']).get_data(as_text=True) or "couldn&#39;t be read" in u.get(r.headers['Location']).get_data(as_text=True)


def test_an_empty_upload_does_nothing(make_user):
    u = make_user()
    n = len(q('SELECT 1 FROM campaigns'))
    r = u.post('/campaigns/import', data={}, content_type='multipart/form-data')
    assert r.status_code == 302 and r.headers['Location'].endswith('/campaigns') and len(q('SELECT 1 FROM campaigns')) == n


def test_a_file_with_the_right_label_and_garbage_inside_becomes_an_empty_campaign(make_user):
    u = make_user()
    r = do_import(u, zip_of({'format': 'oaken-campaign', 'version': 'x', 'campaign': 'nope', 'factions': 5, 'characters': {'a': 1}, 'maps': None, 'quests': 'q'}))
    assert r.status_code == 302 and 'import_error' not in r.headers['Location']
    assert len(mine(u, 'Imported campaign')) == 1 and len(mine(u)) == 1


def test_a_hostile_manifest_is_cleaned_before_it_reaches_the_database(make_user):
    u = make_user()
    big = 'x' * 500
    manifest = {
        'format': 'oaken-campaign', 'version': 2,
        'campaign': {'name': '<img src=x onerror=alert(1)>' + big, 'description': '<script>alert(1)</script>', 'setting': big, 'status': 'archived; DROP TABLE campaigns',
                     'hide_npcs': 'yes', 'notes': '["<script>alert(2)</script>ok"]', 'avatar_file': '../../etc/passwd'},
        'factions': [{'ref': 1, 'name': 'A\x00B', 'color': 'red', 'avatar_file': 'avatars/../../x.png'}, {'ref': 1, 'name': 'duplicate ref'}, 'junk', {'name': 'no ref'}],
        'characters': [
            {'ref': 7, 'name': 'Evil', 'level': 9999, 'max_hp': -50, 'str_score': 'NaN', 'dex_score': float('inf'), 'armor_class': 1e30, 'class_key': 'not-a-class',
             'notes': '<script>alert(3)</script><b>bold</b>', 'faction_refs': [1, 99, 'x', 1], 'main_faction_ref': 99, 'is_npc': 0, 'is_ally': 1,
             'attacks': [{'name': 'x' * 999, 'attack_bonus': 'lots', 'damage': '<b>', 'damage_type': 5}], 'sheet_files': ['sheets/ok.png', 'sheets/../x.png', 5, None],
             'levels': [{'level': 0}, {'level': 5, 'max_hp': 12}, {'level': 5}, {'level': 99}]},
            {'ref': 7, 'name': 'Same ref again'}, {'ref': 8, 'name': 'Kept'}],
        'maps': [{'ref': 1, 'name': '<b>Map</b>', 'image_file': 'maps/missing.png', 'image_width': 90000, 'image_height': -4, 'grid_size': 10 ** 9, 'grid_color': 'neon',
                  'feet_per_square': 0, 'fog': 'not base64 !!',
                  'pins': [{'ref': 1, 'pin_type': 'character', 'character_ref': 7, 'x': 1e400, 'y': 'a', 'scale': 10 ** 9},
                           {'ref': 2, 'pin_type': 'character', 'character_ref': 12345},                 # nobody by that ref
                           {'ref': 3, 'pin_type': 'prop', 'icon_key': '../../x', 'x': 1, 'y': 1},        # not a real icon
                           {'ref': 4, 'pin_type': 'prop', 'icon_key': 'skull', 'custom_name': '<i>' + big, 'x': 5, 'y': 6},
                           {'ref': 5, 'pin_type': 'wizard'}, {'ref': 4, 'pin_type': 'prop', 'icon_key': 'skull'}],
                  'drawings': [{'kind': 'rect', 'cx': float('nan'), 'color': 'javascript:1', 'attached_pin_ref': 4, 'data': {'evil': '<script>', 'points': 'no'}},
                               {'kind': 'laser'}, {'kind': 'pen', 'data': {'points': [[1, 2], [3], ['a', 'b'], [4, 5]]}, 'attached_pin_ref': 77}]}],
        'quests': [{'title': ' ', 'details': 'x'}, {'title': 'T' * 999, 'details': 'D' * 9999, 'done': 'true'}] + [{'title': 'q%d' % i} for i in range(200)],
        'journal': [{'title': '<b>Hi</b>', 'played_on': 'not a date', 'body': '["<script>alert(4)</script>fine"]'}, {'title': 'empty', 'played_on': '2026-01-01', 'body': '[]'}],
        'treasury': [{'gp': 10 ** 12, 'note': '<b>big</b>', 'created_at': 'yesterday', 'batch': '../x'}, {'gp': 0, 'sp': 0}, {'pp': -3, 'holder_ref': 424242, 'note': ''}],
    }
    r = do_import(u, zip_of(None, raw_manifest=json.dumps(manifest)))
    assert r.status_code == 302 and '/campaigns/' in r.headers['Location'] and 'import_error' not in r.headers['Location']
    nid = mine(u)[0]
    camp = q('SELECT * FROM campaigns WHERE id = ?', nid)[0]
    assert len(camp['name']) == 120 and camp['status'] == 'active' and camp['hide_npcs'] is True and camp['avatar_path'] is None and len(camp['setting']) == 80
    assert '<script' not in (camp['description'] or '') + (camp['notes'] or '') and 'ok' in camp['notes']
    groups = q('SELECT name, color, avatar_path FROM groups WHERE campaign_id = ?', nid)
    assert [(g['name'], g['color'], g['avatar_path']) for g in groups] == [('AB', '#c9a24b', None)]
    chars = {c['name']: c for c in q('SELECT * FROM characters WHERE campaign_id = ?', nid)}
    assert set(chars) == {'Evil', 'Kept'}
    evil = chars['Evil']
    assert (evil['level'], evil['max_hp'], evil['class_key'], evil['is_ally'], evil['armor_class']) == (30, 0, None, 0, 100)
    assert '<script' not in evil['notes'] and 'bold' in evil['notes']
    assert q('SELECT group_id FROM character_groups WHERE character_id = ?', evil['id']) == [{'group_id': evil['group_id']}]
    atk = q('SELECT name FROM character_attacks WHERE character_id = ?', evil['id'])
    assert all(len(a['name']) < 200 for a in atk)
    assert [lv['level'] for lv in q('SELECT level FROM character_levels WHERE character_id = ? ORDER BY level', evil['id'])] == [5]
    assert not q('SELECT 1 FROM character_sheets WHERE character_id = ?', evil['id'])          # the one name that looked fine had no file behind it
    m = q('SELECT * FROM maps WHERE campaign_id = ?', nid)[0]
    assert (m['name'], m['grid_size'], m['grid_color'], m['feet_per_square'], m['fog_mask']) == ('<b>Map</b>', 100, 'gray', 1, None)
    assert (m['image_width'], m['image_height']) == (2000, 1000)
    pins = q('SELECT * FROM map_pins WHERE map_id = ? ORDER BY id', m['id'])
    assert [(p['pin_type'], p['icon_key']) for p in pins] == [('character', None), ('prop', 'skull')]
    assert pins[0]['character_id'] == evil['id'] and pins[0]['x'] == 0 and pins[0]['scale'] == 20
    assert len(pins[1]['custom_name']) == 80
    drawings = q('SELECT * FROM map_drawings WHERE map_id = ? ORDER BY sort_order', m['id'])
    assert [d['kind'] for d in drawings] == ['rect', 'pen'] and drawings[0]['color'] == '#c9a24b' and drawings[0]['cx'] == 0
    assert drawings[0]['attached_pin_id'] == pins[1]['id'] and drawings[1]['attached_pin_id'] is None
    assert json.loads(drawings[0]['data']) == {} and json.loads(drawings[1]['data']) == {'points': [[1.0, 2.0], [4.0, 5.0]]}
    quests = q('SELECT title, details, done FROM quests WHERE campaign_id = ? ORDER BY id', nid)
    assert len(quests) == 59 and len(quests[0]['title']) == 120 and len(quests[0]['details']) == 600 and quests[0]['done'] is True
    j = q('SELECT title, body FROM session_journal WHERE campaign_id = ?', nid)
    assert len(j) == 1 and '<' not in j[0]['title'] and '<script' not in j[0]['body']
    t = q('SELECT gp, pp, note, batch, character_id, created_at FROM treasury_entries WHERE campaign_id = ? ORDER BY id', nid)
    assert [(x['gp'], x['pp']) for x in t] == [(1000000, 0), (0, -3)] and all(x['batch'] is None and x['character_id'] is None and x['created_at'] for x in t)
    assert t[1]['note'] == 'Entry'


# ------------------------------------------------------------------ limits and who may import
def test_an_import_that_would_pass_the_image_limit_keeps_nothing(source, monkeypatch):
    u, cid, _ = source
    z = export(u, cid)
    monkeypatch.setenv('OAKEN_QUOTA_USER_MB', str((int(q('SELECT COALESCE(SUM(bytes),0) AS n FROM stored_files WHERE campaign_id = ?', cid)[0]['n']) + 1) / 1048576))
    n_campaigns, n_files = len(q('SELECT 1 FROM campaigns')), len(q('SELECT 1 FROM stored_files'))
    r = do_import(u, z)
    assert r.status_code == 302 and r.headers['Location'].endswith('/campaigns?import_error=quota')
    assert 'image storage limit' in u.get(r.headers['Location']).get_data(as_text=True)
    assert (len(q('SELECT 1 FROM campaigns')), len(q('SELECT 1 FROM stored_files'))) == (n_campaigns, n_files)
    monkeypatch.setenv('OAKEN_QUOTA_USER_MB', '300')
    assert '/campaigns/' in do_import(u, z).headers['Location'] and 'import_error' not in do_import(u, z).headers['Location']


def test_import_counts_against_the_campaign_limit_like_creating_one(make_user, monkeypatch):
    import security
    monkeypatch.setattr(security, 'MAX_OWNED_CAMPAIGNS', 2)
    u = make_user()
    cid = u.new_campaign('One')
    z = export(u, cid)
    u.new_campaign('Two')
    r = do_import(u, z)
    assert r.status_code == 429 and 'already own 2 campaigns' in r.get_data(as_text=True)
    assert len(mine(u)) == 2


def test_a_signed_out_visitor_cannot_import(appmod):
    c = appmod.app.test_client()
    r = c.post('/campaigns/import', data={'import_file': (io.BytesIO(b'PK'), 'x.zip')}, content_type='multipart/form-data')
    assert r.status_code in (302, 401, 403) and (r.status_code != 302 or '/login' in r.headers['Location'])


# ------------------------------------------------------------------ pages
def test_the_campaigns_page_offers_import_to_everyone_and_export_to_dms(source, make_user):
    u, cid, _ = source
    player = make_user()
    u.add_member(cid, player, status='player')
    mine = u.get('/campaigns').get_data(as_text=True)
    assert '/campaigns/import' in mine and '/export/campaign' in mine and 'data-auto-submit-request' in mine
    theirs = player.get('/campaigns').get_data(as_text=True)
    assert '/campaigns/import' in theirs and '/export/campaign' not in theirs


def test_the_settings_page_has_the_export_for_dms_only(source, make_user):
    u, cid, _ = source
    player = make_user()
    u.add_member(cid, player, status='player')
    assert '/export/campaign' in u.get(f'/campaigns/{cid}/edit').get_data(as_text=True)
    assert '/export/campaign' not in player.get(f'/campaigns/{cid}/edit').get_data(as_text=True)


def test_an_export_the_import_would_refuse_is_not_handed_out(source, monkeypatch):
    import importer
    u, cid, _ = source
    monkeypatch.setattr(importer, 'MAX_ZIP_BYTES', 1000)
    r = u.get(f'/campaigns/{cid}/export/campaign')
    assert r.status_code == 302 and 'export_error=size' in r.headers['Location']
    assert 'more than 50 MB' in u.get(r.headers['Location']).get_data(as_text=True)
