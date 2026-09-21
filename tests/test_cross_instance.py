"""The exact scenario reported on Vercel: consecutive requests from ONE browser
session landing on DIFFERENT app instances.  Passing means state lives in the
shared database, not inside an instance."""
import re, io
import pytest
import requests
from conftest import BACKEND, png_bytes, CsrfSession

LEGACY_BUG = pytest.mark.xfail(BACKEND == 'sqlite', strict=True,
                               reason='legacy build keeps SQLite + uploads inside each instance (reproduces the Vercel bug)')


def _login_on_a(a):
    s = CsrfSession(a.url)
    name = f'x{abs(hash(a.url))%10**7}{__import__("uuid").uuid4().hex[:5]}'
    r = s.post(a.url + '/register', data={'username': name, 'password': 'secret12', 'confirm': 'secret12'}, allow_redirects=False)
    assert r.status_code == 302
    return s


@LEGACY_BUG
def test_data_written_on_one_instance_is_visible_on_the_other(two_instances):
    a, b = two_instances
    s = _login_on_a(a)
    r = s.post(a.url + '/campaigns/new', data={'name': 'Shared'}, allow_redirects=False)
    cid = int(re.search(r'/campaigns/(\d+)/', r.headers['Location']).group(1))
    s.post(f'{a.url}/campaigns/{cid}/characters/new', data={'name': 'Visible Everywhere', 'max_hp': '9'}, allow_redirects=False)
    # the very next requests are served by the other instance
    assert 'Shared' in s.get(b.url + '/campaigns').text
    page = s.get(f'{b.url}/campaigns/{cid}/characters')
    assert page.status_code == 200 and 'Visible Everywhere' in page.text


@LEGACY_BUG
def test_delete_then_redirect_across_instances_never_404s(two_instances):
    a, b = two_instances
    s = _login_on_a(a)
    r = s.post(a.url + '/campaigns/new', data={'name': 'C'}, allow_redirects=False)
    cid = int(re.search(r'/campaigns/(\d+)/', r.headers['Location']).group(1))
    s.post(f'{a.url}/campaigns/{cid}/characters/new', data={'name': 'Gone Soon'}, allow_redirects=False)
    api = s.get(f'{b.url}/campaigns/{cid}/api/characters')
    assert api.status_code == 200
    chid = api.json()[0]['id']
    d = s.post(f'{a.url}/campaigns/{cid}/characters/{chid}/delete', allow_redirects=False)
    assert d.status_code == 302
    assert s.get(b.url + d.headers['Location']).status_code == 200          # redirect served by the OTHER instance


from conftest import STORAGE
UPLOAD_XFAIL = pytest.mark.xfail(BACKEND == 'sqlite' or STORAGE == 'local', strict=True,
                                 reason='uploads live on each instance\'s local disk unless shared object storage is used')


@UPLOAD_XFAIL
def test_upload_on_one_instance_loads_on_the_other(two_instances):
    a, b = two_instances
    s = _login_on_a(a)
    r = s.post(a.url + '/campaigns/new', data={'name': 'Img'}, allow_redirects=False)
    cid = int(re.search(r'/campaigns/(\d+)/', r.headers['Location']).group(1))
    s.post(f'{a.url}/campaigns/{cid}/characters/new', data={'name': 'Pic'}, files={'avatar': ('a.png', png_bytes(), 'image/png')}, allow_redirects=False)
    ch = s.get(f'{b.url}/campaigns/{cid}/api/characters').json()[0]
    assert ch['avatar_path']
    assert s.get(f"{b.url}/uploads/{ch['avatar_path']}").status_code == 200
