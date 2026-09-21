import os, sys
sys.path.insert(0, os.getcwd())
import database
if os.environ.get('DATABASE_URL'):
    if not os.environ.get('LEDGER_SKIP_MIGRATE'):
        import migrate; migrate.run(os.environ['DATABASE_URL'])
else:
    database.init_db()
import app
app.app.run(host='127.0.0.1', port=int(os.environ['LEDGER_PORT']), threaded=True, debug=False, use_reloader=False)
