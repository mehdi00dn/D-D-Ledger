from pathlib import Path
p = Path('.tmp/apply_changes.py')
s = p.read_text(encoding='utf-8')
start = s.index("    like = q.replace")
end = s.index("    db.close()\n    return jsonify({'results': [r['username'] for r in rows]})", start)
replacement = '''    like = q.replace('\\\\', '\\\\\\\\').replace('%', r'\\%').replace('_', r'\\_') + '%'
    rows = db.execute("SELECT username FROM users WHERE username ILIKE ? ESCAPE '\\\\' AND id != ? AND id NOT IN (SELECT user_id FROM campaign_members WHERE campaign_id = ?) ORDER BY username ASC LIMIT 8", (like, session['user_id'], g.campaign_id)).fetchall()
'''
p.write_text(s[:start] + replacement + s[end:], encoding='utf-8')
