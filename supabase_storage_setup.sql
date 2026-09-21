-- Storage buckets for Campaign Ledger (Supabase SQL editor).  Idempotent: safe to re-run.
--   ledger-uploads  final, validated images     (private; the app serves them behind login)
--   ledger-temp     short-lived staging: direct browser uploads, import ZIPs, large exports
-- Both are PRIVATE.  Supabase enforces the size/type limits itself, on top of the app's checks.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types) values
  ('ledger-uploads', 'ledger-uploads', false, 31457280,
     array['image/png','image/jpeg','image/gif','image/webp']),
  ('ledger-temp', 'ledger-temp', false, 52428800,
     array['image/png','image/jpeg','image/gif','image/webp','application/zip','application/x-zip-compressed','application/octet-stream'])
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;
