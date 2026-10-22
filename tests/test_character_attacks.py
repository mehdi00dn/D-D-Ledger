"""DL-150: saved attacks.  A character keeps a short list of attacks (name, to-hit bonus, damage dice, type); the sheet
shows a roll button for each, and the numbers travel with export / import."""
import io
import json
import pytest
import dnd5e
from conftest import q, BACKEND

pytestmark = pytest.mark.skipif(BACKEND == 'sqlite', reason='postgres-only')


def _save(user, cid, char_id, attacks, present=True):
    data = {'name': 'Mira', 'level': '1', 'max_hp': '30', 'armor_class': '12'}
    if present:
        data['attacks_present'] = '1'
    data['attack_name'] = [a[0] for a in attacks]
    data['attack_bonus'] = [a[1] for a in attacks]
    data['attack_damage'] = [a[2] for a in attacks]
    data['attack_type'] = [a[3] for a in attacks]
    r = user.post(f'/campaigns/{cid}/characters/{char_id}/edit', data=data, content_type='multipart/form-data')
    assert r.status_code == 302, r.status_code


def _rows(char_id):
    return [(r['name'], r['attack_bonus'], r['damage'], r['damage_type'])
            for r in q('SELECT * FROM character_attacks WHERE character_id = ? ORDER BY position', char_id)]


def test_helpers():
    assert dnd5e.clean_damage('2d6 + 3') == '2d6+3' and dnd5e.clean_damage('1d7') == '' and dnd5e.clean_damage('31d6') == ''
    assert dnd5e.damage_roll_spec('2d6-1') == ({'6': 2}, -1) and dnd5e.damage_roll_spec('') is None
    rows = dnd5e.clean_attacks([('Bow', '99999', '1d8', 'piercing'), ('', '1', '1d4', ''), ('x', 'abc', 'bad', '')] + [('n', '0', '', '')] * 20)
    assert rows[0]['attack_bonus'] == 99 and rows[1]['damage'] == '' and len(rows) == dnd5e.MAX_ATTACKS


def test_attacks_are_saved_in_order_and_replaced(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, [('Longsword', '5', '1d8+3', 'slashing'), ('Dagger', '-1', '1D4 - 1', 'piercing')])
    assert _rows(ch) == [('Longsword', 5, '1d8+3', 'slashing'), ('Dagger', -1, '1d4-1', 'piercing')]
    _save(dm, cid, ch, [('Bow', '4', '1d6', '')])
    assert _rows(ch) == [('Bow', 4, '1d6', '')]


def test_a_form_without_the_attacks_card_leaves_them_alone(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, [('Bow', '4', '1d6', '')])
    _save(dm, cid, ch, [], present=False)
    assert len(_rows(ch)) == 1
    _save(dm, cid, ch, [])                          # the card shown and emptied does clear them
    assert _rows(ch) == []


def test_sheet_shows_roll_buttons(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, [('Longsword', '5', '2d6-1', 'slashing')])
    html = dm.get(f'/campaigns/{cid}/characters/{ch}').data.decode()
    assert 'Longsword' in html and 'data-roll-bonus="5"' in html
    assert 'data-roll-bonus="-1"' in html and '{&#34;6&#34;: 2}' in html or '{"6": 2}' in html


def test_export_import_roundtrip(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, [('Longsword', '5', '1d8+3', 'slashing')])
    cid2 = dm.new_campaign()
    r = dm.post(f'/campaigns/{cid2}/import/data', data={'import_file': (io.BytesIO(dm.get(f'/campaigns/{cid}/export/data').data), 'x.zip')},
                content_type='multipart/form-data')
    assert r.status_code == 302
    new_id = q('SELECT id FROM characters WHERE campaign_id = ? AND name = ?', cid2, 'Mira')[0]['id']
    assert _rows(new_id) == [('Longsword', 5, '1d8+3', 'slashing')]


def test_deleting_the_character_removes_attacks(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, [('Bow', '4', '1d6', '')])
    dm.post(f'/campaigns/{cid}/characters/{ch}/delete')
    assert _rows(ch) == []


def test_popup_detail_carries_attacks(make_user):
    dm = make_user(); cid = dm.new_campaign()
    ch = dm.new_character(cid, name='Mira')
    _save(dm, cid, ch, [('Longsword', '5', '1d8+3', 'slashing')])
    data = dm.get(f'/campaigns/{cid}/api/characters/{ch}/detail').get_json()
    a = data['attacks'][0]
    assert a['name'] == 'Longsword' and a['to_hit_text'] == '+5' and a['damage_bonus'] == 3 and a['dice_json'] == '{"8": 1}'


def test_note_preview_breaks_lines_where_the_editor_starts_a_new_one():
    import app
    raw = app.sanitize_notes_payload(json.dumps(['"Quote."<div>Second line.</div><div>Third.</div>']))
    assert app.notes_plain_preview(raw) == '"Quote."\nSecond line.\nThird.'
