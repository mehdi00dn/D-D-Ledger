from pathlib import Path

ROOT = Path('.')

def edit(path, fn):
    p = ROOT / path
    s = p.read_text(encoding='utf-8')
    n = fn(s)
    if n == s:
        raise SystemExit(f'No change made to {path}; expected source text was not found')
    p.write_text(n, encoding='utf-8')


def auth(s):
    s = s.replace("SELECT id FROM users WHERE username = ?", "SELECT id FROM users WHERE lower(username) = lower(?)", 1)
    s = s.replace("SELECT * FROM users WHERE username = ?", "SELECT * FROM users WHERE lower(username) = lower(?)", 1)
    return s
edit('app.py', auth)


def members(s):
    start = s.index("@app.route('/campaigns/<int:campaign_id>/members/add', methods=['POST'])")
    end = s.index("\n\n@app.route('/campaigns/<int:campaign_id>/members/<int:user_id>/status'", start)
    block = '''@app.route('/campaigns/<int:campaign_id>/members/add', methods=['POST'])
@campaign_access_required
def campaign_member_add():
    if g.campaign_role != 'owner':
        abort(403)
    data = request.get_json(silent=True) or request.form
    username = (data.get('username') or '').strip()
    status = data.get('status') if data.get('status') in ('dm', 'player') else 'player'
    db = get_db()
    if not username:
        db.close()
        return _reject_request('Enter a username to invite.', 400)
    user = db.execute('SELECT id, username FROM users WHERE lower(username) = lower(?)', (username,)).fetchone()
    if not user:
        db.close()
        return _reject_request('No user found with that username.', 404)
    if user['id'] == session['user_id']:
        db.close()
        return _reject_request("You're already the owner of this campaign.", 400)
    existing = db.execute('SELECT id FROM campaign_members WHERE campaign_id = ? AND user_id = ?',
                           (g.campaign_id, user['id'])).fetchone()
    if existing:
        db.close()
        return _reject_request('%s is already a member of this campaign.' % user['username'], 400)
    db.execute('INSERT INTO campaign_members (campaign_id, user_id, role, status) VALUES (?, ?, ?, ?)',
               (g.campaign_id, user['id'], 'member', status))
    db.commit()
    db.close()
    if _wants_json():
        return jsonify({'ok': True, 'username': user['username'], 'status': status, 'user_id': user['id']})
    return redirect(url_for('campaign_edit', campaign_id=g.campaign_id))


@app.route('/campaigns/<int:campaign_id>/api/members/search')
@campaign_access_required
def api_member_search():
    if g.campaign_role != 'owner':
        abort(403)
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'results': []})
    db = get_db()
    secret = app.config['SECRET_KEY']
    retry = security.member_search_blocked_for(db, secret, session['user_id'], g.campaign_id)
    if retry:
        db.close()
        return jsonify({'error': 'Searching too fast -- try again in a moment.'}), 429
    security.record_member_search(db, secret, session['user_id'], g.campaign_id)
    db.commit()
    like = q.replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_') + '%'
    rows = db.execute('''
        SELECT username FROM users
        WHERE username ILIKE ? ESCAPE '\\'
          AND id != ?
          AND id NOT IN (SELECT user_id FROM campaign_members WHERE campaign_id = ?)
        ORDER BY username ASC LIMIT 8
    ''', (like, session['user_id'], g.campaign_id)).fetchall()
    db.close()
    return jsonify({'results': [r['username'] for r in rows]})
'''
    return s[:start] + block + s[end:]
edit('app.py', members)


