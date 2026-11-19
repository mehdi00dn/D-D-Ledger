"""DL-180 part 15: a picture can be placed on a map as an object of its own."""
import io
import json

from conftest import q, png_bytes
from test_campaign_transfer import ex, mine, export, do_import


def put(u, cid, mid, w=800, h=600, **fields):
    data = {'image': (io.BytesIO(png_bytes(w, h)), 'handout.png')}
    data.update(fields)
    return u.post(f'/campaigns/{cid}/api/maps/{mid}/images', data=data, content_type='multipart/form-data')


def test_an_image_lands_on_the_map_as_a_drawing_and_is_served(camp):
    u, cid = camp
    mid = u.new_map(cid, 'Hall', 1000, 800)
    r = put(u, cid, mid, 400, 300)
    assert r.status_code == 200
    d = r.get_json()
    assert d['kind'] == 'image' and d['data']['path'].startswith('maps/') and d['url'] == '/uploads/' + d['data']['path']
    assert (d['cx'], d['cy']) == (500, 400)                                   # the middle of the map by default
    assert (d['w'], d['h']) == (400, 300)                                     # its own size, as it fits
    assert u.get(d['url']).status_code == 200
    rows = u.get(f'/campaigns/{cid}/api/maps/{mid}/drawings').get_json()
    assert [(x['kind'], x['url']) for x in rows] == [('image', d['url'])]


def test_a_large_image_is_shrunk_to_half_the_map_and_placed_where_asked(camp):
    u, cid = camp
    mid = u.new_map(cid, 'Hall', 1000, 800)
    d = put(u, cid, mid, 1500, 1000, cx='200', cy='9999').get_json()
    assert d['w'] == 500 and round(d['h']) == 333                              # half the map's width, same shape
    assert d['cx'] == 200 and d['cy'] == 800                                   # kept inside the map


def test_a_file_that_is_not_an_image_is_refused(camp):
    u, cid = camp
    mid = u.new_map(cid)
    r = u.post(f'/campaigns/{cid}/api/maps/{mid}/images', data={'image': (io.BytesIO(b'not a picture'), 'x.png')}, content_type='multipart/form-data')
    assert r.status_code == 400 and r.get_json()['error']
    assert u.get(f'/campaigns/{cid}/api/maps/{mid}/drawings').get_json() == []


def test_a_picture_can_be_put_back_after_delete_but_only_a_file_of_this_campaign(camp, make_user):
    u, cid = camp
    mid = u.new_map(cid)
    A = f'/campaigns/{cid}/api/maps/{mid}/drawings'
    d = put(u, cid, mid).get_json()
    assert u.json(f'{A}/{d["id"]}/delete').status_code == 200
    back = u.json(A, {'kind': 'image', 'data': d['data'], 'cx': d['cx'], 'cy': d['cy'], 'w': d['w'], 'h': d['h']})
    assert back.status_code == 200 and u.get(A).get_json()[0]['url'] == d['url']
    # a key the campaign does not hold, or one that is not a map file at all, is not accepted
    other = make_user()
    ocid = other.new_campaign('Elsewhere')
    omid = other.new_map(ocid)
    theirs = put(other, ocid, omid).get_json()
    assert u.json(A, {'kind': 'image', 'data': theirs['data'], 'w': 5, 'h': 5}).status_code == 400
    assert u.json(A, {'kind': 'image', 'data': {'path': '../../etc/passwd'}, 'w': 5, 'h': 5}).status_code == 400
    assert u.json(A, {'kind': 'image', 'data': {}, 'w': 5, 'h': 5}).status_code == 400


def test_a_hidden_picture_never_reaches_a_player(camp, make_user):
    u, cid = camp
    mid = u.new_map(cid)
    player = make_user()
    u.add_member(cid, player)
    shown = put(u, cid, mid).get_json()
    hidden = put(u, cid, mid, dm_only='1').get_json()
    assert hidden['dm_only'] == 1
    seen = player.get(f'/campaigns/{cid}/api/maps/{mid}/drawings').get_json()
    assert [x['id'] for x in seen] == [shown['id']]
    assert hidden['data']['path'] not in json.dumps(seen)
    # and a Player may not hide one
    assert put(player, cid, mid, dm_only='1').get_json()['dm_only'] == 0


