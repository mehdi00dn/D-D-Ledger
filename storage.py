"""File storage for uploads, behind one small interface.

Two logical buckets:
  'uploads'  final, validated images (avatars/…, sheets/…, maps/…)   -> served via /uploads/<key>
  'temp'     short-lived objects: direct-upload staging, import ZIPs, large exports

Backends
  LocalStorage     plain files on disk.  Used for local development, the test-suite,
                   and a future self-hosted server.  Needs no internet.
  SupabaseStorage  Supabase Storage over its REST API, using the server-side secret
                   key.  Browsers upload big files straight to Supabase through a
                   signed upload URL, so they never pass through the app server
                   (Vercel caps a request body at 4.5 MB).

The app only ever calls: put / get / delete / delete_many / exists /
create_upload_target / signed_download_url.  Swapping backends changes no app code.

Selection (env):  STORAGE_BACKEND=local|supabase, otherwise Supabase is used when
SUPABASE_URL and a secret key are present, else local.
"""
import os
import re
import time
from urllib.parse import quote

import requests
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

UPLOADS, TEMP = 'uploads', 'temp'
_KEY_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_\-./]{0,199}$')


class StorageError(RuntimeError):
    pass


def safe_key(key):
    """Reject anything that could escape a bucket or hide a path trick."""
    if not isinstance(key, str) or not _KEY_RE.match(key) or '..' in key or '//' in key or key.endswith('/'):
        raise StorageError('invalid storage key')
    return key