def sec(s):
    if "'member-search'" not in s:
        s = s.replace("    'register-ip': (int(os.environ.get('REGISTER_LIMIT_IP', 20)), 60 * 60),\n", "    'register-ip': (int(os.environ.get('REGISTER_LIMIT_IP', 20)), 60 * 60),\n    'member-search': (int(os.environ.get('MEMBER_SEARCH_LIMIT', 30)), 60),\n")
    s += '''

def member_search_blocked_for(db, secret, user_id, campaign_id):
    return _retry_after(db, 'member-search', _key(secret, 'msearch', '%s|%s' % (user_id, campaign_id)))


def record_member_search(db, secret, user_id, campaign_id):
    db.execute('INSERT INTO login_attempts (kind, key) VALUES (?, ?)',
               ('member-search', _key(secret, 'msearch', '%s|%s' % (user_id, campaign_id))))
    _maybe_cleanup(db)
'''
    return s
edit('security.py', sec)

mig = ROOT / 'migrations/0005_case_insensitive_usernames.sql'
mig.parent.mkdir(exist_ok=True)
if not mig.exists():
    mig.write_text('''-- Usernames are unique case-insensitively while display case is preserved.
DO $$
DECLARE conflict RECORD;
BEGIN
    SELECT lower(username) AS lu, array_agg(username ORDER BY id) AS variants
      INTO conflict FROM users GROUP BY lower(username) HAVING count(*) > 1 LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'Cannot make usernames case-insensitive: % already exist as %. Rename one of them, then re-run this migration.',
            array_length(conflict.variants, 1), conflict.variants;
    END IF;
END $$;
CREATE UNIQUE INDEX idx_users_username_ci ON users (lower(username));
''', encoding='utf-8')


def supa(s):
    if 'idx_users_username_ci' in s:
        return s
    ins = '''-- ---- 0005_case_insensitive_usernames.sql ----
DO $$
DECLARE conflict RECORD;
BEGIN
    SELECT lower(username) AS lu, array_agg(username ORDER BY id) AS variants
      INTO conflict FROM users GROUP BY lower(username) HAVING count(*) > 1 LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'Cannot make usernames case-insensitive: % already exist as %. Rename one of them, then re-run this migration.',
            array_length(conflict.variants, 1), conflict.variants;
    END IF;
END $$;
CREATE UNIQUE INDEX idx_users_username_ci ON users (lower(username));
INSERT INTO schema_migrations (version) VALUES ('0005_case_insensitive_usernames.sql');

'''
    return s.replace('COMMIT;', ins + 'COMMIT;', 1)
edit('supabase_setup.sql', supa)


