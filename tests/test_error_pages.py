"""DL-182 part 9: a request that cannot be answered shows an Oaken page in a browser, and keeps its plain or JSON answer for scripts."""
from werkzeug.exceptions import InternalServerError

BROWSER = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'}


def test_unknown_page_is_an_oaken_404_for_a_signed_out_visitor(appmod):
    r = appmod.app.test_client().get('/definitely-not-here', headers=BROWSER)
    html = r.get_data(as_text=True)
    assert r.status_code == 404 and r.mimetype == 'text/html'
    assert 'Page not found' in html and 'This page wandered off the map.' in html and 'Error 404' in html
    assert 'Back to home' in html and 'Log in' in html and 'Go to your campaigns' not in html


def test_unknown_page_offers_the_campaign_list_when_signed_in(user):
    html = user.get('/definitely-not-here', headers=BROWSER).get_data(as_text=True)
    assert 'Go to your campaigns' in html and 'Back to home' not in html


def test_a_page_in_someone_elses_campaign_is_the_same_404(user, make_user):
    cid = user.new_campaign()
    stranger = make_user()
    r = stranger.get(f'/campaigns/{cid}/characters', headers=BROWSER)
    assert r.status_code == 404 and 'Page not found' in r.get_data(as_text=True)


def test_wrong_method_keeps_allow_and_shows_the_405_page(user):
    r = user.get('/logout', headers=BROWSER)
    assert r.status_code == 405 and 'Allow' in r.headers and 'POST' in r.headers['Allow']
    assert 'That did not work' in r.get_data(as_text=True)


def test_json_routes_still_answer_in_json(user):
    cid = user.new_campaign()
    r = user.c.get(f'/campaigns/{cid}/api/not-a-route', headers=BROWSER)
    body = r.get_json()
    assert r.status_code == 404 and body['code'] == 'not_found' and body['request_id']


def test_an_expired_form_is_a_page_for_a_browser_and_plain_text_for_a_script(appmod):
    c = appmod.app.test_client(); c.csrf_enabled = False
    page = c.post('/login', data={'username': 'x', 'password': 'y', 'csrf_token': 'stale'}, headers=BROWSER)
    assert page.status_code == 403 and page.mimetype == 'text/html'
    html = page.get_data(as_text=True)
    assert 'That form expired' in html and 'Your session expired or the form was not valid.' in html
    plain = c.post('/login', data={'username': 'x', 'password': 'y', 'csrf_token': 'stale'})
    assert plain.status_code == 403 and plain.mimetype == 'text/plain'
    assert plain.get_data(as_text=True).startswith('Your session expired')
    api = c.post('/campaigns/1/api/battle/add', json={}, headers={'X-CSRF-Token': 'stale'})
    assert api.status_code in (401, 403) and api.get_json()['code']


def test_too_many_requests_page_keeps_retry_after_and_says_how_long(appmod):
    with appmod.app.test_request_context('/x', headers=BROWSER):
        r = appmod.error_page(429, headers={'Retry-After': '7'})
    assert r.status_code == 429 and r.headers['Retry-After'] == '7'
    html = r.get_data(as_text=True)
    assert 'Slow down a little' in html and 'about 7 seconds' in html


def test_message_text_is_escaped(appmod):
    with appmod.app.test_request_context('/x', headers=BROWSER):
        html = appmod.error_page(400, message='<script>alert(1)</script>', title='<b>t</b>').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in html and '&lt;script&gt;' in html and '<b>t</b>' not in html


def test_an_unlisted_status_uses_the_generic_wording(appmod):
    with appmod.app.test_request_context('/x', headers=BROWSER):
        client_err = appmod.error_page(418)
        server_err = appmod.error_page(502)
    assert client_err.status_code == 418 and 'That request did not work' in client_err.get_data(as_text=True)
    assert server_err.status_code == 502 and 'Something went wrong' in server_err.get_data(as_text=True)


def test_a_server_fault_is_the_500_page(appmod):
    with appmod.app.test_request_context('/x', headers=BROWSER):
        r = appmod.http_error(InternalServerError())
    assert r.status_code == 500 and 'Something went wrong' in r.get_data(as_text=True)


def test_if_the_page_cannot_be_drawn_the_plain_answer_is_used(appmod, monkeypatch):
    def boom(*a, **k): raise RuntimeError('template broke')
    monkeypatch.setattr(appmod, 'render_template', boom)
    with appmod.app.test_request_context('/x', headers=BROWSER):
        assert appmod.error_page(404) is None
        r = appmod._plain_error('Nope.', 429, headers={'Retry-After': '3'})
    assert r.status_code == 429 and r.mimetype == 'text/plain' and r.get_data(as_text=True) == 'Nope.' and r.headers['Retry-After'] == '3'


def test_a_script_without_an_html_accept_gets_plain_text(appmod):
    with appmod.app.test_request_context('/x', headers={'Accept': '*/*'}):
        r = appmod._plain_error('Wait.', 429, headers={'Retry-After': '2'})
    assert r.mimetype == 'text/plain' and r.headers['Retry-After'] == '2'
    with appmod.app.test_request_context('/x'):
        assert appmod._plain_error('Wait.', 429).mimetype == 'text/plain'
