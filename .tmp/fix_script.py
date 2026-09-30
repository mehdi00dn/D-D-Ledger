from pathlib import Path

ROOT = Path('.')

def edit(path, fn):
    p = ROOT / path
    s = p.read_text(encoding='utf-8')
    n = fn(s)
    if n == s:
        raise SystemExit(f'No change made to {path}')
    p.write_text(n, encoding='utf-8')

# Case-insensitive username lookup.
def auth(s):
    s = s.replace('SELECT id FROM users WHERE username = ?', 'SELECT id FROM users WHERE lower(username) = lower(?)', 1)
    s = s.replace('SELECT * FROM users WHERE username = ?', 'SELECT * FROM users WHERE lower(username) = lower(?)', 1)
    return s
edit('app.py', auth)

# Campaign member invite + owner-only autocomplete.
def members(s):
    start = s.index("@app.route('/campaigns/<int:campaign_id>/members/add', methods=['POST'])")
    end = s.index("\n\n@app.route('/campaigns/<int:campaign_id>/members/<int:user_id>/status'", start)
    lines = [
        "@app.route('/campaigns/<int:campaign_id>/members/add', methods=['POST'])",
        '@campaign_access_required',
        'def campaign_member_add():',
        "    if g.campaign_role != 'owner':", "        abort(403)",
        '    data = request.get_json(silent=True) or request.form',
        "    username = (data.get('username') or '').strip()",
        "    status = data.get('status') if data.get('status') in ('dm', 'player') else 'player'",
        '    db = get_db()',
        '    if not username:', '        db.close()', "        return _reject_request('Enter a username to invite.', 400)",
        "    user = db.execute('SELECT id, username FROM users WHERE lower(username) = lower(?)', (username,)).fetchone()",
        '    if not user:', '        db.close()', "        return _reject_request('No user found with that username.', 404)",
        "    if user['id'] == session['user_id']:", '        db.close()', "        return _reject_request(\"You're already the owner of this campaign.\", 400)",
        "    existing = db.execute('SELECT id FROM campaign_members WHERE campaign_id = ? AND user_id = ?', (g.campaign_id, user['id'])).fetchone()",
        '    if existing:', '        db.close()', "        return _reject_request('%s is already a member of this campaign.' % user['username'], 400)",
        "    db.execute('INSERT INTO campaign_members (campaign_id, user_id, role, status) VALUES (?, ?, ?, ?)', (g.campaign_id, user['id'], 'member', status))",
        '    db.commit()', '    db.close()',
        "    if _wants_json():", "        return jsonify({'ok': True, 'username': user['username'], 'status': status, 'user_id': user['id']})",
        '    return redirect(url_for(\'campaign_edit\', campaign_id=g.campaign_id))',
        '', '',
        "@app.route('/campaigns/<int:campaign_id>/api/members/search')",
        '@campaign_access_required',
        'def api_member_search():',
        "    if g.campaign_role != 'owner':", '        abort(403)',
        "    q = request.args.get('q', '').strip()", '    if len(q) < 2:', "        return jsonify({'results': []})",
        '    db = get_db()', "    secret = app.config['SECRET_KEY']",
        "    retry = security.member_search_blocked_for(db, secret, session['user_id'], g.campaign_id)",
        '    if retry:', '        db.close()', "        return jsonify({'error': 'Searching too fast -- try again in a moment.'}), 429",
        "    security.record_member_search(db, secret, session['user_id'], g.campaign_id)", '    db.commit()',
        "    like = q.replace('\\\\', '\\\\\\\\').replace('%', r'\\%').replace('_', r'\\_') + '%'",
        "    rows = db.execute(\"SELECT username FROM users WHERE username ILIKE ? ESCAPE '\\\\' AND id != ? AND id NOT IN (SELECT user_id FROM campaign_members WHERE campaign_id = ?) ORDER BY username ASC LIMIT 8\", (like, session['user_id'], g.campaign_id)).fetchall()",
        '    db.close()', "    return jsonify({'results': [r['username'] for r in rows]})"
    ]
    return s[:start] + '\n'.join(lines) + s[end:]
edit('app.py', members)

# Search rate limit helpers.
def sec(s):
    if "'member-search'" not in s:
        s = s.replace("    'register-ip': (int(os.environ.get('REGISTER_LIMIT_IP', 20)), 60 * 60),", "    'register-ip': (int(os.environ.get('REGISTER_LIMIT_IP', 20)), 60 * 60),\n    'member-search': (int(os.environ.get('MEMBER_SEARCH_LIMIT', 30)), 60),", 1)
        s += "\n\ndef member_search_blocked_for(db, secret, user_id, campaign_id):\n    return _retry_after(db, 'member-search', _key(secret, 'msearch', '%s|%s' % (user_id, campaign_id)))\n\ndef record_member_search(db, secret, user_id, campaign_id):\n    db.execute('INSERT INTO login_attempts (kind, key) VALUES (?, ?)', ('member-search', _key(secret, 'msearch', '%s|%s' % (user_id, campaign_id))))\n    _maybe_cleanup(db)\n"
    return s
edit('security.py', sec)

# Migration.
mig = ROOT / 'migrations/0005_case_insensitive_usernames.sql'
mig.write_text("""-- Case-insensitive username uniqueness.\nDO $$\nDECLARE conflict RECORD;\nBEGIN\n    SELECT lower(username) AS lu, array_agg(username ORDER BY id) AS variants\n      INTO conflict FROM users GROUP BY lower(username) HAVING count(*) > 1 LIMIT 1;\n    IF FOUND THEN\n        RAISE EXCEPTION 'Cannot make usernames case-insensitive: %. Rename one duplicate first.', conflict.variants;\n    END IF;\nEND $$;\nCREATE UNIQUE INDEX idx_users_username_ci ON users (lower(username));\n""", encoding='utf-8')

