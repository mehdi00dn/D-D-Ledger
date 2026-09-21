"""Safe reading of campaign-export ZIP files.

The old importer trusted the archive completely.  This module bounds everything an
attacker (or a corrupted file) could inflate, and turns the manifest into clean,
typed data so the database layer never sees a surprise:

  * archive size, entry count, total uncompressed size and per-entry size are capped
    (and reads are bounded even when the ZIP header lies about sizes -> no zip bombs)
  * only  manifest.json  and  images/<avatars|sheets>/<name>.<image ext>  are read;
    every other name (path traversal, absolute paths, nested archives) is ignored
  * every field is coerced to the right type/range, strings are length-capped,
    NUL bytes removed, colours validated, image references whitelisted
  * notes are passed through the caller's HTML sanitiser
"""
import io
import json
import re
import zipfile
import zlib
import zlib

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_ENTRIES = 3000
MAX_TOTAL_UNCOMPRESSED = 300 * 1024 * 1024
MAX_ENTRY_BYTES = 40 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_GROUPS = 200
MAX_CHARACTERS = 1000
MAX_SHEETS_PER_CHARACTER = 20

_IMAGE_REF = re.compile(r'^(avatars|sheets)/[A-Za-z0-9_\-]{1,80}\.(png|jpe?g|gif|webp)$', re.IGNORECASE)
_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')


class ImportRejected(ValueError):
    """The archive is unusable; the message is safe to show."""


def _clean(text, limit):
    if text is None:
        return ''
    if not isinstance(text, str):
        text = str(text)
    return text.replace('\x00', '')[:limit]


def _int(value, default, lo, hi):
    try:
        if isinstance(value, bool):
            value = int(value)
        n = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lo, min(hi, n))


def _flag(value):
    """Truthiness for checkbox-like fields: 1/true/'yes'/'on' are true, everything else false."""
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    try:
        return bool(value) and float(value) != 0
    except (TypeError, ValueError):
        return False


def _flag(value):
    """0/1 from bools, numbers, or common truthy strings ('yes', 'true', '1', 'on')."""
    if isinstance(value, str):
        return 1 if value.strip().lower() in ('1', 'true', 'yes', 'on', 'y') else 0
    return 1 if _int(value, 0, 0, 1) else 0


def _image_ref(value):
    return value if isinstance(value, str) and _IMAGE_REF.match(value) else None


def open_zip(data):
    """Validate the archive envelope and return (ZipFile, raw manifest dict)."""
    if len(data) > MAX_ZIP_BYTES:
        raise ImportRejected('The file is too large (limit %d MB).' % (MAX_ZIP_BYTES // (1024 * 1024)))
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except zipfile.BadZipFile:
        raise ImportRejected('This is not a valid export file.')
    if len(infos) > MAX_ENTRIES:
        raise ImportRejected('The archive contains too many files.')
    total = 0
    for info in infos:
        if info.file_size > MAX_ENTRY_BYTES:
            raise ImportRejected('The archive contains an oversized file.')
        total += info.file_size
    if total > MAX_TOTAL_UNCOMPRESSED:
        raise ImportRejected('The archive expands to too much data.')
    raw = read_member(zf, 'manifest.json', MAX_MANIFEST_BYTES)
    if raw is None:
        raise ImportRejected('manifest.json is missing - this is not a Campaign Ledger export.')
    try:
        manifest = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ImportRejected('manifest.json is not valid.')
    if not isinstance(manifest, dict):
        raise ImportRejected('manifest.json is not valid.')
    return zf, manifest


def read_member(zf, name, limit):
    """Read one entry with a HARD byte cap (header sizes can be forged).  Any way an
    entry can be corrupt, encrypted or use an unsupported method becomes ImportRejected."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    try:
        with zf.open(info) as fh:
            data = fh.read(limit + 1)
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, OSError):
        raise ImportRejected('The archive is damaged or uses an unsupported format.')
    if len(data) > limit:
        raise ImportRejected('The archive contains an oversized file.')
    return data


def normalize_manifest(manifest, sanitize_notes):
    """Return {'groups': [...], 'characters': [...]} with every field bounded and typed."""
    groups, characters = [], []
    for grp in (manifest.get('groups') if isinstance(manifest.get('groups'), list) else [])[:MAX_GROUPS]:
        if not isinstance(grp, dict):
            continue
        color = grp.get('color')
        groups.append({
            'name': _clean(grp.get('name'), 120).strip() or 'Unnamed Group',
            'bio': _clean(grp.get('bio'), 20000),
            'color': color if isinstance(color, str) and _COLOR.match(color) else '#c9a24b',
            'avatar_file': _image_ref(grp.get('avatar_file')),
        })
    for c in (manifest.get('characters') if isinstance(manifest.get('characters'), list) else [])[:MAX_CHARACTERS]:
        if not isinstance(c, dict):
            continue
        sheets = c.get('sheet_files') if isinstance(c.get('sheet_files'), list) else []
        characters.append({
            'name': _clean(c.get('name'), 120).strip() or 'Unnamed',
            'is_npc': 1 if _flag(c.get('is_npc')) else 0,
            'level': _int(c.get('level'), 1, 0, 100),
            'max_hp': _int(c.get('max_hp'), 10, 0, 100000),
            'str_score': _int(c.get('str_score'), 10, 0, 100),
            'dex_score': _int(c.get('dex_score'), 10, 0, 100),
            'con_score': _int(c.get('con_score'), 10, 0, 100),
            'int_score': _int(c.get('int_score'), 10, 0, 100),
            'wis_score': _int(c.get('wis_score'), 10, 0, 100),
            'cha_score': _int(c.get('cha_score'), 10, 0, 100),
            'armor_class': _int(c.get('armor_class'), 10, 0, 100),
            'notes': sanitize_notes(_clean(c.get('notes'), 200000)),
            'group_name': _clean(c.get('group_name'), 120).strip() or None,
            'avatar_file': _image_ref(c.get('avatar_file')),
            'sheet_files': [r for r in (_image_ref(s) for s in sheets[:MAX_SHEETS_PER_CHARACTER]) if r],
        })
    return {'groups': groups, 'characters': characters}
