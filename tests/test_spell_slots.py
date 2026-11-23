"""DL-185: spell slots and the spell list.  A character has slots per level (the sheet's maximums, and how many are spent),
which are spent and restored in the battle list and on the character's page; and a spell list like the attacks, which a spell
book search can fill."""
import io
import json
import re

import pytest

import dnd5e
import srd
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _form(maxes=None, spells=None, slots_present=True, spells_present=True, npc=False):
    data = {'name': 'Mira', 'level': '5', 'max_hp': '30', 'armor_class': '12'}
    if npc:
        data['is_npc'] = 'on'
    if slots_present:
        data['slots_present'] = '1'
        for lvl in range(1, 10):
            data['slot_max_%d' % lvl] = str((maxes or {}).get(lvl, 0))
    if spells_present:
        data['spells_present'] = '1'
        spells = spells or []
        data['spell_name'] = [s[0] for s in spells]
        data['spell_level'] = [s[1] for s in spells]
        data['spell_damage'] = [s[2] for s in spells]
        data['spell_type'] = [s[3] for s in spells]
        data['spell_note'] = [s[4] for s in spells]
    return data


def _save(user, cid, char_id, **kw):
    r = user.post(f'/campaigns/{cid}/characters/{char_id}/edit', data=_form(**kw), content_type='multipart/form-data')
    assert r.status_code == 302, r.status_code


def _slots(char_id):
    return [(r['slot_level'], r['max_slots'], r['used_slots']) for r in q('SELECT * FROM character_spell_slots WHERE character_id = ? ORDER BY slot_level', char_id)]


def _spells(char_id):
    return [(r['name'], r['spell_level'], r['damage'], r['damage_type'], r['note'])
            for r in q('SELECT * FROM character_spells WHERE character_id = ? ORDER BY position', char_id)]


def _act(user, cid, char_id, action, level=None):
    return user.json(f'/campaigns/{cid}/api/characters/{char_id}/slots', {'action': action, 'level': level})


def test_helpers():
    assert dnd5e.clean_slot_maxes({'1': '4', 2: 3, '3': 'x', '4': '-2', '9': '99'}) == {1: 4, 2: 3, 9: 9}
    assert dnd5e.clean_slot_maxes(None) == {}
    s = dnd5e.clean_spell('  Fire   Bolt ', '0', '1d10', 'fire', 'R 120 ft')
    assert s == {'name': 'Fire Bolt', 'spell_level': 0, 'damage': '1d10', 'damage_type': 'fire', 'note': 'R 120 ft'}
    assert dnd5e.clean_spell('', 1, '', '', '') is None
    assert dnd5e.clean_spell('X', '99', '1d7', '', '')['spell_level'] == 9 and dnd5e.clean_spell('X', 'abc', '1d7', '', '')['damage'] == ''
    assert len(dnd5e.clean_spells([('n', 1, '', '', '')] * 200)) == dnd5e.MAX_SPELLS
    assert dnd5e.spell_damage_from_text('A creature takes 8d6 fire damage on a failed save.') == ('8d6', 'fire')
    assert dnd5e.spell_damage_from_text('deals 2d10 + 5 radiant damage') == ('2d10+5', 'radiant')
    assert dnd5e.spell_damage_from_text('You heal a creature.') == ('', '')


def test_the_spell_book_entry_carries_what_the_list_takes():
    d = {'name': 'Fireball', 'level': 3, 'school': {'name': 'Evocation'}, 'casting_time': '1 action', 'range': '150 feet', 'components': ['V', 'S', 'M'],
         'duration': 'Instantaneous', 'description': 'Each creature takes 8d6 fire damage on a failed save.', 'classes': []}
    spell = srd._spell(d)['spell']
    assert spell == {'name': 'Fireball', 'level': 3, 'damage': '8d6', 'damage_type': 'fire', 'note': '150 feet · 1 action'}
    cantrip = srd._spell({'name': 'Light', 'level': 0, 'range': 'Touch', 'casting_time': '1 action', 'concentration': True, 'description': 'An object glows.'})['spell']
    assert cantrip['level'] == 0 and cantrip['damage'] == '' and cantrip['note'] == 'Touch · 1 action · Concentration'


