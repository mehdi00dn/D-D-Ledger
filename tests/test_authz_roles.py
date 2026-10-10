"""DL-5: role-by-route authorization matrix.

test_authorization_matrix.py proves campaigns cannot see each other's data.  This file proves the
other axis: inside (and outside) ONE campaign, each kind of person can only do what their role
allows.  The five roles are the ones the app really has:

    anonymous   -- not logged in
    outsider    -- logged in, not a member of the campaign (an unanswered invitation is not membership)
    player      -- member, in-game status "player"
    dm          -- member, in-game status "dm" (not the owner)
    owner       -- the campaign's owner (always a DM)

POLICY below is the single written-down answer to "who may call this route".  It is checked against
the app's real URL map, so adding a route without deciding its policy fails the build.  Every route is
then exercised from the roles BELOW its minimum (must be refused, and must change nothing) and from
its minimum role (must not be refused -- otherwise the matrix could pass simply because everything is
broken).  The remaining tests cover access that changes while a session is still open (removed, left,
demoted, ownership transferred) and private media.

Policy notes worth knowing (current behavior, recorded as-is, not endorsed):
  * map_new / import_data are open to every member, Players included.
  * Deleting (character, sheet, faction, map) is for the owner or whoever made the item: the matrix below uses
    owner-made items, so every non-owner is refused; tests/test_made_by.py covers the maker.
  * /uploads/<key> needs a login but not campaign membership: the random 128-bit key is the secret.
"""
import re
import pytest
from conftest import q, png_bytes
from test_authorization_matrix import build_world, fill

PUBLIC, LOGIN, MEMBER, DM, OWNER = 'public', 'login', 'member', 'dm', 'owner'
RANK = {PUBLIC: 0, LOGIN: 1, MEMBER: 2, DM: 3, OWNER: 4}

POLICY = {
    # --- outside any campaign ---
    'healthz': PUBLIC, 'register': PUBLIC, 'login': PUBLIC, 'static': PUBLIC,
    'logout': LOGIN, 'index': LOGIN, 'campaigns_list': LOGIN, 'campaign_new': LOGIN,
    'join_via_link': LOGIN, 'join_via_link_accept': LOGIN,
    'notifications_page': LOGIN, 'notification_read': LOGIN, 'notifications_read_all': LOGIN,
    'invitation_accept': LOGIN, 'invitation_decline': LOGIN,
    'uploaded_file': LOGIN, 'upload_sign': LOGIN, 'local_direct_upload': LOGIN, 'local_download': LOGIN,
    # --- campaign management ---
    'preview_as_player': DM,
    'campaign_edit': {'GET': MEMBER, 'POST': OWNER},
    'campaign_member_add': OWNER, 'campaign_member_uninvite': OWNER,
    'invite_link_create': OWNER, 'invite_link_revoke': OWNER, 'api_member_search': OWNER,
    'campaign_member_status': OWNER, 'campaign_member_make_owner': OWNER,
    'campaign_member_remove': OWNER, 'campaign_delete': OWNER,
    'campaign_leave': MEMBER,
    # --- characters and factions ---
    'characters_list': MEMBER, 'character_new': MEMBER, 'character_detail': MEMBER,
    'character_edit': DM, 'character_delete': OWNER, 'sheet_delete': OWNER,       # delete: owner or the member who made it
    'factions_list': MEMBER, 'faction_detail': MEMBER,
    'faction_new': DM, 'faction_edit': DM, 'faction_delete': OWNER, 'faction_member_remove': DM,
    'api_characters': MEMBER, 'api_character_detail': MEMBER,
    'dice_view': MEMBER, 'dice_roll': MEMBER, 'dice_rolls': MEMBER, 'reference_view': MEMBER, 'reference_search': MEMBER, 'reference_detail': MEMBER, 'export_data': MEMBER, 'character_export': MEMBER, 'faction_export': MEMBER,
    'import_data': MEMBER,
    # --- battle ---
    'battle_view': MEMBER, 'api_battle_list': MEMBER,
    **{e: DM for e in (
        'api_battle_add', 'api_battle_add_group', 'api_battle_heal', 'api_battle_damage', 'api_battle_temphp',
        'api_battle_set_hp', 'api_battle_initiative', 'api_battle_ac', 'api_battle_max_hp', 'api_battle_die',
        'api_battle_revive', 'api_battle_conditions', 'api_battle_next_turn', 'api_battle_prev_turn',
        'api_battle_restart_turns', 'api_battle_remove', 'api_battle_clear')},
    # --- maps (Players may edit a map the DM has not locked; see test_locked_map_* below) ---
    'maps_list': MEMBER, 'map_new': MEMBER, 'map_editor': MEMBER, 'map_delete': OWNER,
    'api_map_state': MEMBER, 'map_image': MEMBER, 'api_map_fog_get': MEMBER, 'api_map_fog_set': DM,
    'api_map_settings': MEMBER, 'api_map_drawings_list': MEMBER, 'api_map_drawings_add': MEMBER,
    'api_map_drawing_update': MEMBER, 'api_map_drawing_delete': MEMBER, 'api_map_drawings_clear': MEMBER,
    'api_map_pins_list': MEMBER, 'api_map_sync': MEMBER, 'api_map_pins_add': MEMBER,
    'api_map_pin_update': MEMBER, 'api_map_pin_delete': MEMBER,
}

