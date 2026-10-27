"""DL-160: the personal character library - characters that belong to a person, not a campaign."""
import io
import re
import pytest
from conftest import q, png_bytes, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _form(**extra):
    data = {'name': 'Sylas Thorne', 'level': '3', 'max_hp': '27', 'armor_class': '16', 'speed': '35', 'class_key': 'rogue',
            'str_score': '8', 'dex_score': '17', 'con_score': '12', 'int_score': '13', 'wis_score': '10', 'cha_score': '14',
            'attacks_present': '1', 'attack_name': 'Rapier', 'attack_bonus': '6', 'attack_damage': '1d8+3', 'attack_type': 'piercing',
            'avatar': (io.BytesIO(png_bytes(40, 40)), 'a.png'), 'sheets': (io.BytesIO(png_bytes(60, 80)), 's.png')}
    data.update(extra)
    return data


def _make(user, **extra):
    r = user.post('/library/new', data=_form(**extra), content_type='multipart/form-data')
    assert r.status_code == 302, r.status_code
    return int(re.search(r'/library/(\d+)', r.headers['Location']).group(1))


def _row(char_id):
    return q('SELECT * FROM characters WHERE id = ?', char_id)[0]


def _exists(path):
    import app as appmod
    from storage import UPLOADS
    return appmod.get_storage().exists(UPLOADS, path)


def test_a_library_character_belongs_to_the_person_and_to_no_campaign(make_user):
    me, other = make_user(), make_user()
    cid = me.new_campaign()
    lib = _make(me)
    row = _row(lib)
    assert row['campaign_id'] is None and row['owner_id'] == q('SELECT id FROM users WHERE username = ?', me.name)[0]['id']
    assert row['level'] == 3 and row['speed'] == 35 and row['class_key'] == 'rogue' and row['avatar_path']
    assert q('SELECT name FROM character_attacks WHERE character_id = ?', lib) == [{'name': 'Rapier'}]
    assert len(q('SELECT id FROM character_sheets WHERE character_id = ?', lib)) == 1
    assert 'id="character-grid"' not in me.get(f'/campaigns/{cid}/characters').get_data(as_text=True)     # not one of the campaign's characters (only offered)
    page = me.get('/library').get_data(as_text=True)
    assert 'Sylas Thorne' in page and 'Rogue' in page
    assert me.get(f'/library/{lib}').status_code == 200
    assert me.get(f'/library/{lib}/edit').status_code == 200
    # nobody else can see or touch it
    assert 'Sylas Thorne' not in other.get('/library').get_data(as_text=True)
    for method, url in (('get', f'/library/{lib}'), ('get', f'/library/{lib}/edit'), ('post', f'/library/{lib}/delete'),
                        ('post', f'/library/{lib}/showcase'), ('post', f'/library/{lib}/add-to-campaign')):
        assert getattr(other, method)(url).status_code == 404, url
    assert _row(lib)['name'] == 'Sylas Thorne'


def test_editing_a_library_character_keeps_it_in_the_library(make_user):
    me = make_user()
    lib = _make(me)
    r = me.post(f'/library/{lib}/edit', data={'name': 'Sylas the Elder', 'level': '4', 'max_hp': '31', 'is_npc': 'on', 'attacks_present': '1'},
                content_type='multipart/form-data')
    assert r.status_code == 302
    row = _row(lib)
    assert row['name'] == 'Sylas the Elder' and row['level'] == 4 and row['campaign_id'] is None and row['is_npc'] == 0     # never an NPC
    assert q('SELECT COUNT(*) n FROM character_attacks WHERE character_id = ?', lib)[0]['n'] == 0