# --------------------------------------------------------------------------- local
class LocalStorage:
    name = 'local'
    direct_upload_origin = None          # direct uploads stay on our own origin

    def __init__(self, uploads_dir, secret):
        base = uploads_dir.rstrip('/\\')
        self.roots = {UPLOADS: base, TEMP: base + '-tmp'}
        self._signer = URLSafeTimedSerializer(secret, salt='ledger-local-storage')

    def _path(self, bucket, key):
        root = os.path.abspath(self.roots[bucket])
        full = os.path.abspath(os.path.join(root, safe_key(key)))
        if not full.startswith(root + os.sep):
            raise StorageError('invalid storage key')
        return full

    def put(self, bucket, key, data, content_type=None):
        path = self._path(bucket, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.part'
        with open(tmp, 'wb') as fh:
            fh.write(data)
        os.replace(tmp, path)

    def get(self, bucket, key):
        try:
            with open(self._path(bucket, key), 'rb') as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def exists(self, bucket, key):
        return os.path.isfile(self._path(bucket, key))

    def delete(self, bucket, key):
        try:
            os.remove(self._path(bucket, key))
        except (FileNotFoundError, StorageError):
            pass

    def delete_many(self, bucket, keys):
        for k in keys:
            self.delete(bucket, k)

    def check(self):
        """(ok, hint) - can we write where uploads go?"""
        try:
            probe = os.path.join(self.roots[UPLOADS], '.healthcheck')
            os.makedirs(self.roots[UPLOADS], exist_ok=True)
            with open(probe, 'wb') as fh:
                fh.write(b'ok')
            os.remove(probe)
            return True, ''
        except OSError:
            return False, 'The uploads folder is not writable.'

    def list_objects(self, bucket, prefix=''):
        """[(key, created_epoch_seconds)] for every object under `prefix`."""
        root = os.path.abspath(self.roots[bucket])
        out = []
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith('.part'):
                    continue
                full = os.path.join(dirpath, name)
                key = os.path.relpath(full, root).replace(os.sep, '/')
                if key.startswith(prefix):
                    out.append((key, os.path.getmtime(full)))
        return out

    # direct upload / download are same-origin routes in the app, authorised by a signed token
    def create_upload_target(self, bucket, key, content_type, max_bytes):
        token = self._signer.dumps({'b': bucket, 'k': safe_key(key), 'max': int(max_bytes)})
        return {'method': 'PUT', 'url': '/_direct-upload/' + token, 'body': 'raw', 'headers': {}}

    def read_upload_token(self, token, max_age=600):
        try:
            return self._signer.loads(token, max_age=max_age)
        except (BadSignature, SignatureExpired):
            return None

    def signed_download_url(self, bucket, key, filename, ttl=120):
        token = self._signer.dumps({'b': bucket, 'k': safe_key(key), 'n': filename})
        return '/_download/' + token

    def read_download_token(self, token, max_age=120):
        try:
            return self._signer.loads(token, max_age=max_age)
        except (BadSignature, SignatureExpired):
            return None


# ------------------------------------------------------------------------ supabase
class SupabaseStorage:
    name = 'supabase'

    def __init__(self, url, key, uploads_bucket, temp_bucket, timeout=(5, 40)):
        self.base = url.rstrip('/') + '/storage/v1'
        self.direct_upload_origin = url.rstrip('/')
        self.buckets = {UPLOADS: uploads_bucket, TEMP: temp_bucket}
        self.timeout = timeout
        self._http = requests.Session()
        # New sb_secret_ keys must appear in `apikey`; they may only appear in
        # Authorization if the value is identical.  Legacy JWT keys work the same way.
        self._headers = {'apikey': key, 'Authorization': 'Bearer ' + key}

    def _url(self, *parts):
        return self.base + '/' + '/'.join(p for p in parts if p)

    def _obj(self, bucket, key):
        return quote(safe_key(key), safe='/')

    def _req(self, method, url, **kw):
        headers = dict(self._headers, **kw.pop('headers', {}))
        try:
            return self._http.request(method, url, headers=headers, timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise StorageError('storage unreachable: %s' % type(exc).__name__)

    @staticmethod
    def _missing(resp):
        if resp.status_code == 404:
            return True
        return resp.status_code == 400 and ('not_found' in resp.text.lower() or 'not found' in resp.text.lower())

    def put(self, bucket, key, data, content_type=None):
        r = self._req('POST', self._url('object', self.buckets[bucket], self._obj(bucket, key)), data=data,
                      headers={'Content-Type': content_type or 'application/octet-stream', 'x-upsert': 'true'})
        if r.status_code >= 300:
            raise StorageError('upload failed (%s)' % r.status_code)

    def get(self, bucket, key):
        r = self._req('GET', self._url('object', 'authenticated', self.buckets[bucket], self._obj(bucket, key)))
        if self._missing(r):
            return None
        if r.status_code >= 300:
            raise StorageError('download failed (%s)' % r.status_code)
        return r.content

    def exists(self, bucket, key):
        r = self._req('HEAD', self._url('object', 'authenticated', self.buckets[bucket], self._obj(bucket, key)))
        return r.status_code < 300

    def delete(self, bucket, key):
        self.delete_many(bucket, [key])

    def delete_many(self, bucket, keys):
        keys = [safe_key(k) for k in keys if k]
        for i in range(0, len(keys), 100):                     # API takes batches
            r = self._req('DELETE', self._url('object', self.buckets[bucket]), json={'prefixes': keys[i:i + 100]})
            if r.status_code >= 300 and not self._missing(r):
                raise StorageError('delete failed (%s)' % r.status_code)

    def check(self):
        """(ok, hint): is the key accepted and do both buckets exist?  Never raises, never leaks the key."""
        try:
            for name in self.buckets.values():
                r = self._req('GET', self._url('bucket', name))
                if r.status_code in (401, 403):
                    return False, 'Supabase rejected the storage key - check SUPABASE_SECRET_KEY (and SUPABASE_URL).'
                if self._missing(r):
                    return False, 'Storage bucket "%s" does not exist - create it (see VERCEL_DEPLOY.md).' % name
                if r.status_code >= 300:
                    return False, 'Supabase Storage answered HTTP %s.' % r.status_code
        except StorageError:
            return False, 'Supabase Storage is unreachable from the server - check SUPABASE_URL.'
        return True, ''

    def list_objects(self, bucket, prefix=''):
        """[(key, created_epoch_seconds)] for every object under `prefix` (walks folders)."""
        from datetime import datetime
        out, folders = [], [prefix.strip('/')]
        while folders:
            folder = folders.pop()
            offset = 0
            while True:
                r = self._req('POST', self._url('object', 'list', self.buckets[bucket]),
                              json={'prefix': folder, 'limit': 100, 'offset': offset,
                                    'sortBy': {'column': 'name', 'order': 'asc'}})
                if r.status_code >= 300:
                    raise StorageError('list failed (%s)' % r.status_code)
                items = r.json()
                for it in items:
                    key = (folder + '/' if folder else '') + it['name']
                    if it.get('id') is None:                   # a folder
                        folders.append(key)
                    else:
                        stamp = it.get('created_at') or it.get('updated_at')
                        try:
                            when = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
                        except Exception:
                            when = None
                        out.append((key, when))
                if len(items) < 100:
                    break
                offset += 100
        return out

    def create_upload_target(self, bucket, key, content_type, max_bytes):
        r = self._req('POST', self._url('object', 'upload', 'sign', self.buckets[bucket], self._obj(bucket, key)), json={})
        if r.status_code >= 300:
            raise StorageError('could not create an upload URL (%s)' % r.status_code)
        rel = r.json().get('url') or ''
        if 'token=' not in rel:
            raise StorageError('unexpected response creating an upload URL')
        return {'method': 'PUT', 'url': self.base + rel, 'body': 'formdata', 'headers': {}}

    def signed_download_url(self, bucket, key, filename, ttl=120):
        r = self._req('POST', self._url('object', 'sign', self.buckets[bucket], self._obj(bucket, key)),
                      json={'expiresIn': int(ttl)})
        if r.status_code >= 300:
            raise StorageError('could not sign a download URL (%s)' % r.status_code)
        rel = r.json().get('signedURL') or r.json().get('signedUrl') or ''
        if not rel:
            raise StorageError('unexpected response signing a download URL')
        url = self.base + rel if rel.startswith('/') else rel
        if filename and 'download=' not in url:
            url += ('&' if '?' in url else '?') + 'download=' + quote(filename)
        return url


# -------------------------------------------------------------------------- factory
_instance = None


def get_storage():
    global _instance
    if _instance is None:
        _instance = build_storage()
    return _instance


def reset_storage():
    global _instance
    _instance = None


def build_storage(env=None):
    env = os.environ if env is None else env
    backend = (env.get('STORAGE_BACKEND') or '').strip().lower()
    key = env.get('SUPABASE_SECRET_KEY') or env.get('SUPABASE_SERVICE_ROLE_KEY')
    url = env.get('SUPABASE_URL')
    if not backend:
        backend = 'supabase' if (url and key) else 'local'
    if backend == 'supabase':
        if not (url and key):
            raise StorageError('STORAGE_BACKEND=supabase needs SUPABASE_URL and SUPABASE_SECRET_KEY')
        return SupabaseStorage(url, key, env.get('STORAGE_BUCKET_UPLOADS', 'ledger-uploads'),
                               env.get('STORAGE_BUCKET_TEMP', 'ledger-temp'))
    if backend == 'local':
        uploads = env.get('UPLOAD_DIR') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
        return LocalStorage(uploads, env.get('SECRET_KEY') or 'dev-only-local-storage-secret')
    raise StorageError('unknown STORAGE_BACKEND %r' % backend)