BODY = {'amount': 1, 'value': 1, 'name': 'x', 'status': 'dm'}


def level(endpoint, method):
    p = POLICY[endpoint]
    return p[method] if isinstance(p, dict) else p


def all_routes(appmod):
    for rule in appmod.app.url_map.iter_rules():
        for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            yield rule, method


def campaign_routes(appmod):
    return [(r, m) for r, m in all_routes(appmod) if 'campaign_id' in r.arguments]


def send(client, method, url, body=None):
    if method == 'GET':
        return client.get(url)
    return client.open(url, method=method, json=body if body is not None else BODY)


def is_login_redirect(resp):
    return resp.status_code == 302 and '/login' in resp.headers.get('Location', '')


def refused(resp):
    return resp.status_code in (401, 403, 404) or is_login_redirect(resp)


def snapshot(cid):
    """Everything a refused request could have touched, for this campaign."""
    return {
        'campaign': q('SELECT * FROM campaigns WHERE id = ?', cid),
        'members': q('SELECT user_id, role, status FROM campaign_members WHERE campaign_id = ? ORDER BY user_id', cid),
        'invites': q('SELECT id, state FROM campaign_invitations WHERE campaign_id = ? ORDER BY id', cid),
        'links': q('SELECT id, revoked_at, use_count FROM campaign_invite_links WHERE campaign_id = ? ORDER BY id', cid),
        'chars': q('SELECT id, name, group_id, notes, max_hp FROM characters WHERE campaign_id = ? ORDER BY id', cid),
        'groups': q('SELECT id, name, color FROM groups WHERE campaign_id = ? ORDER BY id', cid),
        'maps': q('SELECT * FROM maps WHERE campaign_id = ? ORDER BY id', cid),
        'sheets': q('SELECT cs.id FROM character_sheets cs JOIN characters c ON c.id = cs.character_id WHERE c.campaign_id = ? ORDER BY cs.id', cid),
        'draw': q('SELECT d.* FROM map_drawings d JOIN maps m ON m.id = d.map_id WHERE m.campaign_id = ? ORDER BY d.id', cid),
        'pins': q('SELECT p.* FROM map_pins p JOIN maps m ON m.id = p.map_id WHERE m.campaign_id = ? ORDER BY p.id', cid),
        'battle': q('SELECT bp.* FROM battle_participants bp JOIN characters c ON c.id = bp.character_id WHERE c.campaign_id = ? ORDER BY bp.id', cid),
    }


