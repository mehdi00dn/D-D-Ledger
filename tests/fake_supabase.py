"""A faithful-enough fake of Supabase Storage's REST API, for tests.

Implements exactly the endpoints storage.SupabaseStorage uses (paths and payloads as in
the official storage-js client / API):
  POST   /storage/v1/object/{bucket}/{key}                    upload   (x-upsert)
  GET    /storage/v1/object/authenticated/{bucket}/{key}      download
  HEAD   same
  DELETE /storage/v1/object/{bucket}     {"prefixes":[...]}   bulk delete
  POST   /storage/v1/object/upload/sign/{bucket}/{key}        -> {"url": "/object/upload/sign/...?token=T"}
  PUT    /storage/v1/object/upload/sign/{bucket}/{key}?token= signed upload (multipart '' field or raw body)
  POST   /storage/v1/object/sign/{bucket}/{key}  {"expiresIn"} -> {"signedURL": "/object/sign/...?token=T"}
  GET    /storage/v1/object/sign/{bucket}/{key}?token=        signed download
It enforces the API-key rules (apikey header required; Authorization must equal it or be a
JWT), per-bucket file-size limits and allowed MIME types (like real buckets), and answers
CORS preflights so a browser on another origin can PUT to it -- as the real service does.
"""
import io
import threading
import time
import uuid
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

ANY = {'GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS'}