def test_a_player_may_not_add_pictures_to_a_locked_map(camp, make_user):
    u, cid = camp
    mid = u.new_map(cid)
    player = make_user()
    u.add_member(cid, player)
    ex('UPDATE maps SET locked_for_players = 1 WHERE id = ?', mid)
    assert put(player, cid, mid).status_code == 403
    assert q('SELECT COUNT(*) AS n FROM map_drawings WHERE map_id = ?', mid)[0]['n'] == 0


def test_the_number_of_pictures_per_map_is_limited(camp):
    import app
    u, cid = camp
    mid = u.new_map(cid)
    old = app.MAX_IMAGES_PER_MAP
    app.MAX_IMAGES_PER_MAP = 2
    try:
        assert put(u, cid, mid, 50, 50).status_code == 200 and put(u, cid, mid, 50, 50).status_code == 200
        r = put(u, cid, mid, 50, 50)
        assert r.status_code == 400 and 'already has 2 pictures' in r.get_json()['error']
    finally:
        app.MAX_IMAGES_PER_MAP = old


def test_deleting_the_map_removes_its_pictures_files(camp):
    u, cid = camp
    mid = u.new_map(cid)
    url = put(u, cid, mid).get_json()['url']
    assert u.get(url).status_code == 200
    u.post(f'/campaigns/{cid}/maps/{mid}/delete')
    assert u.get(url).status_code == 404
    assert q('SELECT COUNT(*) AS n FROM stored_files WHERE key = ?', url[len('/uploads/'):])[0]['n'] == 0


def test_pictures_travel_with_a_campaign_export_and_import(camp, make_user):
    import zipfile
    u, cid = camp
    mid = u.new_map(cid, 'Hall', 1000, 800)
    d = put(u, cid, mid, 300, 200, cx='400', cy='300').get_json()
    z = export(u, cid)
    m = json.loads(zipfile.ZipFile(io.BytesIO(z)).read('manifest.json'))
    exported = m['maps'][0]['drawings'][0]
    assert exported['kind'] == 'image' and exported['image_file'] and exported['data'] == {}
    assert any(n.startswith('images/') and n.endswith(exported['image_file'].split('/')[-1]) or exported['image_file'] in n
               for n in zipfile.ZipFile(io.BytesIO(z)).namelist())
    friend = make_user()
    assert do_import(friend, z).status_code == 302
    nid = mine(friend)[0]
    new_map = q('SELECT id FROM maps WHERE campaign_id = ?', nid)[0]['id']
    rows = q('SELECT * FROM map_drawings WHERE map_id = ?', new_map)
    assert len(rows) == 1 and rows[0]['kind'] == 'image'
    path = json.loads(rows[0]['data'])['path']
    assert path.startswith('maps/') and path != d['data']['path']              # its own file, so deleting one never breaks the other
    assert friend.get('/uploads/' + path).status_code == 200
    assert (rows[0]['cx'], rows[0]['cy'], rows[0]['w'], rows[0]['h']) == (400, 300, 300, 200)


def test_an_import_drops_a_picture_whose_file_is_missing(camp, make_user):
    import zipfile
    from test_campaign_transfer import zip_of
    u, cid = camp
    mid = u.new_map(cid)
    put(u, cid, mid)
    z = zipfile.ZipFile(io.BytesIO(export(u, cid)))
    m = json.loads(z.read('manifest.json'))
    m['maps'][0]['drawings'][0]['image_file'] = 'maps/not-in-the-zip.webp'
    m['maps'][0]['drawings'].append({'kind': 'image', 'data': {'path': 'maps/x.webp'}, 'cx': 1, 'cy': 1, 'w': 5, 'h': 5})
    files = {n: z.read(n) for n in z.namelist() if n != 'manifest.json'}
    friend = make_user()
    r = do_import(friend, zip_of(m, files))
    assert r.status_code == 302
    nid = mine(friend)[0]
    assert q('SELECT COUNT(*) AS n FROM map_drawings WHERE map_id IN (SELECT id FROM maps WHERE campaign_id = ?)', nid)[0]['n'] == 0