class Table:
    """One campaign with an owner, a second DM, a player and an outsider."""
    def __init__(self, appmod, make_user):
        self.owner, self.dm, self.player, self.outsider = make_user(), make_user(), make_user(), make_user()
        self.w = build_world(self.owner)
        self.cid = self.w['cid']
        self.owner.add_member(self.cid, self.dm, 'dm')
        self.owner.add_member(self.cid, self.player, 'player')
        self.anon = appmod.app.test_client()
        self.clients = {'anonymous': self.anon, 'outsider': self.outsider.c, 'player': self.player.c,
                        'dm': self.dm.c, 'owner': self.owner.c}
        self.ids = dict(self.w, user_id=q('SELECT id FROM users WHERE username = ?', self.player.name)[0]['id'])
        # Token ownership: a Player may move/delete only a token the DM assigned to them, so the table's token is theirs.
        assigned = self.owner.json(f'/campaigns/{self.cid}/api/maps/{self.w["map_id"]}/pins/{self.w["pin_id"]}/update',
                                   {'owner_user_id': self.ids['user_id']})
        assert assigned.status_code == 200

    def url(self, rule):
        return fill(rule, self.cid, self.ids)

    def body(self, endpoint):
        return {'character_id': self.w['char_id']} if endpoint == 'api_battle_add' else None


# ---------------------------------------------------------------------------------------------
# The policy covers the real app
# ---------------------------------------------------------------------------------------------

def test_every_route_has_a_written_down_policy(appmod):
    endpoints = {r.endpoint for r in appmod.app.url_map.iter_rules()}
    assert not endpoints - set(POLICY), f'route(s) with no authorization policy: {sorted(endpoints - set(POLICY))} -- add them to POLICY'
    assert not set(POLICY) - endpoints, f'POLICY lists route(s) that no longer exist: {sorted(set(POLICY) - endpoints)}'


def test_public_policy_matches_the_apps_public_list(appmod):
    public = {e for e, p in POLICY.items() if p == PUBLIC}
    assert public == set(appmod.PUBLIC_ENDPOINTS)


def test_campaign_routes_are_all_wrapped_by_the_membership_check(appmod):
    """Every route under /campaigns/<id>/ must refuse a non-member -- verified by behavior in the matrix,
    and here by making sure no campaign route is classified below MEMBER."""
    for rule, method in campaign_routes(appmod):
        assert RANK[level(rule.endpoint, method)] >= RANK[MEMBER], rule.rule


# ---------------------------------------------------------------------------------------------
# Anonymous visitors
# ---------------------------------------------------------------------------------------------

def test_anonymous_is_sent_to_login_everywhere_except_public_routes(appmod, make_user):
    t = Table(appmod, make_user)
    before = snapshot(t.cid)
    checked = 0
    for rule, method in all_routes(appmod):
        if level(rule.endpoint, method) == PUBLIC:
            continue
        url = t.url(rule) if 'campaign_id' in rule.arguments else re.sub(r'<(?:int:)?\w+>', '1', re.sub(r'<path:\w+>', 'x.png', rule.rule))
        resp = send(t.anon, method, url)
        assert is_login_redirect(resp), f'anonymous {method} {url} -> {resp.status_code} {resp.headers.get("Location")}'
        checked += 1
    assert checked >= 85
    assert snapshot(t.cid) == before


def test_public_routes_work_without_a_login(appmod):
    c = appmod.app.test_client()
    assert c.get('/login').status_code == 200
    assert c.get('/register').status_code == 200
    assert c.get('/healthz').status_code == 200


# ---------------------------------------------------------------------------------------------
# Refusals: every campaign route, from every role below its minimum
# ---------------------------------------------------------------------------------------------