class FakeSupabase:
    def __init__(self, key='sb_secret_TESTKEY', buckets=None):
        self.key = key
        self.buckets = buckets if buckets is not None else {
            'ledger-uploads': {'limit': 30 * 1024 * 1024, 'mime': ['image/png', 'image/jpeg', 'image/gif', 'image/webp']},
            'ledger-temp': {'limit': 50 * 1024 * 1024, 'mime': None},
        }
        self.objects = {}                     # (bucket, key) -> (bytes, content_type)
        self.created = {}                     # (bucket, key) -> epoch seconds (tests may backdate)
        self.upload_tokens = {}               # token -> (bucket, key)
        self.download_tokens = {}
        self.calls = []                       # [(method, path)] for assertions
        self.app = Flask('fake-supabase')
        self._routes()
        self.server = None

    # ---- lifecycle
    def start(self):
        self.server = make_server('127.0.0.1', 0, self.app, threaded=True)
        self.port = self.server.server_port
        self.url = f'http://127.0.0.1:{self.port}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.server:
            self.server.shutdown()

    def keys(self, bucket):
        return sorted(k for (b, k) in self.objects if b == bucket)

    # ---- helpers
    def _auth_ok(self):
        api = request.headers.get('apikey')
        auth = request.headers.get('Authorization', '')
        if api != self.key:
            return False
        return auth in ('', 'Bearer ' + api) or auth.count('.') == 2      # same value, or a real JWT

    def _check_bucket(self, bucket, data, ctype):
        b = self.buckets.get(bucket)
        if b is None:
            return jsonify({'statusCode': '404', 'error': 'Bucket not found', 'message': 'Bucket not found'}), 404
        if len(data) > b['limit']:
            return jsonify({'statusCode': '413', 'error': 'Payload too large', 'message': 'The object exceeded the maximum allowed size'}), 413
        if b['mime'] and (ctype or '').split(';')[0] not in b['mime']:
            return jsonify({'statusCode': '415', 'error': 'invalid_mime_type', 'message': 'mime type not supported'}), 415
        return None

    @staticmethod
    def _cors(resp):
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Headers'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = '*'
        return resp

    def _body_and_type(self):
        """Multipart (storage-js sends the file in a field named '') or a raw body."""
        if request.mimetype == 'multipart/form-data':
            f = request.files.get('') or next(iter(request.files.values()), None)
            if f is None:
                return b'', ''
            return f.read(), f.mimetype
        return request.get_data(), request.headers.get('Content-Type', '')

    def _routes(self):
        app = self.app

        @app.after_request
        def cors(resp):
            return self._cors(resp)

        @app.route('/storage/v1/<path:rest>', methods=list(ANY))
        def storage(rest):
            self.calls.append((request.method, '/' + rest))
            if request.method == 'OPTIONS':
                return Response(status=204)
            parts = rest.split('/')
            # signed URLs carry their own token instead of API keys
            signed_up = parts[:2] == ['object', 'upload'] and len(parts) > 3 and parts[2] == 'sign' and request.method == 'PUT'
            signed_down = parts[:2] == ['object', 'sign'] and request.method == 'GET'
            if not (signed_up or signed_down) and not self._auth_ok():
                return jsonify({'statusCode': '401', 'error': 'Unauthorized', 'message': 'Invalid Compact JWS'}), 401

            m = request.method
            if parts[0] == 'bucket' and len(parts) == 2 and m == 'GET':
                if parts[1] not in self.buckets:
                    return jsonify({'statusCode': '404', 'error': 'Bucket not found', 'message': 'Bucket not found'}), 404
                return jsonify({'id': parts[1], 'name': parts[1], 'public': False})
            if parts[:3] == ['object', 'upload', 'sign'] and m == 'POST':
                bucket, key = parts[3], '/'.join(parts[4:])
                token = uuid.uuid4().hex
                self.upload_tokens[token] = (bucket, key)
                return jsonify({'url': f'/object/upload/sign/{bucket}/{key}?token={token}'})
            if signed_up:
                bucket, key = parts[3], '/'.join(parts[4:])
                if self.upload_tokens.get(request.args.get('token')) != (bucket, key):
                    return jsonify({'statusCode': '403', 'error': 'invalid_jwt', 'message': 'invalid token'}), 403
                data, ctype = self._body_and_type()
                err = self._check_bucket(bucket, data, ctype)
                if err:
                    return err
                self.objects[(bucket, key)] = (data, ctype)
                self.created[(bucket, key)] = time.time()
                return jsonify({'Key': f'{bucket}/{key}'})
            if parts[:2] == ['object', 'sign'] and m == 'POST':
                bucket, key = parts[2], '/'.join(parts[3:])
                if (bucket, key) not in self.objects:
                    return jsonify({'statusCode': '404', 'error': 'not_found', 'message': 'Object not found'}), 400
                token = uuid.uuid4().hex
                self.download_tokens[token] = (bucket, key)
                return jsonify({'signedURL': f'/object/sign/{bucket}/{key}?token={token}'})
            if signed_down:
                bucket, key = parts[2], '/'.join(parts[3:])
                if self.download_tokens.get(request.args.get('token')) != (bucket, key) or (bucket, key) not in self.objects:
                    return jsonify({'statusCode': '400', 'error': 'invalid_jwt'}), 400
                data, ctype = self.objects[(bucket, key)]
                resp = Response(data, mimetype=ctype or 'application/octet-stream')
                if request.args.get('download'):
                    resp.headers['Content-Disposition'] = 'attachment; filename="%s"' % request.args['download']
                return resp
            if parts[0] == 'object' and parts[1] == 'authenticated' and m in ('GET', 'HEAD'):
                bucket, key = parts[2], '/'.join(parts[3:])
                if (bucket, key) not in self.objects:
                    return jsonify({'statusCode': '404', 'error': 'not_found', 'message': 'Object not found'}), 400
                data, ctype = self.objects[(bucket, key)]
                return Response(data if m == 'GET' else b'', mimetype=ctype or 'application/octet-stream')
            if parts[:2] == ['object', 'list'] and m == 'POST':
                bucket, body = parts[2], request.get_json(silent=True) or {}
                prefix = (body.get('prefix') or '').strip('/')
                base = prefix + '/' if prefix else ''
                seen, entries = set(), []
                for (b, key) in sorted(self.objects):
                    if b != bucket or not key.startswith(base):
                        continue
                    head, _, rest = key[len(base):].partition('/')
                    if rest:                                            # deeper -> a folder entry (id null)
                        if head not in seen:
                            seen.add(head); entries.append({'name': head, 'id': None, 'created_at': None})
                    else:
                        ts = datetime.fromtimestamp(self.created.get((b, key), time.time()), timezone.utc).isoformat()
                        entries.append({'name': head, 'id': uuid.uuid4().hex, 'created_at': ts, 'updated_at': ts})
                off, lim = int(body.get('offset', 0)), int(body.get('limit', 100))
                return jsonify(entries[off:off + lim])
            if parts[0] == 'object' and len(parts) == 2 and m == 'DELETE':           # bulk
                bucket = parts[1]
                for k in (request.get_json(silent=True) or {}).get('prefixes', []):
                    self.objects.pop((bucket, k), None)
                return jsonify([])
            if parts[0] == 'object' and len(parts) > 2 and m in ('POST', 'PUT'):
                bucket, key = parts[1], '/'.join(parts[2:])
                data, ctype = self._body_and_type()
                err = self._check_bucket(bucket, data, ctype)
                if err:
                    return err
                if (bucket, key) in self.objects and request.headers.get('x-upsert', 'false') != 'true':
                    return jsonify({'statusCode': '409', 'error': 'Duplicate', 'message': 'The resource already exists'}), 400
                self.objects[(bucket, key)] = (data, ctype)
                self.created[(bucket, key)] = time.time()
                return jsonify({'Key': f'{bucket}/{key}', 'Id': uuid.uuid4().hex})
            return jsonify({'error': 'not implemented in the fake', 'path': rest}), 501