def template(s):
    s = s.replace('<div class="member-list">', '<div class="member-list" id="member-list">', 1)
    s = s.replace("{{ icon('user') }} {{ m.username }}", "{{ icon('user') }} <span class=\"member-name-text\">{{ m.username }}</span>", 1)
    old = '''<form method="POST" action="{{ url_for('campaign_member_add', campaign_id=campaign.id) }}" class="member-add-form">{{ csrf_field() }}
      <input type="text" name="username" placeholder="Username to invite" required>'''
    new = '''<form method="POST" action="{{ url_for('campaign_member_add', campaign_id=campaign.id) }}" class="member-add-form" id="member-add-form" autocomplete="off">{{ csrf_field() }}
      <div class="member-search">
        <input type="text" name="username" id="member-username-input" placeholder="Username to invite" required autocomplete="off">
        <ul class="member-search-results" id="member-search-results" hidden></ul>
      </div>'''
    if old not in s:
        raise SystemExit('member invite form not found')
    s = s.replace(old, new, 1)
    marker = '''    </form>
    {% endif %}

    <div class="leave-campaign-row">'''
    replacement = '''    </form>
    <div class="member-add-feedback" id="member-add-feedback" hidden></div>
    {% endif %}

    <div class="leave-campaign-row">'''
    s = s.replace(marker, replacement, 1)
    css = '''
<style>
.member-search{position:relative;flex:1;min-width:140px}.member-search input[type="text"]{width:100%;min-width:0}.member-search-results{position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:20;margin:0;padding:4px;list-style:none;background:var(--bg-panel-raised);border:1px solid var(--hairline-strong);border-radius:var(--radius-s);box-shadow:var(--shadow-deep);max-height:220px;overflow-y:auto}.member-search-results[hidden]{display:none}.member-search-results li{padding:7px 10px;border-radius:var(--radius-s);font-size:13.5px;cursor:pointer;color:var(--parchment)}.member-search-results li:hover,.member-search-results li.is-active{background:rgba(201,162,75,.16)}.member-search-results li.is-empty{cursor:default;color:var(--parchment-dim);font-size:12.5px}.member-add-feedback{margin-top:10px;padding:8px 12px;border-radius:var(--radius-s);font-size:13px;border:1px solid transparent}.member-add-feedback[hidden]{display:none}.member-add-feedback.is-success{background:rgba(30,154,82,.12);border-color:rgba(30,154,82,.35);color:var(--moss-bright)}.member-add-feedback.is-error{background:rgba(226,80,72,.12);border-color:rgba(226,80,72,.35);color:var(--blood-bright)}
</style>
'''
    js = '''
<script>
(function(){
 var form=document.getElementById('member-add-form'); if(!form)return;
 var input=document.getElementById('member-username-input'),list=document.getElementById('member-search-results'),feedback=document.getElementById('member-add-feedback');
 var memberList=document.getElementById('member-list'),statusSel=form.querySelector('select[name="status"]'),timer=null;
 var campaignId={{ campaign.id|tojson }};
 function esc(s){return s.replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
 function close(){list.hidden=true;list.innerHTML='';}
 function msg(ok,text){feedback.textContent=text;feedback.className='member-add-feedback '+(ok?'is-success':'is-error');feedback.hidden=false;}
 function csrf(){var x=form.querySelector('input[name="csrf_token"]');return x?x.value:'';}
 function render(items){if(!items.length){list.innerHTML='<li class="is-empty">No matching users</li>';list.hidden=false;return;}list.innerHTML=items.map(function(n){return '<li role="option" data-name="'+esc(n)+'">'+esc(n)+'</li>';}).join('');list.hidden=false;}
 input.addEventListener('input',function(){var q=input.value.trim();if(timer)clearTimeout(timer);if(q.length<2){close();return;}timer=setTimeout(function(){fetch('/campaigns/'+campaignId+'/api/members/search?q='+encodeURIComponent(q),{credentials:'same-origin'}).then(function(r){return r.json().then(function(j){return{r:r,j:j};});}).then(function(x){if(x.r.status===429){msg(false,x.j.error||'Searching too fast.');close();return;}render(x.j.results||[]);}).catch(close);},180);});
 list.addEventListener('mousedown',function(e){var li=e.target.closest('li[role="option"]');if(li){input.value=li.dataset.name;close();input.focus();}});
 document.addEventListener('click',function(e){if(!form.contains(e.target))close();});
 form.addEventListener('submit',function(e){e.preventDefault();close();var username=input.value.trim();if(!username)return;var btn=form.querySelector('button[type="submit"]');btn.disabled=true;fetch(form.action,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf(),'Accept':'application/json'},body:JSON.stringify({username:username,status:statusSel.value})}).then(function(r){return r.json().then(function(j){return{r:r,j:j};});}).then(function(x){btn.disabled=false;if(!x.r.ok){msg(false,x.j.error||'Could not add that member.');return;}msg(true,'Added '+x.j.username+' as '+(x.j.status==='dm'?'DM':'Player')+'.');var row=document.createElement('div');row.className='member-row';row.innerHTML='<span class="member-name">'+esc(x.j.username)+'</span><span class="role-badge">'+(x.j.status==='dm'?'DM':'Player')+'</span>';memberList.appendChild(row);form.reset();}).catch(function(){btn.disabled=false;msg(false,'Something went wrong. Please try again.');});});
})();
</script>
'''
    return s.replace('{% endblock %}', css + js + '{% endblock %}', 1)
edit('templates/campaign_form.html', template)

for p in [ROOT/'.tmp/apply_changes.py', ROOT/'.github/workflows/apply-session-changes.yml']:
    if p.exists(): p.unlink()
for p in (ROOT/'.tmp').glob('session-changes.patch.part*'):
    p.unlink()
