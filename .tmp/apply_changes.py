from pathlib import Path
import re

ROOT = Path('.')

def edit(path, fn):
    p = ROOT / path
    s = p.read_text(encoding='utf-8')
    n = fn(s)
    if n == s:
        raise SystemExit(f'No change made to {path}; expected source text was not found')
    p.write_text(n, encoding='utf-8')

# Case-insensitive username registration/login.
edit('app.py', lambda s: s.replace(
    "SELECT id FROM users WHERE username = ?", "SELECT id FROM users WHERE lower(username) = lower(?)", 1
).replace(
    "SELECT * FROM users WHERE username = ?", "SELECT * FROM users WHERE lower(username) = lower(?)", 1
))

# Replace campaign member invite endpoint and add owner-only autocomplete endpoint.
def app_members(s):
    start = s.index("@app.route('/campaigns/<int:campaign_id>/members/add', methods=['POST'])")
    end = s.index("\n\n@app.route('/campaigns/<int:campaign_id>/members/<int:user_id>/status'", start)
    block = '''@app.route('/campaigns/<int:campaign_id>/members/add', methods=['POST'])\n@campaign_access_required\ndef campaign_member_add():\n    if g.campaign_role != 'owner':\n        abort(403)\n    data = request.get_json(silent=True) or request.form\n    username = (data.get('username') or '').strip()\n    status = data.get('status') if data.get('status') in ('dm', 'player') else 'player'\n    db = get_db()\n    if not username:\n        db.close()\n        return _reject_request('Enter a username to invite.', 400)\n    user = db.execute('SELECT id, username FROM users WHERE lower(username) = lower(?)', (username,)).fetchone()\n    if not user:\n        db.close()\n        return _reject_request('No user found with that username.', 404)\n    if user['id'] == session['user_id']:\n        db.close()\n        return _reject_request(\"You're already the owner of this campaign.\", 400)\n    existing = db.execute('SELECT id FROM campaign_members WHERE campaign_id = ? AND user_id = ?',\n                           (g.campaign_id, user['id'])).fetchone()\n    if existing:\n        db.close()\n        return _reject_request('%s is already a member of this campaign.' % user['username'], 400)\n    db.execute('INSERT INTO campaign_members (campaign_id, user_id, role, status) VALUES (?, ?, ?, ?)',\n               (g.campaign_id, user['id'], 'member', status))\n    db.commit()\n    db.close()\n    if _wants_json():\n        return jsonify({'ok': True, 'username': user['username'], 'status': status, 'user_id': user['id']})\n    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))\n\n\n@app.route('/campaigns/<int:campaign_id>/api/members/search')\n@campaign_access_required\ndef api_member_search():\n    if g.campaign_role != 'owner':\n        abort(403)\n    q = request.args.get('q', '').strip()\n    if len(q) < 2:\n        return jsonify({'results': []})\n    db = get_db()\n    secret = app.config['SECRET_KEY']\n    retry = security.member_search_blocked_for(db, secret, session['user_id'], g.campaign_id)\n    if retry:\n        db.close()\n        return jsonify({'error': 'Searching too fast -- try again in a moment.'}), 429\n    security.record_member_search(db, secret, session['user_id'], g.campaign_id)\n    db.commit()\n    like = q.replace('\\\\', '\\\\\\\\').replace('%', r'\\%').replace('_', r'\\_') + '%'\n    rows = db.execute('''\n        SELECT username FROM users\n        WHERE username ILIKE ? ESCAPE '\\\\'\n          AND id != ?\n          AND id NOT IN (SELECT user_id FROM campaign_members WHERE campaign_id = ?)\n        ORDER BY username ASC LIMIT 8\n    ''', (like, session['user_id'], g.campaign_id)).fetchall()\n    db.close()\n    return jsonify({'results': [r['username'] for r in rows]})\n'''
    return s[:start] + block + s[end:]
edit('app.py', app_members)

# Rate-limit autocomplete requests per user/campaign.
def security_edit(s):
    s = s.replace("    'register-ip': (int(os.environ.get('REGISTER_LIMIT_IP', 20)), 60 * 60),\n", "    'register-ip': (int(os.environ.get('REGISTER_LIMIT_IP', 20)), 60 * 60),\n    'member-search': (int(os.environ.get('MEMBER_SEARCH_LIMIT', 30)), 60),\n")
    s += '''\n\ndef member_search_blocked_for(db, secret, user_id, campaign_id):\n    return _retry_after(db, 'member-search', _key(secret, 'msearch', '%s|%s' % (user_id, campaign_id)))\n\n\ndef record_member_search(db, secret, user_id, campaign_id):\n    db.execute('INSERT INTO login_attempts (kind, key) VALUES (?, ?)',\n               ('member-search', _key(secret, 'msearch', '%s|%s' % (user_id, campaign_id))))\n    _maybe_cleanup(db)\n'''
    return s