def test_adding_to_a_campaign_makes_a_separate_copy_once(make_user):
    me = make_user()
    cid, other_cid = me.new_campaign(), me.new_campaign()
    lib = _make(me)
    r = me.post(f'/library/{lib}/add-to-campaign', data={'campaign_id': str(cid), 'next': 'campaign'})
    assert r.status_code == 302 and f'/campaigns/{cid}/characters/' in r.headers['Location']
    copy = q('SELECT * FROM characters WHERE campaign_id = ?', cid)[0]
    src = _row(lib)
    uid = q('SELECT id FROM users WHERE username = ?', me.name)[0]['id']
    assert copy['library_id'] == lib and copy['player_id'] == uid and copy['created_by'] == uid and copy['owner_id'] is None
    assert copy['name'] == 'Sylas Thorne' and copy['level'] == 3 and copy['dex_score'] == 17 and copy['is_npc'] == 0
    assert copy['avatar_path'] and copy['avatar_path'] != src['avatar_path']                    # its own picture file
    assert _exists(copy['avatar_path']) and _exists(src['avatar_path'])
    sheet_copy = q('SELECT image_path FROM character_sheets WHERE character_id = ?', copy['id'])
    assert len(sheet_copy) == 1 and sheet_copy[0]['image_path'] != q('SELECT image_path FROM character_sheets WHERE character_id = ?', lib)[0]['image_path']
    assert q('SELECT name, attack_bonus FROM character_attacks WHERE character_id = ?', copy['id']) == [{'name': 'Rapier', 'attack_bonus': 6}]
    assert q('SELECT level FROM character_levels WHERE character_id = ?', copy['id']) == [{'level': 3}]
    assert 'a personal library' in me.get(f'/campaigns/{cid}/characters/{copy["id"]}').get_data(as_text=True)
    # the same campaign cannot get it twice, another can
    again = me.post(f'/library/{lib}/add-to-campaign', data={'campaign_id': str(cid)})
    assert 'note=already' in again.headers['Location']
    assert len(q('SELECT id FROM characters WHERE campaign_id = ?', cid)) == 1
    assert me.post(f'/library/{lib}/add-to-campaign', data={'campaign_id': str(other_cid)}).status_code == 302
    assert len(q('SELECT id FROM characters WHERE library_id = ?', lib)) == 2
    # the copy and the original now live separately
    me.post(f'/campaigns/{cid}/characters/{copy["id"]}/edit', data={'name': 'Renamed at the table', 'level': '9', 'attacks_present': ''}, content_type='multipart/form-data')
    assert _row(lib)['name'] == 'Sylas Thorne' and _row(lib)['level'] == 3
    assert 'Renamed at the table' in q('SELECT name FROM characters WHERE id = ?', copy['id'])[0]['name']


def test_only_your_own_campaigns_can_receive_a_copy(make_user):
    me, stranger = make_user(), make_user()
    theirs = stranger.new_campaign()
    lib = _make(me)
    r = me.post(f'/library/{lib}/add-to-campaign', data={'campaign_id': str(theirs)})
    assert 'note=not_member' in r.headers['Location']
    assert q('SELECT id FROM characters WHERE campaign_id = ?', theirs) == []
    assert 'note=not_member' in me.post(f'/library/{lib}/add-to-campaign', data={'campaign_id': 'nope'}).headers['Location']


def test_deleting_from_the_library_keeps_campaign_copies_and_frees_its_files(make_user):
    me = make_user()
    cid = me.new_campaign()
    lib = _make(me)
    me.post(f'/library/{lib}/add-to-campaign', data={'campaign_id': str(cid)})
    src = _row(lib)
    sheet = q('SELECT image_path FROM character_sheets WHERE character_id = ?', lib)[0]['image_path']
    r = me.post(f'/library/{lib}/delete')
    assert r.status_code == 302 and 'note=deleted' in r.headers['Location']
    assert q('SELECT id FROM characters WHERE id = ?', lib) == []
    assert not _exists(src['avatar_path']) and not _exists(sheet)
    copy = q('SELECT * FROM characters WHERE campaign_id = ?', cid)[0]
    assert copy['library_id'] is None and _exists(copy['avatar_path'])                          # the table keeps its character and picture