def test_slots_and_spells_are_saved_and_a_form_without_the_cards_leaves_them_alone(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, maxes={1: 4, 2: 3, 3: 2}, spells=[('Fireball', '3', '8d6', 'fire', '150 ft'), ('Fire Bolt', '0', '1D10', 'fire', '')])
    assert _slots(ch) == [(1, 4, 0), (2, 3, 0), (3, 2, 0)]
    assert _spells(ch) == [('Fireball', 3, '8d6', 'fire', '150 ft'), ('Fire Bolt', 0, '1d10', 'fire', '')]
    _save(dm, cid, ch, slots_present=False, spells_present=False)
    assert len(_slots(ch)) == 3 and len(_spells(ch)) == 2
    _save(dm, cid, ch, maxes={1: 4}, spells=[])                       # the cards shown and emptied do clear them
    assert _slots(ch) == [(1, 4, 0)] and _spells(ch) == []


def test_lowering_the_maximum_never_leaves_more_spent_than_there_are(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, maxes={1: 4})
    for _ in range(3):
        assert _act(dm, cid, ch, 'spend', 1).status_code == 200
    assert _slots(ch) == [(1, 4, 3)]
    _save(dm, cid, ch, maxes={1: 2})
    assert _slots(ch) == [(1, 2, 2)]
    _save(dm, cid, ch, maxes={1: 3})                                  # raising it does not refill
    assert _slots(ch) == [(1, 3, 2)]


def test_spend_restore_stay_inside_zero_and_the_maximum(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, maxes={2: 2})
    out = _act(dm, cid, ch, 'spend', 2).get_json()
    assert out['slots'] == [{'level': 2, 'max': 2, 'used': 1}]
    _act(dm, cid, ch, 'spend', 2); _act(dm, cid, ch, 'spend', 2)
    assert _slots(ch) == [(2, 2, 2)]
    _act(dm, cid, ch, 'restore', 2); _act(dm, cid, ch, 'restore', 2); _act(dm, cid, ch, 'restore', 2)
    assert _slots(ch) == [(2, 2, 0)]
    assert _act(dm, cid, ch, 'spend', 5).status_code == 200 and _slots(ch) == [(2, 2, 0)]      # a level with no slots changes nothing
    assert _act(dm, cid, ch, 'spend', 0).status_code == 400 and _act(dm, cid, ch, 'spend', 10).status_code == 400
    assert _act(dm, cid, ch, 'dance', 1).status_code == 400


def test_casting_spends_the_lowest_free_slot_at_or_above_the_spell_and_says_when_there_is_none(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, maxes={1: 1, 3: 1})
    assert _act(dm, cid, ch, 'cast', 1).get_json()['used_level'] == 1
    assert _act(dm, cid, ch, 'cast', 1).get_json()['used_level'] == 3          # level 1 is spent: the next one up is used
    r = _act(dm, cid, ch, 'cast', 1)
    assert r.status_code == 409 and 'No spell slot left' in r.get_json()['error'] and r.get_json()['slots']
    assert _slots(ch) == [(1, 1, 1), (3, 1, 1)]
    assert _act(dm, cid, ch, 'rest').status_code == 200 and _slots(ch) == [(1, 1, 0), (3, 1, 0)]


def test_only_the_dm_and_the_characters_player_may_change_slots(make_user):
    dm, player, other = make_user(), make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, player); dm.add_member(cid, other)
    mine = player.new_character(cid, name='Pip')
    _save(player, cid, mine, maxes={1: 2})
    assert _act(player, cid, mine, 'spend', 1).status_code == 200
    assert _act(other, cid, mine, 'spend', 1).status_code == 403
    assert _act(dm, cid, mine, 'restore', 1).status_code == 200
    assert _act(dm, cid, 999999, 'spend', 1).status_code == 404
    assert _slots(mine) == [(1, 2, 0)]


def _peek(user, cid, char_id):
    return user.get(f'/campaigns/{cid}/api/characters/{char_id}/detail')