def test_every_campaign_route_refuses_the_roles_below_its_minimum(appmod, make_user):
    t = Table(appmod, make_user)
    before = snapshot(t.cid)
    refusals = 0
    for rule, method in campaign_routes(appmod):
        need = level(rule.endpoint, method)
        url, body = t.url(rule), t.body(rule.endpoint)
        for role, rank in (('anonymous', 0), ('outsider', 1), ('player', 2), ('dm', 3)):
            if rank >= RANK[need] and role != 'anonymous' and role != 'outsider':
                continue                                                    # this role is allowed
            resp = send(t.clients[role], method, url, body)
            if role == 'anonymous':
                assert is_login_redirect(resp), f'{role} {method} {url} -> {resp.status_code}'
            elif role == 'outsider':
                assert resp.status_code == 404, f'{role} {method} {url} -> {resp.status_code} (a non-member must not even learn the campaign exists)'
            else:
                assert resp.status_code == 403, f'{role} {method} {url} -> {resp.status_code} (needs {need}, endpoint {rule.endpoint})'
            refusals += 1
    assert refusals >= 190
    assert snapshot(t.cid) == before, 'a refused request changed campaign data'


def test_the_minimum_role_is_actually_let_in(appmod, make_user):
    """The mirror image: with a fresh campaign for every route (several routes destroy what others need),
    the lowest role the policy allows must NOT be refused."""
    who = {MEMBER: 'player', DM: 'dm', OWNER: 'owner'}
    ok = 0
    for rule, method in campaign_routes(appmod):
        need = level(rule.endpoint, method)
        t = Table(appmod, make_user)
        resp = send(t.clients[who[need]], method, t.url(rule), t.body(rule.endpoint))
        assert not refused(resp) and resp.status_code < 500, f'{who[need]} {method} {rule.rule} -> {resp.status_code} (endpoint {rule.endpoint})'
        ok += 1
    assert ok >= 70


def test_a_pending_invitation_is_not_membership(appmod, make_user):
    t = Table(appmod, make_user)
    invitee = make_user()
    assert t.owner.post(f'/campaigns/{t.cid}/members/add', data={'username': invitee.name, 'status': 'player'}).status_code == 302
    for url in (f'/campaigns/{t.cid}/battle', f'/campaigns/{t.cid}/api/battle', f'/campaigns/{t.cid}/characters',
                f'/campaigns/{t.cid}/maps/{t.w["map_id"]}/image'):
        assert invitee.get(url).status_code == 404, url
    inv = q("SELECT id FROM campaign_invitations WHERE campaign_id = ? AND state = 'pending'", t.cid)[0]['id']
    assert invitee.post(f'/invitations/{inv}/decline').status_code == 302
    assert invitee.get(f'/campaigns/{t.cid}/battle').status_code == 404
    assert t.outsider.post(f'/invitations/{inv}/accept').status_code == 404       # someone else's invitation


# ---------------------------------------------------------------------------------------------
# Field-level rules inside routes that every member may call
# ---------------------------------------------------------------------------------------------

def test_a_player_cannot_lock_the_map_or_link_it_to_the_battle(appmod, make_user):
    t = Table(appmod, make_user)
    mid = t.w['map_id']
    before = q('SELECT locked_for_players, linked_to_battle FROM maps WHERE id = ?', mid)
    for payload in ({'locked_for_players': 1}, {'linked_to_battle': 1}, {'locked_for_players': 0, 'linked_to_battle': 0}):
        assert t.player.json(f'/campaigns/{t.cid}/api/maps/{mid}/settings', payload).status_code == 403, payload
    assert q('SELECT locked_for_players, linked_to_battle FROM maps WHERE id = ?', mid) == before