def test_a_sheet_image_can_be_removed_from_the_edit_page(make_user):
    me, other = make_user(), make_user()
    lib = _make(me)
    sheet = q('SELECT id, image_path FROM character_sheets WHERE character_id = ?', lib)[0]
    assert other.post(f'/library/{lib}/sheets/{sheet["id"]}/delete').status_code == 404
    assert len(q('SELECT id FROM character_sheets WHERE character_id = ?', lib)) == 1
    assert me.post(f'/library/{lib}/sheets/{sheet["id"]}/delete').status_code == 302
    assert q('SELECT id FROM character_sheets WHERE character_id = ?', lib) == [] and not _exists(sheet['image_path'])


def test_saving_a_campaign_character_to_the_library(make_user):
    dm, pl = make_user(), make_user()
    cid = dm.new_campaign(); dm.add_member(cid, pl)
    mine = pl.new_character(cid, name='Player hero', class_key='fighter', level=2)
    npc = dm.new_character(cid, name='Villain', is_npc='on')
    dms = dm.new_character(cid, name='DM hero')
    r = pl.post('/library/save', data={'campaign_id': str(cid), 'character_id': str(mine)})
    assert r.status_code == 302
    lib = int(re.search(r'/library/(\d+)', r.headers['Location']).group(1))
    row = _row(lib)
    assert row['campaign_id'] is None and row['name'] == 'Player hero' and row['class_key'] == 'fighter' and row['added_from']
    assert row['library_id'] is None and _row(mine)['library_id'] == lib                          # linked both ways round
    assert 'Saved from' in pl.get(f'/library/{lib}').get_data(as_text=True)
    # saving it again just opens the one already kept
    again = pl.post('/library/save', data={'campaign_id': str(cid), 'character_id': str(mine)})
    assert again.headers['Location'].endswith(f'/library/{lib}')
    assert len(q('SELECT id FROM characters WHERE owner_id IS NOT NULL AND campaign_id IS NULL AND name = ?', 'Player hero')) == 1
    # and it cannot be added back to the campaign it came from
    assert 'note=already' in pl.post(f'/library/{lib}/add-to-campaign', data={'campaign_id': str(cid)}).headers['Location']
    # only your own player characters can be kept: not an NPC, not another member's character
    assert pl.post('/library/save', data={'campaign_id': str(cid), 'character_id': str(dms)}).status_code == 404
    assert dm.post('/library/save', data={'campaign_id': str(cid), 'character_id': str(mine)}).status_code == 404
    assert dm.post('/library/save', data={'campaign_id': str(cid), 'character_id': str(npc)}).status_code == 404
    # the button is offered on the details page of a character you own
    assert 'Save to my library' in dm.get(f'/campaigns/{cid}/characters/{dms}').get_data(as_text=True)
    assert 'Save to my library' not in pl.get(f'/campaigns/{cid}/characters/{dms}').get_data(as_text=True)
    assert 'In my library' in pl.get(f'/campaigns/{cid}/characters/{mine}').get_data(as_text=True)


def test_the_campaign_character_list_offers_your_library(make_user):
    me = make_user()
    cid = me.new_campaign()
    assert 'Add a character from my library' not in me.get(f'/campaigns/{cid}/characters').get_data(as_text=True)
    lib = _make(me)
    page = me.get(f'/campaigns/{cid}/characters').get_data(as_text=True)
    assert 'Add a character from my library' in page and f'/library/{lib}/add-to-campaign' in page
    me.post(f'/library/{lib}/add-to-campaign', data={'campaign_id': str(cid)})
    assert 'Add a character from my library' not in me.get(f'/campaigns/{cid}/characters').get_data(as_text=True)