def test_the_peek_carries_the_slots_and_who_may_click_them(make_user):
    dm, player, other = make_user(), make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, player); dm.add_member(cid, other)
    pc = player.new_character(cid, name='Pip')
    _save(player, cid, pc, maxes={1: 3, 2: 1})
    _act(player, cid, pc, 'spend', 1)
    seen_by_dm = _peek(dm, cid, pc).get_json()
    assert seen_by_dm['slots'] == [{'level': 1, 'max': 3, 'used': 1}, {'level': 2, 'max': 1, 'used': 0}] and seen_by_dm['can_edit_slots'] is True
    assert _peek(player, cid, pc).get_json()['can_edit_slots'] is True
    seen = _peek(other, cid, pc).get_json()
    assert seen['slots'] and seen['can_edit_slots'] is False                       # everyone at the table sees them, only two may change them


def test_the_battle_list_does_not_carry_slots(make_user):
    dm = make_user(); cid = dm.new_campaign()
    pc = dm.new_character(cid, name='Mira')
    _save(dm, cid, pc, maxes={1: 2})
    dm.add_to_battle(cid, pc)
    row = [r for r in dm.battle(cid) if r['character_id'] == pc][0]
    assert 'slots' not in row and 'can_edit_slots' not in row
    page = dm.get(f'/campaigns/{cid}/battle').data.decode()
    assert 'slot-pip' not in page and 'battle-slots' not in page


def test_a_monsters_slots_stay_hidden_from_players(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, player)
    mage = dm.new_character(cid, name='Lich', is_npc='on')
    _save(dm, cid, mage, maxes={9: 1}, npc=True)
    assert _peek(dm, cid, mage).get_json()['slots']
    r = _peek(player, cid, mage)
    assert r.status_code == 404 or 'slots' not in r.get_json()


def test_a_long_rest_refills_every_slot_of_the_party_but_not_of_monsters(make_user):
    dm = make_user(); cid = dm.new_campaign()
    pc = dm.new_character(cid, name='Mira')
    foe = dm.new_character(cid, name='Cultist', is_npc='on')
    for c in (pc, foe):
        _save(dm, cid, c, maxes={1: 2}, npc=(c == foe))
        dm.add_to_battle(cid, c)
        _act(dm, cid, c, 'spend', 1)
    assert dm.json(f'/campaigns/{cid}/api/battle/long-rest').status_code == 200
    assert _slots(pc) == [(1, 2, 0)] and _slots(foe) == [(1, 2, 1)]
    _act(dm, cid, pc, 'spend', 1)
    pid = [r for r in dm.battle(cid) if r['character_id'] == pc][0]['id']
    assert dm.json(f'/campaigns/{cid}/api/battle/long-rest', {'id': pid}).status_code == 200 and _slots(pc) == [(1, 2, 0)]


def test_the_character_page_shows_slots_with_buttons_for_those_who_may_and_spells_by_level(make_user):
    dm, player = make_user(), make_user()
    cid = dm.new_campaign()
    dm.add_member(cid, player)
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, maxes={1: 4, 3: 2}, spells=[('Fireball', '3', '8d6', 'fire', '150 ft'), ('Fire Bolt', '0', '1d10', 'fire', ''), ('Shield', '1', '', '', 'Reaction')])
    html = dm.get(f'/campaigns/{cid}/characters/{ch}').data.decode()
    assert 'data-slots' in html and 'data-can-edit="1"' in html and 'Level 3: 2 of 2 left' in html and 'js/spell-slots.js' in html
    assert html.index('Cantrips') < html.index('Level 1</h3>') < html.index('Level 3</h3>')
    assert 'data-cast-level="3"' in html and 'data-cast-level="1"' in html and 'data-cast-level="0"' not in html       # cantrips cost nothing
    assert 'data-roll-dice' in html and '8d6' in html
    seen = player.get(f'/campaigns/{cid}/characters/{ch}').data.decode()
    assert 'data-can-edit="0"' in seen and 'data-cast-level' not in seen and 'Fireball' in seen


