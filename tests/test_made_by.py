"""Who made it: every character, faction and map records its maker, shows "Made by <name>", and only the maker
or the campaign owner can delete it -- nobody else, DMs included."""
import io
from conftest import q, png_bytes


def setup(make_user):
    owner, dm, player = make_user(), make_user(), make_user()
    cid = owner.new_campaign()
    owner.add_member(cid, dm, 'dm'); owner.add_member(cid, player, 'player')
    return owner, dm, player, cid


def uid(u):
    return q('SELECT id FROM users WHERE username = ?', u.name)[0]['id']


def test_new_things_record_their_maker(make_user):
    owner, dm, player, cid = setup(make_user)
    pc = player.new_character(cid, 'Pip')
    gid = dm.new_group(cid, 'Dm Guild')
    mid = player.new_map(cid, 'Pip Map')
    assert q('SELECT created_by FROM characters WHERE id = ?', pc)[0]['created_by'] == uid(player)
    assert q('SELECT created_by FROM groups WHERE id = ?', gid)[0]['created_by'] == uid(dm)
    assert q('SELECT created_by FROM maps WHERE id = ?', mid)[0]['created_by'] == uid(player)


def test_the_details_say_who_made_it(make_user):
    owner, dm, player, cid = setup(make_user)
    pc = player.new_character(cid, 'Pip')
    gid = dm.new_group(cid, 'Dm Guild')
    player.new_map(cid, 'Pip Map')
    assert f'Made by {player.name}' in owner.get(f'/campaigns/{cid}/characters/{pc}').get_data(as_text=True)
    assert f'Made by {player.name}' in owner.get(f'/campaigns/{cid}/characters').get_data(as_text=True)
    assert f'Made by {dm.name}' in player.get(f'/campaigns/{cid}/factions/{gid}').get_data(as_text=True)
    assert f'Made by {dm.name}' in player.get(f'/campaigns/{cid}/factions').get_data(as_text=True)
    assert f'Made by {player.name}' in owner.get(f'/campaigns/{cid}/maps?browse=1').get_data(as_text=True)
    detail = owner.get(f'/campaigns/{cid}/api/characters/{pc}/detail').get_json()
    assert detail['made_by'] == player.name and detail['can_delete'] is True


def test_the_maker_can_delete_their_own_but_nobody_elses(make_user):
    owner, dm, player, cid = setup(make_user)
    mine = player.new_map(cid, 'Pip Map')
    theirs = owner.new_map(cid, 'Owner Map')
    assert player.post(f'/campaigns/{cid}/maps/{theirs}/delete').status_code == 403
    assert dm.post(f'/campaigns/{cid}/maps/{theirs}/delete').status_code == 403           # a DM is not the owner
    assert q('SELECT 1 FROM maps WHERE id = ?', theirs)
    assert dm.post(f'/campaigns/{cid}/maps/{mine}/delete').status_code == 403             # nor the maker of Pip's map
    assert player.post(f'/campaigns/{cid}/maps/{mine}/delete').status_code == 302
    assert not q('SELECT 1 FROM maps WHERE id = ?', mine)


def test_characters_and_sheets_follow_the_same_rule(make_user):
    owner, dm, player, cid = setup(make_user)
    mine = player.new_character(cid, 'Pip')
    theirs = owner.new_character(cid, 'Boss')
    for who, ch in ((player, theirs), (dm, theirs), (dm, mine)):
        assert who.post(f'/campaigns/{cid}/characters/{ch}/delete').status_code == 403
    assert q('SELECT 1 FROM characters WHERE id = ?', theirs) and q('SELECT 1 FROM characters WHERE id = ?', mine)
    player.post(f'/campaigns/{cid}/characters/{mine}/edit', data={'name': 'Pip', 'sheets': (io.BytesIO(png_bytes(30, 30)), 's.png')}, content_type='multipart/form-data')
    sheet = q('SELECT id FROM character_sheets WHERE character_id = ?', mine)[0]['id']
    assert dm.post(f'/campaigns/{cid}/characters/{mine}/sheets/{sheet}/delete').status_code == 403
    assert player.post(f'/campaigns/{cid}/characters/{mine}/sheets/{sheet}/delete').status_code == 302
    assert player.post(f'/campaigns/{cid}/characters/{mine}/delete').status_code == 302
    assert owner.post(f'/campaigns/{cid}/characters/{theirs}/delete').status_code == 302  # the owner may delete anything


def test_a_dm_deletes_only_their_own_faction_and_the_owner_any(make_user):
    owner, dm, player, cid = setup(make_user)
    dm_group = dm.new_group(cid, 'Dm Guild')
    owner_group = owner.new_group(cid, 'Crown')
    assert dm.post(f'/campaigns/{cid}/factions/{owner_group}/delete').status_code == 403
    assert dm.post(f'/campaigns/{cid}/factions/{dm_group}/delete').status_code == 302
    assert owner.post(f'/campaigns/{cid}/factions/{owner_group}/delete').status_code == 302


def test_delete_buttons_appear_only_where_deleting_is_allowed(make_user):
    owner, dm, player, cid = setup(make_user)
    mine = player.new_map(cid, 'Pip Map'); theirs = owner.new_map(cid, 'Owner Map')
    pc = player.new_character(cid, 'Pip'); boss = owner.new_character(cid, 'Boss')
    maps = player.get(f'/campaigns/{cid}/maps?browse=1').get_data(as_text=True)
    assert f'/maps/{mine}/delete' in maps and f'/maps/{theirs}/delete' not in maps
    chars = player.get(f'/campaigns/{cid}/characters').get_data(as_text=True)
    assert f'/characters/{pc}/delete' in chars and f'/characters/{boss}/delete' not in chars
    dm_chars = dm.get(f'/campaigns/{cid}/characters').get_data(as_text=True)
    assert f'/characters/{pc}/delete' not in dm_chars and f'/characters/{boss}/delete' not in dm_chars
    owner_maps = owner.get(f'/campaigns/{cid}/maps?browse=1').get_data(as_text=True)
    assert f'/maps/{mine}/delete' in owner_maps and f'/maps/{theirs}/delete' in owner_maps


def test_existing_rows_are_backfilled_to_each_campaigns_owner(appmod, make_user):
    """Migration 0014: things that predate created_by belong to the campaign owner."""
    import database
    owner, dm, player, cid = setup(make_user)
    gid = owner.new_group(cid, 'Old'); mid = owner.new_map(cid, 'Old Map')
    ch = owner.new_character(cid, 'Old Char')
    db = database.get_db()
    db.execute('UPDATE groups SET created_by = NULL WHERE id = ?', (gid,)); db.execute('UPDATE maps SET created_by = NULL WHERE id = ?', (mid,))
    db.execute('UPDATE characters SET created_by = NULL WHERE id = ?', (ch,)); db.commit(); db.close()
    sql = open('migrations/0014_made_by.sql').read()
    db = database.get_db()
    for st in sql.split(';'):
        body = '\n'.join(l for l in st.splitlines() if not l.strip().startswith('--')).strip()
        if body.upper().startswith('UPDATE'):
            db.execute(body)
    db.commit(); db.close()
    assert q('SELECT created_by FROM groups WHERE id = ?', gid)[0]['created_by'] == uid(owner)
    assert q('SELECT created_by FROM maps WHERE id = ?', mid)[0]['created_by'] == uid(owner)
    assert q('SELECT created_by FROM characters WHERE id = ?', ch)[0]['created_by'] == uid(owner)
