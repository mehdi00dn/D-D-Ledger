#!/usr/bin/env python3
"""Delete stale objects from the TEMP bucket (parked exports, abandoned upload staging).

    python scripts/cleanup_temp.py --dry-run              # show what would go
    python scripts/cleanup_temp.py --older-than-hours 24  # delete them

Uses the same environment as the app (SUPABASE_URL + SUPABASE_SECRET_KEY, or local
storage).  Only the temp bucket is touched -- never the real uploads.  Run it now
and then (or from a scheduled job); files younger than the threshold are kept, so a
download or upload in progress is never disturbed.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from storage import TEMP, build_storage  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--older-than-hours', type=float, default=24)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    st = build_storage()
    cutoff = time.time() - args.older_than_hours * 3600
    objects = st.list_objects(TEMP)
    stale = [k for k, created in objects if created is not None and created < cutoff]
    print(f'{len(objects)} object(s) in the temp bucket, {len(stale)} older than {args.older_than_hours:g} h')
    for k in stale:
        print(('would delete  ' if args.dry_run else 'deleting  ') + k)
    if stale and not args.dry_run:
        st.delete_many(TEMP, stale)
    return 0


if __name__ == '__main__':
    sys.exit(main())