def test_the_peek_and_the_library_show_the_spells(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, maxes={1: 2}, spells=[('Fireball', '3', '8d6', 'fire', '150 ft')])
    d = dm.get(f'/campaigns/{cid}/api/characters/{ch}/detail').get_json()
    assert d['slots'] == [{'level': 1, 'max': 2, 'used': 0}] and d['spells'][0]['name'] == 'Fireball' and d['spells'][0]['dice_json'] == '{"6": 8}'
    r = dm.post('/library/save', data={'campaign_id': cid, 'character_id': ch})           # keep a copy in Your characters
    lib = q('SELECT id FROM characters WHERE owner_id IS NOT NULL AND library_id IS NULL AND name = ? ORDER BY id DESC', 'Mira')
    assert lib, r.status_code
    assert _slots(lib[0]['id']) == [(1, 2, 0)] and _spells(lib[0]['id'])[0][0] == 'Fireball'          # the copy keeps both
    page = dm.get(f"/library/{lib[0]['id']}").data.decode()
    assert 'Spell slots' in page and 'Fireball' in page


def test_the_forms_offer_the_slots_and_the_spell_book(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, maxes={2: 3}, spells=[('Shield', '1', '', '', '')])
    for path, base in ((f'/campaigns/{cid}/characters/{ch}/edit', f'/campaigns/{cid}/api/reference/'), ('/library/new', '/api/reference/')):
        html = dm.get(path).data.decode()
        assert 'name="slots_present"' in html and 'name="slot_max_9"' in html and 'name="spells_present"' in html
        assert 'data-spellbook' in html, path
        assert 'Add from spell book' in html, path
        got = re.search(r'data-spellbook data-base="([^"]*)"', html).group(1)
        assert got.endswith('/api/reference/') and (got == '/api/reference/') == (base == '/api/reference/'), (path, got)
        assert 'js/character-spells.js' in html, path
    html = dm.get(f'/campaigns/{cid}/characters/{ch}/edit').data.decode()
    assert 'name="slot_max_2" value="3"' in html and 'value="Shield"' in html


def test_export_and_import_carry_slots_and_spells(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, maxes={1: 4, 2: 2}, spells=[('Fireball', '3', '8d6', 'fire', '150 ft'), ('Shield', '1', '', '', '')])
    _act(dm, cid, ch, 'spend', 1)
    cid2 = dm.new_campaign()
    r = dm.post(f'/campaigns/{cid2}/import/data', data={'import_file': (io.BytesIO(dm.get(f'/campaigns/{cid}/export/data').data), 'x.zip')},
                content_type='multipart/form-data')
    assert r.status_code == 302
    new = q('SELECT id FROM characters WHERE campaign_id = ? AND name = ?', cid2, 'Mira')[0]['id']
    assert _slots(new) == [(1, 4, 0), (2, 2, 0)]                     # the maximums travel; the copy starts rested
    assert _spells(new) == [('Fireball', 3, '8d6', 'fire', '150 ft'), ('Shield', 1, '', '', '')]


def test_the_whole_campaign_export_and_import_carry_them_too(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, maxes={5: 1}, spells=[('Cone of Cold', '5', '8d8', 'cold', '')])
    blob = dm.get(f'/campaigns/{cid}/export/campaign')
    assert blob.status_code == 200
    r = dm.post('/campaigns/import', data={'import_file': (io.BytesIO(blob.data), 'c.zip')}, content_type='multipart/form-data')
    assert r.status_code == 302, r.status_code
    new = q("SELECT id FROM characters WHERE name = 'Mira' ORDER BY id DESC")[0]['id']
    assert _slots(new) == [(5, 1, 0)] and _spells(new) == [('Cone of Cold', 5, '8d8', 'cold', '')]


def test_deleting_a_character_takes_its_slots_and_spells_with_it(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, maxes={1: 1}, spells=[('Shield', '1', '', '', '')])
    assert dm.post(f'/campaigns/{cid}/characters/{ch}/delete').status_code in (302, 200)
    assert _slots(ch) == [] and _spells(ch) == []