def test_the_library_has_a_size_limit(make_user, monkeypatch):
    import app as appmod
    monkeypatch.setattr(appmod, 'LIBRARY_MAX', 2)
    me = make_user()
    _make(me); _make(me)
    r = me.post('/library/new', data=_form(), content_type='multipart/form-data')
    assert 'note=full' in r.headers['Location']
    assert 'note=full' in me.get('/library/new').headers['Location']
    assert len(q('SELECT id FROM characters WHERE owner_id = (SELECT id FROM users WHERE username = ?) AND campaign_id IS NULL', me.name)) == 2


def test_featuring_a_library_character_on_the_profile(make_user):
    me, friend = make_user(), make_user()
    cid = me.new_campaign(); me.add_member(cid, friend)
    lib = _make(me)
    assert me.post(f'/library/{lib}/showcase').status_code == 302
    shown = friend.get(f'/u/{me.name}').get_data(as_text=True)
    assert 'Sylas Thorne' in shown
    assert q('SELECT character_id FROM profile_showcase WHERE character_id = ?', lib) != []
    assert 'On your profile' in me.get(f'/library/{lib}').get_data(as_text=True)
    me.post(f'/library/{lib}/showcase')
    assert 'Sylas Thorne' not in friend.get(f'/u/{me.name}').get_data(as_text=True)
    assert q('SELECT character_id FROM profile_showcase WHERE character_id = ?', lib) == []


def test_the_profile_showcase_is_capped_across_the_library(make_user):
    import app as appmod
    me = make_user()
    ids = [_make(me, name=f'Hero {i}') for i in range(appmod.SHOWCASE_MAX + 1)]
    for i in ids[:appmod.SHOWCASE_MAX]:
        assert 'note=featured' in me.post(f'/library/{i}/showcase').headers['Location']
    assert 'note=featured_full' in me.post(f'/library/{ids[-1]}/showcase').headers['Location']
    assert len(q('SELECT id FROM profile_showcase WHERE user_id = (SELECT id FROM users WHERE username = ?)', me.name)) == appmod.SHOWCASE_MAX


def test_a_library_character_shows_up_in_the_account_profile_picker(make_user):
    me = make_user()
    _make(me)
    page = me.get('/account').get_data(as_text=True)
    assert 'Sylas Thorne' in page and 'Library' in page


def test_deleting_the_account_removes_the_library_and_its_files(make_user):
    me = make_user()
    lib = _make(me)
    avatar = _row(lib)['avatar_path']
    sheet = q('SELECT image_path FROM character_sheets WHERE character_id = ?', lib)[0]['image_path']
    assert _exists(avatar) and _exists(sheet)
    r = me.post('/account/delete', data={'current_password': 'secret12', 'confirm_delete': 'DELETE'})
    assert r.status_code in (302, 200)
    assert q('SELECT id FROM characters WHERE id = ?', lib) == []
    assert not _exists(avatar) and not _exists(sheet)


def test_the_account_export_lists_the_library(make_user):
    me = make_user()
    _make(me)
    data = me.post('/account/export').get_json(force=True)
    assert [c['name'] for c in data['library_characters']] == ['Sylas Thorne']


def test_a_failed_copy_leaves_nothing_behind(make_user, monkeypatch):
    import app as appmod
    me = make_user()
    cid = me.new_campaign()
    lib = _make(me)
    before = q('SELECT COUNT(*) n FROM characters')[0]['n']
    real = appmod._save_level_stats
    def boom(*a, **k): raise RuntimeError('storage went away')
    monkeypatch.setattr(appmod, '_save_level_stats', boom)
    me.c.application.config['PROPAGATE_EXCEPTIONS'] = False
    r = me.post(f'/library/{lib}/add-to-campaign', data={'campaign_id': str(cid)})
    monkeypatch.setattr(appmod, '_save_level_stats', real)
    assert r.status_code == 500
    assert q('SELECT COUNT(*) n FROM characters')[0]['n'] == before and q('SELECT id FROM characters WHERE campaign_id = ?', cid) == []