# Supabase setup mirror.
def supa(s):
    if 'idx_users_username_ci' in s:
        return s
    marker = 'COMMIT;'
    ins = """-- 0005_case_insensitive_usernames.sql\nDO $$\nDECLARE conflict RECORD;\nBEGIN\n    SELECT lower(username) AS lu, array_agg(username ORDER BY id) AS variants\n      INTO conflict FROM users GROUP BY lower(username) HAVING count(*) > 1 LIMIT 1;\n    IF FOUND THEN\n        RAISE EXCEPTION 'Cannot make usernames case-insensitive: %. Rename one duplicate first.', conflict.variants;\n    END IF;\nEND $$;\nCREATE UNIQUE INDEX idx_users_username_ci ON users (lower(username));\nINSERT INTO schema_migrations (version) VALUES ('0005_case_insensitive_usernames.sql');\n\n"""
    return s.replace(marker, ins + marker, 1)
edit('supabase_setup.sql', supa)

# Basic autocomplete UI and inline result feedback.
def template(s):
    s = s.replace('<div class="member-list">', '<div class="member-list" id="member-list">', 1)
    old = "<form method=\"POST\" action=\"{{ url_for('campaign_member_add', campaign_id=campaign.id) }}\" class=\"member-add-form\">{{ csrf_field() }}\n      <input type=\"text\" name=\"username\" placeholder=\"Username to invite\" required>"
    new = "<form method=\"POST\" action=\"{{ url_for('campaign_member_add', campaign_id=campaign.id) }}\" class=\"member-add-form\" id=\"member-add-form\" autocomplete=\"off\">{{ csrf_field() }}\n      <div class=\"member-search\"><input type=\"text\" name=\"username\" id=\"member-username-input\" placeholder=\"Username to invite\" required autocomplete=\"off\"><ul class=\"member-search-results\" id=\"member-search-results\" hidden></ul></div>"
    if old not in s:
        raise SystemExit('member invite form not found')
    s = s.replace(old, new, 1)
    marker = '    </form>\n    {% endif %}'
    s = s.replace(marker, '    </form>\n    <div class="member-add-feedback" id="member-add-feedback" hidden></div>\n    {% endif %}', 1)
    css = '<style>.member-search{position:relative;flex:1}.member-search-results{position:absolute;left:0;right:0;top:100%;z-index:20;margin:4px 0 0;padding:4px;list-style:none;background:var(--bg-panel-raised);border:1px solid var(--hairline-strong);border-radius:var(--radius-s);max-height:220px;overflow:auto}.member-search-results[hidden]{display:none}.member-search-results li{padding:7px 10px;cursor:pointer}.member-search-results li:hover{background:rgba(201,162,75,.16)}.member-add-feedback{margin-top:10px;padding:8px 12px;border-radius:var(--radius-s)}.member-add-feedback[hidden]{display:none}.member-add-feedback.is-success{color:var(--moss-bright);background:rgba(30,154,82,.12)}.member-add-feedback.is-error{color:var(--blood-bright);background:rgba(226,80,72,.12)}</style>'
    js = '''<script>(function(){var f=document.getElementById('member-add-form');if(!f)return;var i=document.getElementById('member-username-input'),l=document.getElementById('member-search-results'),m=document.getElementById('member-add-feedback'),t=null,c={{ campaign.id|tojson }};function close(){l.hidden=true;l.innerHTML=''}function show(ok,x){m.textContent=x;m.className='member-add-feedback '+(ok?'is-success':'is-error');m.hidden=false}i.addEventListener('input',function(){var q=i.value.trim();clearTimeout(t);if(q.length<2){close();return}t=setTimeout(function(){fetch('/campaigns/'+c+'/api/members/search?q='+encodeURIComponent(q),{credentials:'same-origin'}).then(function(r){return r.json().then(function(x){return{r:r,j:x}})}).then(function(x){if(!x.r.ok){show(false,x.j.error||'Search failed');close();return}l.innerHTML=(x.j.results||[]).map(function(n){return '<li data-name="'+n.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')+'">'+n+'</li>'}).join('')||'<li>No matching users</li>';l.hidden=false}).catch(function(){close()})},180)});l.addEventListener('mousedown',function(e){if(e.target.dataset.name){i.value=e.target.dataset.name;close()}});f.addEventListener('submit',function(e){e.preventDefault();var b=f.querySelector('button[type="submit"]'),x=f.querySelector('input[name="csrf_token"]');b.disabled=true;fetch(f.action,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':x?x.value:'','Accept':'application/json'},body:JSON.stringify({username:i.value.trim(),status:f.querySelector('select[name="status"]').value})}).then(function(r){return r.json().then(function(j){return{r:r,j:j}})}).then(function(x){b.disabled=false;if(!x.r.ok){show(false,x.j.error||'Could not add member');return}show(true,'Added '+x.j.username+'.');f.reset();}).catch(function(){b.disabled=false;show(false,'Something went wrong. Please try again.')})});document.addEventListener('click',function(e){if(!f.contains(e.target))close()})})();</script>'''
    return s.replace('{% endblock %}', css + js + '{% endblock %}', 1)
edit('templates/campaign_form.html', template)

# Clean temporary files/workflows only after all edits succeed.
for p in (ROOT/'.tmp').glob('*'):
    if p.is_file():
        p.unlink()
for p in [ROOT/'.github/workflows/apply-session-changes.yml', ROOT/'.github/workflows/apply-final.yml']:
    if p.exists(): p.unlink()
