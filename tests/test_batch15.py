"""DL-180 part 15: the Treasury guide on dark, "Additional images", the map tutorial, caching, phone layouts (static checks and pages)."""
import io
import os
import re

import pytest
from conftest import q, png_bytes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def test_the_treasury_guide_is_dark_with_light_text():
    assert 'class="guide-card guide-card-paper o-ondark"' in read('templates', 'treasury.html')   # paper in the light theme (DL-180 part 24)
    css = read('static', 'css', 'style.css')
    assert re.search(r'\.guide-card\.o-ondark \{[^}]*background-color: var\(--oak-charcoal-700\)', css)
    assert re.search(r'\.guide-card\.o-ondark \.guide-steps li[^{]*\{ color: var\(--parchment-dim\)', css)
    assert '.guide-card.o-paper' not in css


def test_the_character_page_calls_them_additional_images_and_has_no_thumbnail_list(camp):
    u, cid = camp
    ch = u.new_character(cid, name='Ayla')
    r = u.post(f'/campaigns/{cid}/characters/{ch}/edit', data={
        'name': 'Ayla', 'max_hp': '10', 'sheets': [(io.BytesIO(png_bytes(200, 300)), 'a.png'), (io.BytesIO(png_bytes(200, 300, (1, 2, 3))), 'b.png')]},
        content_type='multipart/form-data')
    assert r.status_code == 302
    page = u.get(f'/campaigns/{cid}/characters/{ch}').get_data(as_text=True)
    assert 'Additional images' in page and 'data-sheet-src' in page
    assert 'sheet-thumbs' not in page and 'Sheet images' not in page
    form = u.get(f'/campaigns/{cid}/characters/{ch}/edit').get_data(as_text=True)
    assert 'Additional images' in form and 'Sheet images' not in form


def test_the_map_has_a_how_it_works_guide_for_everyone(camp, make_user):
    u, cid = camp
    mid = u.new_map(cid)
    player = make_user()
    u.add_member(cid, player)
    dm_page = u.get(f'/campaigns/{cid}/maps/{mid}').get_data(as_text=True)
    pl_page = player.get(f'/campaigns/{cid}/maps/{mid}').get_data(as_text=True)
    for page in (dm_page, pl_page):
        assert 'How the map works' in page and 'map-help-btn' in page and 'Add a picture' in page and '<kbd>Ctrl</kbd>+<kbd>Z</kbd>' in page and 'id="map-add-image-btn"' in page
    assert 'hide from players' in dm_page and 'hide from players' not in pl_page      # the DM-only controls are explained to the DM only
    assert 'use fog' in dm_page and 'use fog' not in pl_page


def test_static_files_are_cached_for_a_year_when_versioned_and_a_day_otherwise(camp):
    u, cid = camp
    css = re.search(r'href="(/static/css/oaken\.css\?v=[0-9a-f]+)"', u.get(f'/campaigns/{cid}').get_data(as_text=True))
    assert css, 'stylesheets are linked with a content hash'
    r = u.get(css.group(1))
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'public, max-age=31536000, immutable'
    r = u.get('/static/assets/textures/wood/oak-dark.webp')
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'public, max-age=86400, stale-while-revalidate=604800'
    r = u.get('/static/css/oaken.css', headers={'If-None-Match': u.get('/static/css/oaken.css').headers['ETag']})
    assert r.status_code == 304 and 'max-age=86400' in r.headers['Cache-Control']


def test_the_map_image_answers_not_modified_without_reading_the_file(camp, monkeypatch):
    import app as appmod
    u, cid = camp
    mid = u.new_map(cid)
    url = f'/campaigns/{cid}/maps/{mid}/image'
    first = u.get(url)
    assert first.status_code == 200 and first.headers['Cache-Control'] == 'private, no-cache'
    etag = first.headers['ETag']
    reads = []
    real = appmod.get_storage().get
    monkeypatch.setattr(appmod.get_storage(), 'get', lambda *a, **k: reads.append(a) or real(*a, **k), raising=False)
    again = u.get(url, headers={'If-None-Match': etag})
    assert again.status_code == 304 and again.headers['ETag'] == etag and not reads
    import database
    db = database.get_db()
    db.execute('UPDATE maps SET fog_version = fog_version + 1 WHERE id = ?', (mid,)); db.commit(); db.close()      # the DM changed the fog
    changed = u.get(url, headers={'If-None-Match': etag})
    assert changed.status_code == 200 and changed.headers['ETag'] != etag


def test_card_pictures_load_lazily(camp):
    u, cid = camp
    u.post(f'/campaigns/{cid}/edit', data={'name': 'Banner', 'avatar': (io.BytesIO(png_bytes(1200, 720)), 'b.png')}, content_type='multipart/form-data')
    page = u.get('/campaigns').get_data(as_text=True)
    assert 'campaign-grid' in page and re.search(r'<img src="/uploads/avatars/[^"]+" alt="[^"]*" loading="lazy" decoding="async"', page)


def test_phone_rules_for_the_header_quests_and_campaign_cards():
    pub = read('static', 'css', 'public.css')
    assert re.search(r'@media \(max-width: 820px\) \{\s*\.pub-bar \{ row-gap: 0; min-block-size: 0; \}\s*\.pub-brand \{ min-block-size: var\(--pub-nav-h\); \}', pub)
    css = read('static', 'css', 'style.css')
    assert '.campaign-grid .group-image-block { block-size: auto; aspect-ratio: 2 / 1; }' in css
    assert re.search(r'@media \(max-width: 640px\) \{\s*\.quest-main \{ flex-basis: 100%; \}', css)
    assert '.quest-actions form[data-confirm] { order: 2; margin-inline-start: auto; }' in css
    assert '.quest-edit:not([open]) { order: 1; display: flex; }' in css