edit('security.py', security_edit)

# Migration for case-insensitive uniqueness.
mig = ROOT / 'migrations/0005_case_insensitive_usernames.sql'
mig.parent.mkdir(exist_ok=True)
if not mig.exists():
    mig.write_text('''-- Make usernames unique case-insensitively while preserving display case.\nDO $$\nDECLARE conflict RECORD;\nBEGIN\n    SELECT lower(username) AS lu, array_agg(username ORDER BY id) AS variants\n      INTO conflict FROM users GROUP BY lower(username) HAVING count(*) > 1 LIMIT 1;\n    IF FOUND THEN\n        RAISE EXCEPTION 'Cannot make usernames case-insensitive: % already exist as %. Rename one of them, then re-run this migration.',\n            array_length(conflict.variants, 1), conflict.variants;\n    END IF;\nEND $$;\n\nCREATE UNIQUE INDEX idx_users_username_ci ON users (lower(username));\n''', encoding='utf-8')

# Mirror migration in the all-in-one Supabase setup script.
def supa(s):
    if "idx_users_username_ci" in s:
        return s
    marker = "COMMIT;"
    ins = '''-- ---- 0005_case_insensitive_usernames.sql ----\nDO $$\nDECLARE conflict RECORD;\nBEGIN\n    SELECT lower(username) AS lu, array_agg(username ORDER BY id) AS variants\n      INTO conflict FROM users GROUP BY lower(username) HAVING count(*) > 1 LIMIT 1;\n    IF FOUND THEN\n        RAISE EXCEPTION 'Cannot make usernames case-insensitive: % already exist as %. Rename one of them, then re-run this migration.',\n            array_length(conflict.variants, 1), conflict.variants;\n    END IF;\nEND $$;\nCREATE UNIQUE INDEX idx_users_username_ci ON users (lower(username));\nINSERT INTO schema_migrations (version) VALUES ('0005_case_insensitive_usernames.sql');\n\n'''
    return s.replace(marker, ins + marker, 1)
edit('supabase_setup.sql', supa)

