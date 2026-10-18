"""Security layer for the KNOCK console and API.

Threats this addresses (full model in docs/SECURITY.md):
* Anyone on the network driving the grid      -> login required; signed, expiring, HttpOnly session cookies
* An operator doing more than their job       -> roles: viewer < dispatcher < supervisor
* A user in one country touching another's    -> each user is scoped to regions; sessions are region-tenants
* One person pushing a fleet-wide change      -> two-person rule for firmware rollbacks and large actions
* Someone covering their tracks               -> hash-chained, append-only audit log, verifiable from the UI
* Password guessing / request flooding        -> PBKDF2-SHA256 hashes, login lockout, per-user rate limits
* Cross-site request forgery / clickjacking   -> SameSite=Strict cookie + required custom header; CSP, frame-deny

KNOCK_AUTH=off disables login for local development only; the console then shows a red banner.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional

from fastapi import HTTPException, Request

from knock import keys

ROLES = ['viewer', 'dispatcher', 'supervisor']
ALL_REGIONS = '*'
USERS_PATH = os.path.join(keys.CONFIG_DIR, 'users.json')
AUDIT_PATH = os.path.join(keys.ROOT, 'logs', 'audit.jsonl')
SESSION_TTL_S = 8 * 3600
COOKIE = 'knock_session'
CSRF_HEADER = 'x-knock-request'
PBKDF2_ITER = 200_000

PERMISSIONS = {
    'viewer': ['Watch both neighborhoods, results, insights and the audit log'],
    'dispatcher': ['Everything a viewer can', 'Approve or deny decisions', 'Play, pause and step', 'Use the copilot'],
    'supervisor': ['Everything a dispatcher can', 'Inject disasters and change region or scenario',
                   'Change the emergency and auto-approve policies', 'Reveal ground truth in the inspector'],
}


def auth_enabled() -> bool:
    return os.environ.get('KNOCK_AUTH', 'on').lower() not in ('off', '0', 'false', 'no')


def role_at_least(role: str, needed: str) -> bool:
    return ROLES.index(role) >= ROLES.index(needed)


# ---------- users ----------
def _hash_pw(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ITER).hex()


class UserStore:
    def __init__(self, path: str = USERS_PATH):
        self.path = path
        self.lock = threading.Lock()

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {'users': {}}
        with open(self.path) as f:
            return json.load(f)

    def _save(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, self.path)

    def add(self, name: str, password: str, role: str, regions: List[str]) -> None:
        if role not in ROLES:
            raise ValueError('role must be one of %s' % ', '.join(ROLES))
        if len(password) < 10:
            raise ValueError('password must be at least 10 characters')
        if not name.replace('-', '').replace('_', '').isalnum() or len(name) > 40:
            raise ValueError('user name: letters, digits, - and _ only, up to 40 characters')
        salt = os.urandom(16)
        with self.lock:
            data = self._load()
            data['users'][name] = {'salt': salt.hex(), 'hash': _hash_pw(password, salt), 'role': role,
                                   'regions': regions or [ALL_REGIONS], 'created': int(time.time())}
            self._save(data)

    def check(self, name: str, password: str) -> Optional[dict]:
        u = self._load()['users'].get(name)
        if not u:
            _hash_pw(password, b'x' * 16)        # same work either way, so timing does not reveal valid names
            return None
        if not hmac.compare_digest(_hash_pw(password, bytes.fromhex(u['salt'])), u['hash']):
            return None
        return {'name': name, 'role': u['role'], 'regions': u['regions']}

    def get(self, name: str) -> Optional[dict]:
        u = self._load()['users'].get(name)
        return u and {'name': name, 'role': u['role'], 'regions': u['regions']}

    def count(self) -> int:
        return len(self._load()['users'])


USERS = UserStore()


# ---------- session tokens ----------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip('=')


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def issue_token(user: dict) -> str:
    body = json.dumps({'u': user['name'], 'r': user['role'], 'g': user['regions'],
                       'exp': int(time.time()) + SESSION_TTL_S, 'n': secrets.token_hex(8)}, separators=(',', ':')).encode()
    return _b64(body) + '.' + keys.sign(body, 'session')


REVOKED: set = set()


def read_token(token: str) -> Optional[dict]:
    try:
        body_b64, sig = token.split('.', 1)
        body = _unb64(body_b64)
    except Exception:
        return None
    if not keys.verify(body, 'session', sig):
        return None
    data = json.loads(body)
    if data['exp'] < time.time() or data['n'] in REVOKED:
        return None
    # the user may have been removed or demoted since the token was issued
    current = USERS.get(data['u'])
    if current is None:
        return None
    return {'name': current['name'], 'role': current['role'], 'regions': current['regions'], 'nonce': data['n']}


LOCAL_USER = {'name': 'local-dev', 'role': 'supervisor', 'regions': [ALL_REGIONS], 'nonce': 'local'}


def current_user(request: Request) -> Optional[dict]:
    if not auth_enabled():
        return LOCAL_USER
    tok = request.cookies.get(COOKIE) or ''
    return read_token(tok) if tok else None


def require(request: Request, role: str = 'viewer', region: Optional[str] = None) -> dict:
    """Authenticate, authorize by role, enforce region tenancy and CSRF for state-changing calls."""
    user = current_user(request)
    if user is None:
        raise HTTPException(401, detail={'error': 'login_required', 'detail': 'Sign in to use KNOCK.'})
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and auth_enabled():
        if request.headers.get(CSRF_HEADER) != '1':
            raise HTTPException(403, detail={'error': 'csrf', 'detail': 'Request rejected: missing request header.'})
        RATE.check(user['name'])
    if not role_at_least(user['role'], role):
        raise HTTPException(403, detail={'error': 'forbidden', 'detail': 'Your role (%s) cannot do this; it needs %s.' % (user['role'], role)})
    if region is not None and not region_allowed(user, region):
        raise HTTPException(403, detail={'error': 'region_forbidden', 'detail': 'Your account is not authorized for this region.'})
    return user


def region_allowed(user: dict, region: str) -> bool:
    return ALL_REGIONS in user['regions'] or region in user['regions']


# ---------- throttling ----------
class RateLimiter:
    def __init__(self, limit: int = 40, window_s: float = 10.0):
        self.limit, self.window = limit, window_s
        self.hits: Dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                raise HTTPException(429, detail={'error': 'rate_limited', 'detail': 'Too many requests; slow down.'})
            q.append(now)


RATE = RateLimiter()


class LoginGuard:
    """Lock a user name (and an address) for 10 minutes after 5 failed sign-ins."""

    def __init__(self, max_fail: int = 5, lock_s: int = 600):
        self.max_fail, self.lock_s = max_fail, lock_s
        self.fails: Dict[str, list] = defaultdict(list)
        self.lock = threading.Lock()

    def blocked(self, *keys_: str) -> bool:
        now = time.time()
        with self.lock:
            for k in keys_:
                self.fails[k] = [t for t in self.fails[k] if now - t < self.lock_s]
                if len(self.fails[k]) >= self.max_fail:
                    return True
        return False

    def fail(self, *keys_: str) -> None:
        with self.lock:
            for k in keys_:
                self.fails[k].append(time.time())

    def reset(self, *keys_: str) -> None:
        with self.lock:
            for k in keys_:
                self.fails.pop(k, None)


GUARD = LoginGuard()


# ---------- tamper-evident audit log ----------
class AuditLog:
    """Append-only JSON lines. Each entry stores the hash of the previous one, so editing or deleting any past
    line breaks the chain from that point on, and /api/audit/verify says where."""

    GENESIS = '0' * 64

    def __init__(self, path: str = AUDIT_PATH):
        self.path = path
        self.lock = threading.Lock()
        self.last_hash, self.seq = self.GENESIS, 0
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.strip():
                        e = json.loads(line)
                        self.last_hash, self.seq = e['hash'], e['seq']

    @staticmethod
    def _digest(entry: dict) -> str:
        body = {k: entry[k] for k in ('seq', 'ts', 'user', 'role', 'action', 'detail', 'prev')}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def record(self, user: Optional[dict], action: str, detail: dict) -> dict:
        with self.lock:
            self.seq += 1
            e = {'seq': self.seq, 'ts': round(time.time(), 3), 'user': (user or {}).get('name', 'system'),
                 'role': (user or {}).get('role', 'system'), 'action': action, 'detail': detail, 'prev': self.last_hash}
            e['hash'] = self._digest(e)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, 'a') as f:
                f.write(json.dumps(e, separators=(',', ':')) + '\n')
            self.last_hash = e['hash']
            return e

    def tail(self, n: int = 100, region: Optional[str] = None) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        if region:
            lines = [e for e in lines if e['detail'].get('region') in (None, region)]
        return lines[-n:][::-1]

    def verify(self) -> dict:
        if not os.path.exists(self.path):
            return {'ok': True, 'entries': 0}
        prev, n = self.GENESIS, 0
        with open(self.path) as f:
            for i, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    return {'ok': False, 'entries': n, 'brokenAt': i, 'reason': 'unreadable line'}
                if e.get('prev') != prev or self._digest(e) != e.get('hash'):
                    return {'ok': False, 'entries': n, 'brokenAt': e.get('seq', i), 'reason': 'entry changed or removed'}
                prev, n = e['hash'], n + 1
        return {'ok': True, 'entries': n, 'head': prev[:16]}


AUDIT = AuditLog()


# ---------- two-person rule ----------
TWO_PERSON_KINDS = {'COHORT_ROLLBACK'}
TWO_PERSON_UNITS = 15          # actions touching more than about 10% of the fleet


def needs_two_people(action: dict) -> bool:
    return action.get('kind') in TWO_PERSON_KINDS or len(action.get('units') or []) > TWO_PERSON_UNITS


# ---------- HTTP security headers ----------
SECURITY_HEADERS = {
    'Content-Security-Policy': ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' "
                                "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; "
                                "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    'X-Frame-Options': 'DENY',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    'Cache-Control': 'no-store',
}
