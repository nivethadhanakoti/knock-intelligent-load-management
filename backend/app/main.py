"""KNOCK API: REST + WebSocket, plus the web console as static files.

Run:  uvicorn app.main:app --app-dir backend --port 8000   (or `make dev`)
"""
from __future__ import annotations

import asyncio
import logging
import time
import os
import traceback
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import json

from fastapi.concurrency import run_in_threadpool

from knock import engine as E
from knock import funding as F
from knock import regions as R
from knock.ai.copilot import LANG_NAMES
from knock.harness import run_pair, summary

from . import security as SEC
from .sessions import Registry, Session, classifier, classifier_status

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
FRONTEND = os.path.join(ROOT, 'frontend')
os.makedirs(os.path.join(ROOT, 'logs'), exist_ok=True)
logging.basicConfig(filename=os.path.join(ROOT, 'logs', 'knock.log'), level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('knock')
REG = Registry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        classifier()          # load the model, or start a background retrain if it is unusable on this machine
    except Exception as e:    # never block startup on the AI layer
        log.error('classifier init failed: %s', e)
    task = asyncio.create_task(REG.loop())
    yield
    task.cancel()


app = FastAPI(title='KNOCK', version='1.0', lifespan=lifespan)


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    log.error('Unhandled error on %s: %s\n%s', request.url.path, exc, traceback.format_exc())
    return JSONResponse(status_code=500, content={'error': 'internal_error', 'detail': str(exc)[:300],
                                                  'hint': 'The simulation keeps running. Details are in logs/knock.log.'})


def _session(sid: str) -> Session:
    try:
        return REG.get(sid)
    except KeyError:
        raise HTTPException(404, detail={'error': 'session_not_found', 'detail': 'No session %s.' % sid,
                                         'hint': 'Create one with POST /api/sessions.'})


# ---------- frames ----------
def look(run: E.Run, h: E.Home, side: str) -> str:
    """One-letter visual state per home, computed server-side so every client draws the same thing."""
    if h.dark:
        return 'd'
    if side == 'b':
        if h.islanded:
            return 'i'
        return 'x' if not h.tele else 'n'
    k = h.k
    if k.probing:
        return 'k'
    if h.truck or (k.top == 'BROKEN' and k.counted):
        return 't'
    if h.isolated or (k.top == 'ROGUE' and k.counted):
        return 'h'
    if h.islanded:
        return 'i'
    if any(a['kind'] == 'REVIEW' and a['status'] == 'AWAITING' for a in k.actions[:3]):
        return 'r'
    if h.slowed:
        return 's'
    if h.fwRolledBack and h.w.fw == E.BAD_FW:
        return 'f'
    if E._silent(run, h) and k.top == 'MUTE' and k.counted:
        return 'm'
    if E._silent(run, h):
        return 'u'
    return 'n'


def run_frame(run: E.Run, side: str) -> dict:
    t = run.tick
    xfs = []
    for X in run.xfs:
        badge = ''
        if X.deenergized:
            badge = 'PSPS SHUTOFF'
        elif t < X.fuseUntil:
            badge = 'FUSE BLOWN'
        elif t < X.shedUntil:
            badge = 'STREET CUT'
        elif X.probe:
            badge = 'KNOCKING'
        elif X.prechargeUntil > t and X.loading < 0.85:
            badge = 'PRE-CHARGING'
        xfs.append({'id': X.id, 'street': E.street(run, X.i), 'loading': round(X.loading, 3), 'volt': round(X.volt, 3),
                    'badge': badge, 'forecastPeak': round(X.forecastPeak, 2) if X.forecastPeak else None})
    return {'metrics': run.m.to_dict(), 'xfs': xfs, 'looks': ''.join(look(run, h, side) for h in run.homes),
            'soc': [round(h.soc, 2) for h in run.homes],
            'facility': run.facility and {'xf': run.facility['xf'], 'dark': run.facility['dark'], 'spoiled': run.m.spoiled}}


CATEGORIES = [
    ('knock', 'Knocks sent', ('PROBE',)),
    ('recover', 'Batteries recovered', ('RECOVER', 'RESET', 'CLEAR', 'REPAIRED')),
    ('slow', 'Slow-downs and prep', ('SLOW', 'PREP', 'RESTORE')),
    ('island', 'Island requests', ('ISLAND',)),
    ('hold', 'Holds (rogue)', ('ISOLATE',)),
    ('truck', 'Technicians', ('TRUCK',)),
    ('ai', 'AI actions', ('PREDICT', 'COHORT_ROLLBACK', 'INSIGHT', 'REVIEW')),
]
KIND_TO_CAT = {k: c for c, _, kinds in CATEGORIES for k in kinds}


def feed_counts(run: E.Run) -> list:
    tot = {c: [0, 0] for c, _, _ in CATEGORIES}
    for a in run.feed:
        c = KIND_TO_CAT.get(a['kind'])
        if c is None:
            continue
        if a.get('source') == 'ai':
            c = 'ai'
        tot[c][0] += 1
        if a['status'] == 'AWAITING':
            tot[c][1] += 1
    return [{'cat': c, 'label': lab, 'total': tot[c][0], 'awaiting': tot[c][1]} for c, lab, _ in CATEGORIES]


def action_cat(a: dict) -> Optional[str]:
    return 'ai' if a.get('source') == 'ai' else KIND_TO_CAT.get(a['kind'])


def action_view(run: E.Run, a: dict) -> dict:
    keep = ('id', 't', 'home', 'xf', 'rung', 'kind', 'title', 'text', 'status', 'source', 'units', 'urgent', 'auto', 'decidedAt', 'xfs', 'approvers')
    v = {k: a.get(k) for k in keep if k in a}
    v['clock'] = E.fmt_clock(run, a['t'])[:5]
    v['cat'] = action_cat(a)
    v['twoPerson'] = SEC.needs_two_people(a)
    return v


def frame(s: Session, feed_n: int = 60) -> dict:
    A, B, C = s.runs['knock_ai'], s.runs['baseline'], s.runs['knock']
    blog = [l for l in B.log if l['kind'] in ('bad', 'twist') or l['text'].startswith('Baseline')]
    events = [l for l in A.log if l['kind'] in ('event', 'twist')]
    ai = A.ai.status() if A.ai else None
    if ai:
        ai['insights'] = ai['insights'][:30]
    dr = s.scen.get('dr')
    minute = A.tick / E.TPM
    return {
        'id': s.id, 'scenario': s.scenario, 'scenarioName': s.scen['name'], 'tick': A.tick,
        'region': R.public(s.region), 'temp': R.temperature(R.get(s.region), E.heat_now(A)),
        'feedCounts': feed_counts(A), 'streets': A.scen['streets'],
        'total': s.scen['duration'] * E.TPM, 'clock': E.fmt_clock(A), 'tempF': round(80 + (E.heat_now(A) - 1) * 40),
        'drLive': bool(dr) and dr['from'] <= minute < dr['to'], 'playing': s.playing, 'speed': s.speed, 'done': s.done,
        'autoApproveTicks': s.auto_approve_ticks, 'emergencyAuto': A.policy['emergencyAuto'],
        'knock': run_frame(A, 'k'), 'baseline': run_frame(B, 'b'), 'core': {'metrics': C.m.to_dict()},
        'feed': [action_view(A, a) for a in A.feed[:feed_n]] + [action_view(A, a) for a in A.feed[feed_n:] if a.get('source') == 'ai'][:20],
        'awaiting': [action_view(A, a) for a in A.feed if a['status'] == 'AWAITING'][:20],
        'blog': [{'clock': E.fmt_clock(B, l['t'])[:5], 'text': l['text'], 'kind': l['kind']} for l in reversed(blog[-40:])],
        'events': [{'clock': E.fmt_clock(A, l['t'])[:5], 'text': l['text'], 'kind': l['kind']} for l in reversed(events[-20:])],
        'twists': [l['key'] for l in s.twist_log], 'twistLog': s.twist_log, 'ai': ai, 'classifierStatus': classifier_status(), 'copilot': s.copilot.mode, 'alerts': s.alerts[-3:],
    }


# ---------- REST ----------
class NewSession(BaseModel):
    scenario: str = 'crisis'
    seed: int = 42
    region: str = 'la'


class Control(BaseModel):
    cmd: str = Field(..., description='play | pause | step | speed | reset')
    value: Optional[float] = None
    scenario: Optional[str] = None
    region: Optional[str] = None


class Twist(BaseModel):
    key: str


class Decision(BaseModel):
    action_id: int
    approve: bool


class Policy(BaseModel):
    emergency_auto: Optional[bool] = None
    auto_approve_ticks: Optional[int] = None


class CopilotReq(BaseModel):
    task: str = 'ask'
    question: str = ''
    home: Optional[int] = None
    action_id: Optional[int] = None
    language: str = 'en'


@app.middleware('http')
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    for k, v in SEC.SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp


def _client(request: Request) -> str:
    return (request.client.host if request.client else 'unknown')


def _session_for(request: Request, sid: str, role: str = 'viewer') -> tuple:
    """Look up a session and check the caller may act on it (role and region tenancy)."""
    s = _session(sid)
    user = SEC.require(request, role, s.region)
    return s, user


# ---------- authentication ----------
class Login(BaseModel):
    username: str = Field(..., max_length=40)
    password: str = Field(..., max_length=200)


@app.post('/api/login')
def login(body: Login, request: Request):
    if not SEC.auth_enabled():
        return {'user': SEC.LOCAL_USER, 'authEnabled': False}
    if request.headers.get(SEC.CSRF_HEADER) != '1':
        raise HTTPException(403, detail={'error': 'csrf', 'detail': 'Request rejected: missing request header.'})
    ip = 'ip:' + _client(request)
    name = 'user:' + body.username.lower()
    if SEC.GUARD.blocked(name, ip):
        SEC.AUDIT.record(None, 'login_blocked', {'username': body.username[:40], 'ip': _client(request)})
        raise HTTPException(429, detail={'error': 'locked', 'detail': 'Too many failed sign-ins. Try again in 10 minutes.'})
    user = SEC.USERS.check(body.username, body.password)
    if user is None:
        SEC.GUARD.fail(name, ip)
        SEC.AUDIT.record(None, 'login_failed', {'username': body.username[:40], 'ip': _client(request)})
        raise HTTPException(401, detail={'error': 'bad_credentials', 'detail': 'Wrong user name or password.'})
    SEC.GUARD.reset(name, ip)
    tok = SEC.issue_token(user)
    SEC.AUDIT.record(user, 'login', {'ip': _client(request)})
    resp = JSONResponse({'user': {'name': user['name'], 'role': user['role'], 'regions': user['regions']}, 'authEnabled': True})
    resp.set_cookie(SEC.COOKIE, tok, max_age=SEC.SESSION_TTL_S, httponly=True, samesite='strict',
                    secure=request.url.scheme == 'https', path='/')
    return resp


@app.post('/api/logout')
def logout(request: Request):
    user = SEC.current_user(request)
    if user and user.get('nonce') not in (None, 'local'):
        SEC.REVOKED.add(user['nonce'])
        SEC.AUDIT.record(user, 'logout', {})
    resp = JSONResponse({'ok': True})
    resp.delete_cookie(SEC.COOKIE, path='/')
    return resp


@app.get('/api/me')
def me(request: Request):
    user = SEC.current_user(request)
    if user is None:
        raise HTTPException(401, detail={'error': 'login_required', 'detail': 'Sign in to use KNOCK.'})
    return {'user': {k: user[k] for k in ('name', 'role', 'regions')}, 'authEnabled': SEC.auth_enabled(),
            'permissions': SEC.PERMISSIONS[user['role']]}


# ---------- public ----------
@app.get('/api/health')
def health():
    return {'ok': True}


# ---------- data ----------
@app.get('/api/meta')
def meta(request: Request):
    SEC.require(request, 'viewer')
    try:
        c = classifier()
    except Exception:
        c = None
    return {'regions': [R.public(k) for k in R.ORDER], 'classifierStatus': classifier_status(), 'scenarios': ['crisis', 'tuesday'],
            'twists': E.TWISTS, 'languages': LANG_NAMES, 'streets': E.STREETS,
            'homes': [{'id': h.id, 'cls': h.cls, 'fw': h.fw} for h in E.build_world(42).homes],
            'classifier': c.meta if c else None, 'llmProvider': os.environ.get('LLM_PROVIDER', 'offline')}


@app.get('/api/sessions')
def list_sessions(request: Request, region: Optional[str] = None):
    """Sessions this user may see: the shared dispatch room for each region they are authorized for."""
    user = SEC.require(request, 'viewer')
    out = []
    for s in sorted(REG.sessions.values(), key=lambda x: -x.last_seen):
        if SEC.region_allowed(user, s.region) and (region is None or s.region == region):
            out.append({'id': s.id, 'region': s.region, 'scenario': s.scenario, 'clock': E.fmt_clock(s.A)[:5], 'done': s.done})
    return {'sessions': out}


@app.post('/api/sessions')
def create(body: NewSession, request: Request):
    user = SEC.require(request, 'dispatcher', body.region if body.region in R.REGIONS else None)
    try:
        s = REG.create(body.scenario, body.seed, body.region)
    except ValueError as e:
        raise HTTPException(400, detail={'error': 'bad_request', 'detail': str(e)})
    SEC.AUDIT.record(user, 'session_create', {'session': s.id, 'region': s.region, 'scenario': s.scenario})
    return frame(s)


@app.get('/api/sessions/{sid}')
def get_frame(sid: str, request: Request):
    s, _ = _session_for(request, sid)
    return frame(s)


@app.post('/api/sessions/{sid}/control')
def control(sid: str, body: Control, request: Request):
    need = 'supervisor' if body.cmd == 'reset' else 'dispatcher'
    s, user = _session_for(request, sid, need)
    if body.cmd == 'reset' and body.region and body.region in R.REGIONS and not SEC.region_allowed(user, body.region):
        raise HTTPException(403, detail={'error': 'region_forbidden', 'detail': 'Your account is not authorized for that region.'})
    if body.cmd == 'play':
        s.playing = not s.done
    elif body.cmd == 'pause':
        s.playing = False
    elif body.cmd == 'step':
        s.playing = False
        s.step(max(1, min(int(body.value or E.TPM), 600)))
    elif body.cmd == 'speed':
        if not body.value or body.value <= 0 or body.value > 60:
            raise HTTPException(400, detail={'error': 'bad_speed', 'detail': 'Speed must be 1-60 ticks per second.'})
        s.speed = float(body.value)
    elif body.cmd == 'reset':
        old = s.region
        try:
            s.reset(body.scenario, body.region)
        except ValueError as e:
            raise HTTPException(400, detail={'error': 'bad_request', 'detail': str(e)})
        SEC.AUDIT.record(user, 'session_reset', {'session': s.id, 'region': s.region, 'from': old, 'scenario': s.scenario})
    else:
        raise HTTPException(400, detail={'error': 'bad_command', 'detail': "Unknown cmd '%s'." % body.cmd,
                                         'hint': 'Use play, pause, step, speed or reset.'})
    return frame(s)


@app.post('/api/sessions/{sid}/twist')
def twist(sid: str, body: Twist, request: Request):
    s, user = _session_for(request, sid, 'supervisor')
    try:
        s.twist(body.key)
    except ValueError as e:
        raise HTTPException(400, detail={'error': 'bad_twist', 'detail': str(e)})
    SEC.AUDIT.record(user, 'disaster_injected', {'session': s.id, 'region': s.region, 'key': body.key, 'clock': E.fmt_clock(s.A)[:5]})
    return frame(s)


@app.post('/api/sessions/{sid}/decide')
def decide(sid: str, body: Decision, request: Request):
    s, user = _session_for(request, sid, 'dispatcher')
    with s.lock:
        a = next((x for x in s.A.feed if x['id'] == body.action_id), None)
        if a is None or a['status'] != 'AWAITING':
            raise HTTPException(409, detail={'error': 'not_pending', 'detail': 'Action %d is not waiting for a decision.' % body.action_id})
        detail = {'session': s.id, 'region': s.region, 'action': a['id'], 'kind': a['kind'], 'title': a['title'][:120],
                  'units': len(a.get('units') or []) or (1 if a.get('home') is not None else 0)}
        if body.approve and SEC.needs_two_people(a):
            approvers = a.setdefault('approvers', [])
            if user['name'] in approvers:
                raise HTTPException(409, detail={'error': 'second_person_needed',
                                                 'detail': 'You already approved this. A second, different person must approve it too.'})
            approvers.append(user['name'])
            if len(approvers) < 2:
                SEC.AUDIT.record(user, 'approve_1_of_2', detail)
                return frame(s)
            SEC.AUDIT.record(user, 'approve_2_of_2', dict(detail, approvers=list(approvers)))
        else:
            SEC.AUDIT.record(user, 'approve' if body.approve else 'deny', detail)
        s.decide(body.action_id, body.approve)
    return frame(s)


@app.post('/api/sessions/{sid}/policy')
def policy(sid: str, body: Policy, request: Request):
    s, user = _session_for(request, sid, 'supervisor')
    if body.auto_approve_ticks is not None and not 0 <= body.auto_approve_ticks <= 360:
        raise HTTPException(400, detail={'error': 'bad_policy', 'detail': 'auto_approve_ticks must be 0-360.'})
    s.set_policy(body.emergency_auto, body.auto_approve_ticks)
    SEC.AUDIT.record(user, 'policy_change', {'session': s.id, 'region': s.region, 'emergencyAuto': body.emergency_auto,
                                             'autoApproveTicks': body.auto_approve_ticks})
    return frame(s)


@app.get('/api/sessions/{sid}/homes/{idx}')
def home(sid: str, idx: int, request: Request, truth: bool = False):
    s, user = _session_for(request, sid, 'supervisor' if truth else 'viewer')
    if not 0 <= idx < E.N_XF * E.PER_XF:
        raise HTTPException(404, detail={'error': 'no_such_home', 'detail': 'Homes are numbered 0-143.'})
    with s.lock:
        A, B = s.runs['knock_ai'], s.runs['baseline']
        h, hb = A.homes[idx], B.homes[idx]
        lm = A.ai.load if A.ai else None
        k = h.k
        out = {
            'idx': idx, 'id': h.w.id, 'street': E.street(A, h.w.xf), 'xf': h.w.xf, 'cls': h.w.cls, 'classLabel': E.CLASS_LABEL[h.w.cls],
            'fw': h.w.fw, 'fwRolledBack': h.fwRolledBack, 'cap': h.w.cap, 'pmax': h.w.pmax, 'soc': h.soc, 'load': h.load, 'p': h.p,
            'reporting': not E._silent(A, h), 'patience': E.patience(A, h),
            'belief': {'post': dict(zip(E.HYP, k.post)), 'top': k.top, 'conf': k.conf, 'evidence': k.evidence},
            'model': k.ml and {'top': k.ml['top'], 'conf': k.ml['conf'], 'probs': k.ml['probs'], 'agree': k.ml.get('agree')},
            'probe': k.probe, 'hist': h.hist[-60:],
            'forecast': {'learned': lm.predict_home(A, h) if lm else None, 'samples': int(lm.count[idx]) if lm else 0,
                         'theta': [round(float(v), 3) for v in lm.theta[idx]] if lm else None},
            'actions': [action_view(A, a) for a in k.actions[:6]],
            'baselineView': 'No power' if hb.dark else 'Grid cut, running on own battery' if hb.islanded else
                            ('Reporting' if hb.tele else 'Presumed dead (silent)'),
        }
        if truth:
            out['truth'] = h.state
    if truth:
        SEC.AUDIT.record(user, 'truth_revealed', {'session': s.id, 'region': s.region, 'home': h.w.id})
    return out


@app.post('/api/sessions/{sid}/copilot')
def copilot(sid: str, body: CopilotReq, request: Request):
    s, user = _session_for(request, sid, 'dispatcher')
    if body.task not in ('ask', 'explain', 'brief', 'homeowner', 'pitch'):
        raise HTTPException(400, detail={'error': 'bad_task', 'detail': 'task must be ask, explain, brief, homeowner or pitch.'})
    if body.task == 'ask' and not body.question.strip():
        raise HTTPException(400, detail={'error': 'empty_question', 'detail': 'Type a question first.'})
    if body.task == 'homeowner' and body.home is None:
        raise HTTPException(400, detail={'error': 'no_home', 'detail': 'Pick a home on the map first.'})
    with s.lock:
        out = s.copilot.run(s, body.task, body.question[:500], body.home, body.action_id, body.language)
    SEC.AUDIT.record(user, 'copilot', {'session': s.id, 'region': s.region, 'task': body.task, 'provider': out['provider'],
                                       'questionChars': len(body.question)})
    return out


@app.get('/api/sessions/{sid}/feed')
def feed(sid: str, request: Request, cat: Optional[str] = None, awaiting: bool = False, limit: int = 150):
    """The full decision list, optionally filtered by chart category or by 'needs you'."""
    s, _ = _session_for(request, sid)
    if cat and cat not in KIND_TO_CAT.values() and cat != 'ai':
        raise HTTPException(400, detail={'error': 'bad_category', 'detail': "Unknown category '%s'." % cat})
    with s.lock:
        A = s.A
        items = [a for a in A.feed if (not cat or action_cat(a) == cat) and (not awaiting or a['status'] == 'AWAITING')]
        return {'total': len(items), 'items': [action_view(A, a) for a in items[:max(1, min(limit, 500))]]}


REPORT_CACHE: dict = {}


def _full_report(scenario: str, region: str, twists: tuple) -> dict:
    key = (scenario, region, twists)
    if key in REPORT_CACHE:
        return REPORT_CACHE[key]
    runs = run_pair(scenario, region=region, twists=list(twists), modes=('knock_ai', 'knock', 'baseline'))
    a = runs['knock_ai']
    out = {'scenario': scenario, 'region': region, 'twists': [list(t) for t in twists],
           'summary': {m: summary(r) for m, r in runs.items()},
           'insights': [{'clock': i['clock'][:5], 'title': i['title'], 'text': i['text'], 'severity': i['severity']}
                        for i in a.ai.insights.items[::-1]],
           'note': 'Simulated to the end of the afternoon on the same seed; approvals granted after 5 minutes.'}
    if len(REPORT_CACHE) > 64:
        REPORT_CACHE.clear()
    REPORT_CACHE[key] = out
    return out


@app.post('/api/sessions/{sid}/report')
async def report(sid: str, request: Request):
    """Fast-forward this session's scenario, region and twists to the end, headless (what `make demo` prints)."""
    s, _ = _session_for(request, sid, 'viewer')
    twists = tuple((int(t['tick'] // E.TPM), t['key']) for t in s.twist_log)
    return await run_in_threadpool(_full_report, s.scenario, s.region, twists)


GLOBAL_PATH = os.path.join(ROOT, 'models', 'global_results.json')


@app.get('/api/regions')
def regions_list(request: Request):
    user = SEC.require(request, 'viewer')
    data = None
    if os.path.exists(GLOBAL_PATH):
        try:
            with open(GLOBAL_PATH) as f:
                data = json.load(f)
        except Exception:
            data = None
    regs = [dict(R.public(k), allowed=SEC.region_allowed(user, k)) for k in R.ORDER]
    return {'regions': regs, 'results': data}


@app.get('/api/sessions/{sid}/funding')
def funding(sid: str, request: Request):
    s, _ = _session_for(request, sid)
    c = classifier()
    with s.lock:
        ev = F.evidence(s.A.m.to_dict(), s.runs['baseline'].m.to_dict(), c.meta if c else None)
    return {'programs': F.PROGRAMS, 'notAFit': F.NOT_A_FIT, 'steps': F.STEPS, 'context': F.CONTEXT, 'evidence': ev,
            'clock': E.fmt_clock(s.A)[:5], 'scenarioName': s.scen['name'], 'region': R.public(s.region)['name'],
            'checked': '2026-10-17'}


# ---------- security posture and audit ----------
@app.get('/api/security')
def security_posture(request: Request):
    user = SEC.require(request, 'viewer')
    from knock.ai.classifier import FaultClassifier
    model_ok = classifier() is not None
    return {
        'me': {k: user[k] for k in ('name', 'role', 'regions')}, 'permissions': SEC.PERMISSIONS,
        'authEnabled': SEC.auth_enabled(), 'audit': SEC.AUDIT.verify(),
        'model': {'signedAndVerified': model_ok, 'status': classifier_status(), 'lastError': FaultClassifier.last_error},
        'controls': [
            {'name': 'Sign-in required', 'on': SEC.auth_enabled(), 'detail': 'PBKDF2-SHA256 password hashes; signed, expiring, HttpOnly session cookies'},
            {'name': 'Roles', 'on': True, 'detail': 'viewer < dispatcher < supervisor, checked on every API call'},
            {'name': 'Region tenancy', 'on': True, 'detail': 'each account is scoped to the regions it may see and act on'},
            {'name': 'Two-person rule', 'on': True, 'detail': 'firmware rollbacks and actions on more than %d units need two different approvers' % SEC.TWO_PERSON_UNITS},
            {'name': 'Tamper-evident audit log', 'on': True, 'detail': 'hash-chained, append-only; verified below'},
            {'name': 'Brute-force and flood limits', 'on': True, 'detail': 'lockout after 5 failed sign-ins; per-user request limits'},
            {'name': 'Signed model file', 'on': model_ok, 'detail': 'the model is verified before loading; foreign or altered files are refused'},
            {'name': 'Browser protections', 'on': True, 'detail': 'SameSite=Strict cookie, required request header, CSP, no framing'},
            {'name': 'Command safety envelope', 'on': True, 'detail': 'knocks capped at 0.5 kW and net zero; never on a stressed transformer; life-support homes never auto-islanded'},
            {'name': 'Copilot cannot act', 'on': True, 'detail': 'read-only, data fenced as untrusted, action claims flagged'},
        ],
        'twoPersonKinds': sorted(SEC.TWO_PERSON_KINDS),
    }


@app.get('/api/audit')
def audit(request: Request, region: Optional[str] = None, n: int = 100):
    user = SEC.require(request, 'viewer')
    if region and not SEC.region_allowed(user, region):
        raise HTTPException(403, detail={'error': 'region_forbidden', 'detail': 'Not authorized for that region.'})
    entries = SEC.AUDIT.tail(max(1, min(n, 500)), region)
    if SEC.ALL_REGIONS not in user['regions']:
        entries = [e for e in entries if e['detail'].get('region') in user['regions']]
    return {'entries': entries, 'verify': SEC.AUDIT.verify()}


# ---------- WebSocket ----------
@app.websocket('/api/sessions/{sid}/stream')
async def stream(ws: WebSocket, sid: str):
    # Same-origin only (blocks cross-site WebSocket hijacking), and the same cookie, role and region checks as REST.
    origin = ws.headers.get('origin')
    host = ws.headers.get('host')
    if origin and host and origin.split('://', 1)[-1] != host:
        await ws.close(code=4403)
        return
    user = SEC.LOCAL_USER if not SEC.auth_enabled() else SEC.read_token(ws.cookies.get(SEC.COOKIE, ''))
    s = REG.sessions.get(sid)
    await ws.accept()
    if user is None:
        await ws.send_json({'error': 'login_required'})
        await ws.close(code=4401)
        return
    if s is None:
        await ws.send_json({'error': 'session_not_found'})
        await ws.close()
        return
    if not SEC.region_allowed(user, s.region):
        await ws.send_json({'error': 'region_forbidden'})
        await ws.close(code=4403)
        return
    last_sig = None
    try:
        while True:
            if not SEC.region_allowed(user, s.region):        # a supervisor switched this room to another region
                await ws.send_json({'error': 'region_forbidden'})
                await ws.close(code=4403)
                return
            sig = (s.A.tick, s.playing, s.region, len(s.A.feed) and s.A.feed[0]['id'],
                   sum(1 for a in s.A.feed[:40] if a['status'] == 'AWAITING'),
                   sum(len(a.get('approvers', [])) for a in s.A.feed[:40]))
            if sig != last_sig:
                with s.lock:
                    f = frame(s)
                await ws.send_json(f)
                last_sig = sig
                s.last_seen = time.time()
            await asyncio.sleep(0.12)
    except (WebSocketDisconnect, RuntimeError):
        return


# ---------- console ----------
if os.path.isdir(FRONTEND):
    app.mount('/static', StaticFiles(directory=FRONTEND), name='static')


@app.get('/')
def index():
    return FileResponse(os.path.join(FRONTEND, 'index.html'))
