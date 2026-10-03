#!/usr/bin/env python3
"""One-time conversion of images that were stored before everything became WebP (DL-68).

    python scripts/optimize_stored_images.py            # DRY RUN: report what would change
    python scripts/optimize_stored_images.py --apply    # do it

Uses the same environment as the app (DATABASE_URL + SUPABASE_URL / SUPABASE_SECRET_KEY, or the
local equivalents).  For every stored PNG / JPEG / GIF image it:
  1. re-encodes it with the same pipeline new uploads use (WebP, per-kind size cap),
  2. keeps the result only if it is actually smaller,
  3. stores it under a new key, points the database row at it, and only then deletes the old file.

Safe by design:
  * a map's pixel size is never changed (pins, drawings and fog are positioned in map pixels),
    so maps are only re-compressed, never shrunk;
  * animated GIFs and images that are already WebP are left alone;
  * an image that cannot be read or converted is reported and skipped, never deleted;
  * the old file is removed only after the database row has been updated and committed.
Safe to run again: already-converted images are skipped.
"""
import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import images  # noqa: E402
from database import get_db  # noqa: E402
from storage import UPLOADS, StorageError, build_storage  # noqa: E402

# (table, column, upload folder, has image_width/image_height to keep in step)
TARGETS = [
    ('campaigns', 'avatar_path', 'avatars', False),
    ('groups', 'avatar_path', 'avatars', False),
    ('characters', 'avatar_path', 'avatars', False),
    ('character_sheets', 'image_path', 'sheets', False),
    ('maps', 'image_path', 'maps', True),
]


def _mb(n):
    return '%.1f MB' % (n / 1048576)


def convert_all(db, storage, apply=False, out=print):
    """Returns (converted, skipped, bytes_before, bytes_after)."""
    converted = skipped = before = after = 0
    for table, column, folder, is_map in TARGETS:
        cols = f'id, {column} AS path' + (', image_width AS w, image_height AS h' if is_map else '')
        rows = db.execute(f"SELECT {cols} FROM {table} WHERE {column} IS NOT NULL AND {column} <> ''").fetchall()
        for row in rows:
            old = row['path']
            if old.lower().endswith('.webp'):
                continue
            try:
                data = storage.get(UPLOADS, old)
            except StorageError as exc:
                out(f'  skip {table}#{row["id"]} {old}: {exc}')
                skipped += 1
                continue
            if data is None:
                out(f'  skip {table}#{row["id"]} {old}: file missing in storage')
                skipped += 1
                continue
            try:
                # Maps keep their exact pixel size: the cap is their current longest edge.
                cap = max(row['w'] or 0, row['h'] or 0) if is_map and row['w'] and row['h'] else None
                p = images.process_image(data, folder, cap_dimension=cap)
            except images.ImageError as exc:
                out(f'  skip {table}#{row["id"]} {old}: {exc}')
                skipped += 1
                continue
            if p.ext != 'webp':                            # animated: left as it is
                continue
            if is_map and row['w'] and (p.width, p.height) != (row['w'], row['h']):
                out(f'  skip {table}#{row["id"]} {old}: size would change')
                skipped += 1
                continue
            if len(p.data) >= len(data):
                out(f'  keep {table}#{row["id"]} {old}: WebP is not smaller ({_mb(len(data))})')
                continue
            out(f'  {"convert" if apply else "would convert"} {table}#{row["id"]} {old}: {_mb(len(data))} -> {_mb(len(p.data))}')
            converted += 1
            before += len(data)
            after += len(p.data)
            if not apply:
                continue
            new = f'{folder}/{uuid.uuid4().hex}.webp'
            storage.put(UPLOADS, new, p.data, p.content_type)
            try:
                db.execute(f'UPDATE {table} SET {column} = ? WHERE id = ? AND {column} = ?', (new, row['id'], old))
                db.commit()
            except Exception:
                db.rollback()
                storage.delete(UPLOADS, new)               # leave everything as it was
                raise
            if not _still_used(db, old):
                try:
                    storage.delete(UPLOADS, old)
                except StorageError as exc:
                    out(f'  note: could not delete old file {old}: {exc}')
    return converted, skipped, before, after


def _still_used(db, key):
    for table, column, _f, _m in TARGETS:
        if db.execute(f'SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1', (key,)).fetchone():
            return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', action='store_true', help='actually convert (default is a dry run)')
    args = ap.parse_args(argv)
    db = get_db()
    try:
        converted, skipped, before, after = convert_all(db, build_storage(), apply=args.apply)
    finally:
        db.close()
    verb = 'converted' if args.apply else 'would convert'
    print(f'{verb} {converted} image(s), {_mb(before)} -> {_mb(after)} (saves {_mb(before - after)}); skipped {skipped}')
    if not args.apply and converted:
        print('This was a dry run. Run again with --apply to do it.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