# Campaign form: member search/autocomplete and inline feedback.
def template_edit(s):
    s = s.replace('<div class="member-list">', '<div class="member-list" id="member-list">', 1)
    s = s.replace('<div class="member-row">', '<div class="member-row" data-member-row data-user-id="{{ m.id }}">', 1)
    s = s.replace('{{ icon(\'user\') }} {{ m.username }}', '{{ icon(\'user\') }} <span class="member-name-text">{{ m.username }}</span>', 1)
    old = '''<form method="POST" action="{{ url_for('campaign_member_add', campaign_id=campaign.id) }}" class="member-add-form">{{ csrf_field() }}\n      <input type="text" name="username" placeholder="Username to invite" required>'''
    new = '''<form method="POST" action="{{ url_for('campaign_member_add', campaign_id=campaign.id) }}" class="member-add-form" id="member-add-form" autocomplete="off">{{ csrf_field() }}\n      <div class="member-search">\n        <input type="text" name="username" id="member-username-input" placeholder="Username to invite" required autocomplete="off">\n        <ul class="member-search-results" id="member-search-results" hidden></ul>\n      </div>'''
    if old not in s:
        raise SystemExit('member invite form not found')
    s = s.replace(old, new, 1)
    anchor = '    </form>\n    {% endif %}\n\n    <div class="leave-campaign-row">'
    s = s.replace(anchor, '    </form>\n    <div class="member-add-feedback" id="member-add-feedback" hidden></div>\n    {% endif %}\n\n    <div class="leave-campaign-row">', 1)
    css = '''\n<style>\n.member-search { position: relative; flex: 1; min-width: 140px; }\n.member-search input[type="text"] { width: 100%; min-width: 0; }\n.member-search-results { position:absolute; left:0; right:0; top:calc(100% + 4px); z-index:20; margin:0; padding:4px; list-style:none; background:var(--bg-panel-raised); border:1px solid var(--hairline-strong); border-radius:var(--radius-s); box-shadow:var(--shadow-deep); max-height:220px; overflow-y:auto; }\n.member-search-results[hidden] { display:none; }\n.member-search-results li { padding:7px 10px; border-radius:var(--radius-s); font-size:13.5px; cursor:pointer; color:var(--parchment); }\n.member-search-results li:hover,.member-search-results li.is-active { background:rgba(201,162,75,.16); }\n.member-search-results li.is-empty,.member-search-results li.is-note { cursor:default; color:var(--parchment-dim); font-size:12.5px; }\n.member-add-feedback { margin-top:10px; padding:8px 12px; border-radius:var(--radius-s); font-size:13px; border:1px solid transparent; }\n.member-add-feedback[hidden] { display:none; }\n.member-add-feedback.is-success { background:rgba(30,154,82,.12); border-color:rgba(30,154,82,.35); color:var(--moss-bright); }\n.member-add-feedback.is-error { background:rgba(226,80,72,.12); border-color:rgba(226,80,72,.35); color:var(--blood-bright); }\n</style>\n'''
    js = r'''\n<script>\n(function () {\n  var form=document.getElementById('member-add-form'); if(!form) return;\n  var input=document.getElementById('member-username-input'), list=document.getElementById('member-search-results'), feedback=document.getElementById('member-add-feedback');\n  var memberList=document.getElementById('member-list'), statusSel=form.querySelector('select[name="status"]'), timer=null;\n  var campaignId={{ campaign.id|tojson }};\n  function esc(s){return s.replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}\n  function close(){list.hidden=true;list.innerHTML='';}\n  function feedbackMsg(ok,msg){feedback.textContent=msg;feedback.className='member-add-feedback '+(ok?'is-success':'is-error');feedback.hidden=false;}\n  function csrf(){var x=form.querySelector('input[name="csrf_token"]');return x?x.value:'';}\n  function render(items){if(!items.length){list.innerHTML='<li class="is-empty">No matching users</li>';list.hidden=false;return;} list.innerHTML=items.map(function(n){return '<li role="option" data-name="'+esc(n)+'">'+esc(n)+'</li>';}).join('');list.hidden=false;}\n  input.addEventListener('input',function(){var q=input.value.trim(); if(timer) clearTimeout(timer); if(q.length<2){close();return;} timer=setTimeout(function(){fetch('/campaigns/'+campaignId+'/api/members/search?q='+encodeURIComponent(q),{credentials:'same-origin'}).then(function(r){return r.json().then(function(j){return {r:r,j:j};});}).then(function(x){if(x.r.status===429){feedbackMsg(false,x.j.error||'Searching too fast.');close();return;}render(x.j.results||[]);}).catch(function(){close();});},180);});\n  list.addEventListener('mousedown',function(e){var li=e.target.closest('li[role="option"]');if(li){input.value=li.dataset.name;close();input.focus();}});\n  input.addEventListener('keydown',function(e){var items=list.querySelectorAll('li[role="option"]');if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();if(!items.length)return;var active=Array.prototype.indexOf.call(items, list.querySelector('.is-active'));active=(active+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;items.forEach(function(x,i){x.classList.toggle('is-active',i===active);});}else if(e.key==='Enter'&&list.querySelector('.is-active')){e.preventDefault();input.value=list.querySelector('.is-active').dataset.name;close();}});\n  document.addEventListener('click',function(e){if(!form.contains(e.target))close();});\n  form.addEventListener('submit',function(e){e.preventDefault();close();var username=input.value.trim();if(!username)return;var btn=form.querySelector('button[type="submit"]');btn.disabled=true;fetch(form.action,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf(),'Accept':'application/json'},body:JSON.stringify({username:username,status:statusSel.value})}).then(function(r){return r.json().then(function(j){return {r:r,j:j};});}).then(function(x){btn.disabled=false;if(!x.r.ok){feedbackMsg(false,x.j.error||'Could not add that member.');return;}feedbackMsg(true,'Added '+x.j.username+' as '+(x.j.status==='dm'?'DM':'Player')+'.');var row=document.createElement('div');row.className='member-row';row.dataset.memberRow='';row.dataset.userId=x.j.user_id;row.innerHTML='<span class="member-name">'+esc(x.j.username)+' <span class="role-badge">'+(x.j.status==='dm'?'DM':'Player')+'</span></span>';memberList.appendChild(row);form.reset();}).catch(function(){btn.disabled=false;feedbackMsg(false,'Something went wrong. Please try again.');});});\n})();\n</script>\n'''
    s = s.replace('{% endblock %}', css + js + '{% endblock %}', 1)
    return s
edit('templates/campaign_form.html', template_edit)

# Remove temporary staging files and workflow after the real changes are committed.
for p in [ROOT/'.tmp/apply_changes.py', ROOT/'.github/workflows/apply-session-changes.yml']:
    if p.exists(): p.unlink()
for p in (ROOT/'.tmp').glob('session-changes.patch.part*'):
    p.unlink()
