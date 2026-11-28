"""DL-180 part 15 (reworked in part 27): a picture is added to the canvas, beside the map: a position, a size, a turn and a remove, and nothing else."""
import io
import json

from conftest import q, png_bytes
from test_campaign_transfer import ex, mine, export, do_import


def put(u, cid, mid, w=800, h=600, **fields):
    data = {'image': (io.BytesIO(png_bytes(w, h)), 'handout.png')}
    data.update(fields)
    return u.post(f'/campaigns/{cid}/api/maps/{mid}/images', data=data, content_type='multipart/form-data')


def test_an_image_lands_beside_the_map_as_a_drawing_and_is_served(camp):
    u, cid = camp
    mid = u.new_map(cid, 'Hall', 1000, 800)
    r = put(u, cid, mid, 400, 300)
    assert r.status_code == 200
    d = r.get_json()
    assert d['kind'] == 'image' and d['data']['path'].startswith('maps/') and d['url'] == '/uploads/' + d['data']['path']
    assert (d['w'], d['h']) == (400, 300)                                     # its own size, as it fits
    assert d['cx'] - d['w'] / 2 >= 1000 and d['cy'] - d['h'] / 2 == 0         # right of the map, top-aligned with it
    assert u.get(d['url']).status_code == 200
    rows = u.get(f'/campaigns/{cid}/api/maps/{mid}/drawings').get_json()
    assert [(x['kind'], x['url']) for x in rows] == [('image', d['url'])]


def test_a_large_image_is_shrunk_to_the_size_of_the_map(camp):
    u, cid = camp
    mid = u.new_map(cid, 'Hall', 1000, 800)
    d = put(u, cid, mid, 3000, 2000).get_json()
    assert d['w'] == 1000 and round(d['h']) == 667                             # the map's width, same shape


def test_pictures_are_placed_next_to_each_other_not_on_top(camp):
    u, cid = camp
    mid = u.new_map(cid, 'Hall', 1000, 800)
    boxes = []
    for _ in range(6):
        d = put(u, cid, mid, 600, 400).get_json()
        boxes.append((d['cx'] - d['w'] / 2, d['cy'] - d['h'] / 2, d['cx'] + d['w'] / 2, d['cy'] + d['h'] / 2))
    boxes.append((0, 0, 1000, 800))                                            # the map
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)     # none overlap
        assert -1500 <= a[0] and a[2] <= 2500 and -1200 <= a[1] and a[3] <= 2000            # all within reach of the map


def test_a_picture_lands_on_the_grid_when_the_map_snaps(camp):
    u, cid = camp
    mid = u.new_map(cid, 'Hall', 1000, 800)
    ex('UPDATE maps SET grid_size = 60, grid_offset_x = 7, grid_offset_y = 11, snap_to_grid = 1 WHERE id = ?', mid)
    d = put(u, cid, mid, 300, 200).get_json()
    left, top = d['cx'] - d['w'] / 2, d['cy'] - d['h'] / 2
    assert (left - 7) % 60 == 0 and (top - 11) % 60 == 0 and left >= 1000


def test_a_picture_has_no_hiding_locking_or_attaching(camp, make_user):
    u, cid = camp
    mid = u.new_map(cid)
    player = make_user()
    u.add_member(cid, player)
    A = f'/campaigns/{cid}/api/maps/{mid}/drawings'
    d = put(u, cid, mid, dm_only='1').get_json()
    assert d['dm_only'] == 0 and d['locked'] == 0 and d['attached_pin_id'] is None
    assert [x['id'] for x in player.get(A).get_json()] == [d['id']]            # the whole table sees the canvas
    # even a picture an older version hid is now shown, and a Player may move it
    ex('UPDATE map_drawings SET dm_only = 1 WHERE id = ?', d['id'])
    assert [x['id'] for x in player.get(A).get_json()] == [d['id']]
    assert player.json(f'{A}/{d["id"]}/update', {'cx': 5}).status_code == 200


def test_a_picture_stays_within_reach_of_the_map(camp):
    u, cid = camp
    mid = u.new_map(cid, 'Hall', 1000, 800)
    A = f'/campaigns/{cid}/api/maps/{mid}/drawings'
    d = put(u, cid, mid, 300, 200).get_json()
    assert u.json(f'{A}/{d["id"]}/update', {'cx': 90000, 'cy': -90000, 'w': 90000, 'h': 5}).status_code == 200
    got = u.get(A).get_json()[0]
    assert got['w'] == 2000 and got['h'] == 6                                   # at most two map sizes across
    assert got['cx'] - got['w'] / 2 >= -1500 and got['cx'] + got['w'] / 2 <= 2500 and got['cy'] >= -1200 and got['cy'] <= -1200 + 3
    # a picture put back by undo is held to the same limits
    back = u.json(A, {'kind': 'image', 'data': d['data'], 'cx': 5e6, 'cy': 5e6, 'w': 400, 'h': 300}).get_json()
    row = [x for x in u.get(A).get_json() if x['id'] == back['id']][0]
    assert row['cx'] + 200 <= 2500 and row['cy'] + 150 <= 2000


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
    d = put(u, cid, mid, 300, 200).get_json()
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
    assert (rows[0]['cx'], rows[0]['cy'], rows[0]['w'], rows[0]['h']) == (d['cx'], d['cy'], 300, 200)     # beside the map, where it was


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
