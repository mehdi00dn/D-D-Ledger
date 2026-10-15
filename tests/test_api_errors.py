"""DL-21: every AJAX/JSON route answers failures in one shape {error, code, request_id}; HTML pages keep HTML errors."""


def _shape(r, status, code):
    assert r.status_code == status
    body = r.get_json()
    assert body and body['code'] == code
    assert isinstance(body['error'], str) and body['error']
    assert r.headers['X-Request-ID'] == body['request_id']
    return body


def test_unauthenticated_api_call_is_json_401(appmod):
    c = appmod.app.test_client()
    r = c.post('/campaigns/1/api/battle/add', json={})
    body = _shape(r, 401, 'unauthenticated')
    assert body['login_url'].endswith('/login')


def test_unauthenticated_page_still_redirects(appmod):
    r = appmod.app.test_client().get('/campaigns/1/maps')
    assert r.status_code == 302


def test_non_member_api_404_is_json_but_page_404_is_html(user, make_user):
    owner = user
    cid = owner.new_campaign()
    stranger = make_user()
    r = stranger.json(f'/campaigns/{cid}/api/battle/add', {'character_id': 1})
    _shape(r, 404, 'not_found')
    page = stranger.get(f'/campaigns/{cid}/maps')
    assert page.status_code == 404
    assert page.get_json(silent=True) is None


def test_wrong_method_on_api_is_json_405(user):
    cid = user.new_campaign()
    r = user.c.delete(f'/campaigns/{cid}/api/battle/add')
    _shape(r, 405, 'method_not_allowed')


def test_validation_error_carries_code(user):
    cid = user.new_campaign()
    r = user.json(f'/campaigns/{cid}/api/battle/add', {'character_id': 999999})
    body = r.get_json()
    assert r.status_code >= 400 and body['code'] and body['error'] and body['request_id']


def test_api_error_helper_shape(appmod):
    with appmod.app.test_request_context('/x'):
        resp = appmod.api_error('Nope', 409, thing=1)
        assert resp.status_code == 409
        d = resp.get_json()
        assert d['error'] == 'Nope' and d['code'] == 'conflict' and d['thing'] == 1
