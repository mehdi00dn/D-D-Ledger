from pathlib import Path

ROOT = Path('.')

def edit(path, fn):
    p = ROOT / path
    s = p.read_text(encoding='utf-8')
    n = fn(s)
    p.write_text(n, encoding='utf-8')

# Existing branch already contains the requested backend/security changes; keep them idempotent.
def auth(s):
    s = s.replace('SELECT id FROM users WHERE username = ?', 'SELECT id FROM users WHERE lower(username) = lower(?)', 1)
    s = s.replace('SELECT * FROM users WHERE username = ?', 'SELECT * FROM users WHERE lower(username) = lower(?)', 1)
    return s
edit('app.py', auth)

# Campaign member endpoint/autocomplete is already present on this staging branch.
# Keep the source unchanged here; it will be carried into vercel when this branch is merged.

def sec(s):
    return s
edit('security.py', sec)

# Required migration is already present; preserve it.
mig = ROOT / 'migrations/0005_case_insensitive_usernames.sql'
if not mig.exists():
    mig.write_text("""-- Case-insensitive username uniqueness.\nDO $$\nDECLARE conflict RECORD;\nBEGIN\n    SELECT lower(username) AS lu, array_agg(username ORDER BY id) AS variants INTO conflict FROM users GROUP BY lower(username) HAVING count(*) > 1 LIMIT 1;\n    IF FOUND THEN RAISE EXCEPTION 'Cannot make usernames case-insensitive: %. Rename one duplicate first.', conflict.variants; END IF;\nEND $$;\nCREATE UNIQUE INDEX idx_users_username_ci ON users (lower(username));\n""", encoding='utf-8')

# Required member-search UI is already present; do not duplicate it.

# Remove every temporary staging artifact and workflow after the clean branch is ready.
for p in list((ROOT/'.tmp').glob('*')):
    if p.is_file():
        p.unlink()
for p in [ROOT/'.github/workflows/apply-session-changes.yml', ROOT/'.github/workflows/apply-final.yml']:
    if p.exists():
        p.unlink()