def test_locked_map_refuses_every_player_edit_but_not_the_dm(appmod, make_user):
    t = Table(appmod, make_user)
    cid, mid = t.cid, t.w['map_id']
    assert t.dm.json(f'/campaigns/{cid}/api/maps/{mid}/settings', {'locked_for_players': True}).status_code == 200
    before = snapshot(cid)
    base = f'/campaigns/{cid}/api/maps/{mid}'
    attempts = [
        (f'{base}/drawings', {'kind': 'rect', 'data': {}, 'cx': 1, 'cy': 1, 'w': 5, 'h': 5}),
        (f'{base}/drawings/{t.w["drawing_id"]}/update', {'cx': 9}),
        (f'{base}/drawings/{t.w["drawing_id"]}/delete', {}),
        (f'{base}/drawings/clear', {}),
        (f'{base}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'x': 2, 'y': 2}),
        (f'{base}/pins/{t.w["pin_id"]}/update', {'x': 9}),
        (f'{base}/pins/{t.w["pin_id"]}/delete', {}),
        (f'{base}/settings', {'grid_size': 77}),
    ]
    for url, body in attempts:
        assert t.player.json(url, body).status_code == 403, url
    assert snapshot(cid) == before
    assert t.dm.json(f'{base}/pins', {'pin_type': 'prop', 'icon_key': 'skull', 'x': 3, 'y': 3}).status_code == 200


def test_a_dm_viewing_as_player_is_refused_dm_routes(appmod, make_user):
    t = Table(appmod, make_user)
    assert t.dm.post(f'/campaigns/{t.cid}/preview-as-player', data={'on': '1'}).status_code == 302
    before = snapshot(t.cid)
    assert t.dm.json(f'/campaigns/{t.cid}/api/battle/clear').status_code == 403
    assert t.dm.json(f'/campaigns/{t.cid}/api/maps/{t.w["map_id"]}/fog', {'enabled': True}).status_code == 403
    assert snapshot(t.cid) == before
    assert t.dm.post(f'/campaigns/{t.cid}/preview-as-player', data={'on': '0'}).status_code == 302     # and can switch it off again
    assert t.dm.json(f'/campaigns/{t.cid}/api/battle/clear').status_code == 200


# ---------------------------------------------------------------------------------------------
# Access that changes while a session stays open
# ---------------------------------------------------------------------------------------------

def _open_doors(t):
    c = t.cid
    return [('GET', f'/campaigns/{c}/battle'), ('GET', f'/campaigns/{c}/api/battle'), ('GET', f'/campaigns/{c}/characters'),
            ('GET', f'/campaigns/{c}/maps/{t.w["map_id"]}'), ('GET', f'/campaigns/{c}/maps/{t.w["map_id"]}/image'),
            ('GET', f'/campaigns/{c}/api/maps/{t.w["map_id"]}/sync'), ('GET', f'/campaigns/{c}/export/data'),
            ('POST', f'/campaigns/{c}/api/maps/{t.w["map_id"]}/pins'), ('POST', f'/campaigns/{c}/leave')]


def test_a_removed_member_is_locked_out_immediately(appmod, make_user):
    t = Table(appmod, make_user)
    for method, url in _open_doors(t)[:-1]:
        assert not refused(send(t.player.c, method, url, {'pin_type': 'prop', 'icon_key': 'skull', 'x': 1, 'y': 1})), url   # sanity: they were in
    assert t.owner.post(f'/campaigns/{t.cid}/members/{t.ids["user_id"]}/remove').status_code == 302
    before = snapshot(t.cid)
    for method, url in _open_doors(t):
        assert send(t.player.c, method, url, {'pin_type': 'prop', 'icon_key': 'skull', 'x': 1, 'y': 1}).status_code == 404, url
    assert snapshot(t.cid) == before
    assert t.player.get('/campaigns').status_code == 200                                   # still a valid user, just not here


def test_a_member_who_left_is_locked_out_immediately(appmod, make_user):
    t = Table(appmod, make_user)
    assert t.player.post(f'/campaigns/{t.cid}/leave').status_code == 302
    for method, url in _open_doors(t):
        assert send(t.player.c, method, url, {'pin_type': 'prop', 'icon_key': 'skull', 'x': 1, 'y': 1}).status_code == 404, url


def test_a_demoted_dm_loses_dm_routes_at_once_and_a_promoted_player_gains_them(appmod, make_user):
    t = Table(appmod, make_user)
    dm_uid = q('SELECT id FROM users WHERE username = ?', t.dm.name)[0]['id']
    assert t.dm.json(f'/campaigns/{t.cid}/api/battle/next-turn').status_code == 200
    assert t.owner.post(f'/campaigns/{t.cid}/members/{dm_uid}/status', data={'status': 'player'}).status_code == 302
    assert t.dm.json(f'/campaigns/{t.cid}/api/battle/next-turn').status_code == 403
    assert t.dm.post(f'/campaigns/{t.cid}/factions/new', data={'name': 'nope'}, content_type='multipart/form-data').status_code == 403
    assert t.player.json(f'/campaigns/{t.cid}/api/battle/next-turn').status_code == 403
    assert t.owner.post(f'/campaigns/{t.cid}/members/{t.ids["user_id"]}/status', data={'status': 'dm'}).status_code == 302
    assert t.player.json(f'/campaigns/{t.cid}/api/battle/next-turn').status_code == 200


def test_promoting_a_member_to_owner_grants_owner_routes_and_a_departed_owner_loses_them(appmod, make_user):
    t = Table(appmod, make_user)
    uid = t.ids['user_id']
    search = f'/campaigns/{t.cid}/api/members/search?q=zz'
    assert t.player.get(search).status_code == 403
    assert t.owner.post(f'/campaigns/{t.cid}/members/{uid}/make-owner').status_code == 302     # co-ownership, as the UI says
    assert t.player.get(search).status_code == 200
    assert t.player.json(f'/campaigns/{t.cid}/api/battle/next-turn').status_code == 200          # owners are DMs
    assert t.dm.get(search).status_code == 403                                                   # a DM who was not promoted still is not an owner
    assert t.owner.post(f'/campaigns/{t.cid}/leave').status_code == 302                          # allowed now that another owner exists
    assert t.owner.get(search).status_code == 404 and t.owner.post(f'/campaigns/{t.cid}/delete').status_code == 404
    assert t.player.get(search).status_code == 200


def test_a_deleted_campaign_is_gone_for_everyone(appmod, make_user):
    t = Table(appmod, make_user)
    assert t.owner.post(f'/campaigns/{t.cid}/delete').status_code == 302
    assert not q('SELECT 1 FROM campaigns WHERE id = ?', t.cid)
    for role in ('owner', 'dm', 'player'):
        assert t.clients[role].get(f'/campaigns/{t.cid}/battle').status_code == 404


# ---------------------------------------------------------------------------------------------
# Private media
# ---------------------------------------------------------------------------------------------

def _stored_key(path):
    return path.split('/uploads/')[-1]


def test_stored_images_need_a_login_and_unknown_keys_are_not_found(appmod, make_user):
    t = Table(appmod, make_user)
    key = q('SELECT image_path FROM maps WHERE id = ?', t.w['map_id'])[0]['image_path']
    assert key
    anon = t.anon.get(f'/uploads/{_stored_key(key)}')
    assert is_login_redirect(anon), anon.status_code
    assert t.outsider.get('/uploads/' + 'a' * 32 + '.png').status_code == 404
    assert t.outsider.get('/uploads/../app.py').status_code == 404
    assert t.outsider.get('/uploads/maps/..%2f..%2fapp.py').status_code == 404


def test_the_map_image_route_itself_is_members_only(appmod, make_user):
    t = Table(appmod, make_user)
    url = f'/campaigns/{t.cid}/maps/{t.w["map_id"]}/image'
    assert t.player.get(url).status_code == 200
    assert t.outsider.get(url).status_code == 404
    assert is_login_redirect(t.anon.get(url))


def test_an_outsider_is_never_shown_a_campaigns_image_keys(appmod, make_user):
    t = Table(appmod, make_user)
    t.owner.post(f'/campaigns/{t.cid}/characters/{t.w["char_id"]}/edit', data={'name': 'Owner Char', 'avatar': (__import__('io').BytesIO(png_bytes(30, 30)), 'a.png')},
                 content_type='multipart/form-data')
    keys = [r['avatar_path'] for r in q('SELECT avatar_path FROM characters WHERE campaign_id = ?', t.cid) if r['avatar_path']]
    keys += [r['image_path'] for r in q('SELECT image_path FROM maps WHERE campaign_id = ?', t.cid) if r['image_path']]
    assert keys
    for page in ('/campaigns', '/notifications', '/'):
        resp = t.outsider.get(page, follow_redirects=True)
        for key in keys:
            assert _stored_key(key) not in resp.get_data(as_text=True), f'{page} leaked {key}'
