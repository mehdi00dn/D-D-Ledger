"""Short public ids in page addresses (DL-109).

Campaigns, characters, maps, journal recaps and quests keep their integer primary keys everywhere inside the app (queries,
foreign keys, exports).  Only the address changes: /campaigns/3f9a0c7be214 instead of /campaigns/1.  This works through
Flask URL converters, so no route function and no url_for() call had to change:

* matching an address: a 12-character code is looked up and turned into the integer; a plain number still works (old
  links, bookmarks, scripts) and is flagged so that a browser navigation is redirected to the short form;
* building an address: url_for(..., campaign_id=1) gives the short code, found once per process and remembered.

The code is written by the database (gen_short_id(), migration 0056) so every insert gets one without app changes.
Knowing a code gives no access: every route still runs the same membership checks on the integer it resolves to.
"""
import re
from flask import g, has_request_context, request, redirect, url_for
from werkzeug.routing import BaseConverter, ValidationError

from database import get_db

CODE_RE = r'[0-9a-f]{12}'
_WINDOW = 200                       # on a cache miss, remember the neighbouring ids too (one query fills a whole list page)

_codes = {}                         # table -> {id: code}
_ids = {}                           # table -> {code: id}


def clear_cache():
    """Forget what was learned (tests that reset the database; ids restart from 1 there)."""
    _codes.clear()
    _ids.clear()


def _remember(table, rows):
    codes, ids = _codes.setdefault(table, {}), _ids.setdefault(table, {})
    for r in rows:
        codes[r['id']] = r['short_id']
        ids[r['short_id']] = r['id']


def code_for(table, pk):
    """The short code of one row, or None when the row does not exist."""
    pk = int(pk)
    hit = _codes.get(table, {}).get(pk)
    if hit:
        return hit
    db = get_db()
    try:
        rows = db.execute(f'SELECT id, short_id FROM {table} WHERE id BETWEEN ? AND ?', (pk - _WINDOW, pk + _WINDOW)).fetchall()
    finally:
        db.close()
    _remember(table, rows)
    return _codes.get(table, {}).get(pk)


def id_for(table, code):
    """The integer key behind a short code, or None."""
    hit = _ids.get(table, {}).get(code)
    if hit:
        return hit
    db = get_db()
    try:
        row = db.execute(f'SELECT id, short_id FROM {table} WHERE short_id = ?', (code,)).fetchone()
    finally:
        db.close()
    if row:
        _remember(table, [row])
        return row['id']
    return None


class _Converter(BaseConverter):
    table = None
    regex = CODE_RE + r'|[0-9]{1,18}'

    def to_python(self, value):
        if value.isdigit():
            g.legacy_id_in_url = True              # an old numeric address: the browser is sent to the short one
            return int(value)
        pk = id_for(self.table, value)
        if pk is None:
            raise ValidationError()
        return pk

    def to_url(self, value):
        code = code_for(self.table, value)
        return code or str(int(value))             # a row that is gone: keep the number, the route answers 404


def _make(name, table):
    return type(name, (_Converter,), {'table': table})


CONVERTERS = {
    'campaign': _make('CampaignConverter', 'campaigns'),
    'character': _make('CharacterConverter', 'characters'),
    'gamemap': _make('MapConverter', 'maps'),
    'journal': _make('JournalConverter', 'session_journal'),
    'quest': _make('QuestConverter', 'quests'),
}

# route variable name -> converter name
VARIABLES = {'campaign_id': 'campaign', 'char_id': 'character', 'map_id': 'gamemap', 'journal_id': 'journal', 'quest_id': 'quest'}


def init_app(app):
    for name, cls in CONVERTERS.items():
        app.url_map.converters[name] = cls

    @app.after_request
    def _send_old_addresses_to_the_short_form(resp):
        """A browser that opened /campaigns/1/... lands on /campaigns/<code>/... once the page itself was allowed.
        Done after the view ran (not before) so a number never reveals a code to someone who may not open the page;
        script and fetch calls (no text/html in Accept) keep working on the number untouched."""
        if (not g.get('legacy_id_in_url') or resp.status_code != 200 or request.method != 'GET'
                or request.endpoint in (None, 'static') or request.view_args is None
                or request.accept_mimetypes.best != 'text/html'):
            return resp
        target = url_for(request.endpoint, **request.view_args)
        if request.query_string:
            target += '?' + request.query_string.decode('latin-1')
        if target == request.full_path.rstrip('?'):
            return resp
        return redirect(target, 302)
