"""KNOCK test suite: engine correctness, safety properties, AI layer, copilot guardrails, API."""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from knock import engine as E                       # noqa: E402
from knock.harness import run_pair, summary          # noqa: E402
from knock.rng import Mulberry32                     # noqa: E402


# ---------- signed-in API clients on throwaway user, audit and key files ----------
@pytest.fixture()
def secure_app(tmp_path, monkeypatch):
    from app import security as SEC
    monkeypatch.setenv('KNOCK_AUTH', 'on')
    monkeypatch.setattr(SEC, 'USERS', SEC.UserStore(str(tmp_path / 'users.json')))
    monkeypatch.setattr(SEC, 'AUDIT', SEC.AuditLog(str(tmp_path / 'audit.jsonl')))
    monkeypatch.setattr(SEC, 'GUARD', SEC.LoginGuard())
    monkeypatch.setattr(SEC, 'RATE', SEC.RateLimiter(limit=1000))
    for name, role, regions in [('sup', 'supervisor', ['*']), ('disp', 'dispatcher', ['*']), ('disp2', 'dispatcher', ['*']),
                                ('view', 'viewer', ['*']), ('jhb', 'dispatcher', ['jhb'])]:
        SEC.USERS.add(name, 'correct-horse-' + name, role, regions)
    return SEC


def signed_in(name):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    c.headers.update({'X-Knock-Request': '1'})
    r = c.post('/api/login', json={'username': name, 'password': 'correct-horse-' + name})
    assert r.status_code == 200, r.text
    return c


# ---------- shared runs (computed once; each full run takes about a second) ----------
@pytest.fixture(scope='module')
def crisis():
    return run_pair('crisis', modes=('knock', 'knock_ai', 'baseline'))


@pytest.fixture(scope='module')
def tuesday():
    return run_pair('tuesday', modes=('knock', 'knock_ai', 'baseline'))


# ---------- engine ----------
def test_rng_matches_javascript_reference():
    r = Mulberry32(42)
    assert [round(r(), 12) for _ in range(3)] == [0.60110375192, 0.448290558998, 0.852465793490]


def test_world_is_deterministic():
    a, b = E.build_world(42), E.build_world(42)
    assert [(h.cls, round(h.base, 6), h.fw) for h in a.homes] == [(h.cls, round(h.base, 6), h.fw) for h in b.homes]
    assert sum(1 for h in a.homes if h.fw == E.BAD_FW) == 12
    assert any(h.cls == 'LIFE' for h in a.homes if h.xf == 3)


def test_tuesday_reproduces_reference_numbers(tuesday):
    k, b = summary(tuesday['knock']), summary(tuesday['baseline'])
    assert k['recovered'] == 14 and k['trucks'] == 1 and k['maint'] == 1
    assert b['trucks'] == 15
    assert abs(k['dr'] - 484) <= 5 and abs(b['dr'] - 442) <= 5
    assert k['dr'] > b['dr']


def test_crisis_knock_keeps_everyone_powered(crisis):
    k, b = summary(crisis['knock']), summary(crisis['baseline'])
    assert k['darkPeak'] == 0 and k['crit'] == 0 and k['fuses'] == 0
    assert b['darkPeak'] >= 10 and b['crit'] >= 3
    assert k['trucks'] < b['trucks']
    assert k['rogueS'] is not None and k['rogueS'] <= 600


def test_diagnoses_match_truth_after_knocks(crisis):
    run = crisis['knock']
    acted = [a for a in run.feed if a['kind'] in ('RECOVER', 'ISOLATE') and a.get('home') is not None]
    assert acted, 'expected KNOCK to act on some diagnoses'
    # a recovered unit must have been mute or reset; an isolated unit must truly be rogue
    for a in acted:
        h = run.homes[a['home']]
        if a['kind'] == 'ISOLATE':
            assert h.state == 'ROGUE'


def test_life_support_and_medical_never_proposed_for_islanding():
    runs = run_pair('crisis', twists=[(50, 'facility'), (95, 'heat_spike')], modes=('knock',))
    run = runs['knock']
    islands = [a for a in run.feed if a['kind'] == 'ISLAND']
    assert islands, 'compound twist should push KNOCK to the island rung'
    for a in islands:
        for i in a['units']:
            assert run.homes[i].w.cls not in ('LIFE', 'MED')


def test_ladder_does_not_oscillate(crisis):
    run = crisis['knock']
    slows = [a for a in run.feed if a['kind'] == 'SLOW']
    for x in range(E.N_XF):
        ticks = sorted(a['t'] for a in slows if a['xf'] == x)
        for i, t in enumerate(ticks):
            in_window = [u for u in ticks[i:] if u - t <= 30 * E.TPM]
            assert len(in_window) <= 2, 'T%d slowed too often' % (x + 1)


@pytest.mark.parametrize('minute', [5, 40, 100])
def test_psps_preparation_never_blows_a_fuse(minute):
    runs = run_pair('crisis', twists=[(minute, 'psps')], modes=('knock',))
    assert runs['knock'].m.fuseTrips == 0


def test_emergency_policy_prevents_fuses_without_a_human():
    def go(policy):
        world = E.build_world(42)
        scen = E.scenario_defs(world)['crisis']
        r = E.make_run(world, scen, 'knock')
        r.policy['emergencyAuto'] = policy
        for i in range(scen['duration'] * E.TPM):
            if i == 50 * E.TPM:
                E.inject_twist(r, 'facility')
            if i == 95 * E.TPM:
                E.inject_twist(r, 'heat_spike')
            E.step(r)
        return r.m.fuseTrips
    assert go(True) == 0
    assert go(False) >= 1


def test_unknown_twist_is_rejected_clearly():
    world = E.build_world(42)
    r = E.make_run(world, E.scenario_defs(world)['crisis'], 'knock')
    with pytest.raises(ValueError, match='Unknown twist'):
        E.inject_twist(r, 'meteor')


# ---------- AI layer ----------
def test_ai_cohort_rollback_prevents_later_rogue_waves(crisis):
    ai, core = crisis['knock_ai'], crisis['knock']
    assert core.m.roguesActivated == 12
    assert ai.m.roguesActivated == 3 and ai.m.roguesPrevented == 9
    assert any(a['kind'] == 'COHORT_ROLLBACK' for a in ai.feed)


def test_ai_does_not_make_outcomes_worse(crisis):
    ai, core = summary(crisis['knock_ai']), summary(crisis['knock'])
    assert ai['darkPeak'] <= core['darkPeak'] and ai['crit'] <= core['crit'] and ai['fuses'] <= core['fuses']
    assert ai['slowMin'] <= core['slowMin']


def test_forecaster_learns_each_home(crisis):
    q = crisis['knock_ai'].ai.load.quality()
    assert q['homesLearned'] >= 120
    assert q['meanAbsErrorKw'] < 0.4


def test_no_false_clear_loops(crisis):
    assert sum(1 for a in crisis['knock_ai'].feed if a['kind'] == 'CLEAR') < 10


def test_classifier_is_trained_and_honest():
    from knock.ai.classifier import FaultClassifier
    c = FaultClassifier.load()
    assert c is not None, 'run scripts/train_models.py first'
    cmp = c.meta['comparison']
    assert cmp['accuracyWhenAgree'] >= cmp['bayesAccuracy'] - 0.01
    assert cmp['agreementRate'] > 0.9


def test_ai_component_failure_is_contained():
    from knock.ai.layer import AILayer

    class Broken(AILayer):
        def forecast(self, run, h):
            raise RuntimeError('boom')

    world = E.build_world(42)
    r = E.make_run(world, E.scenario_defs(world)['crisis'], 'knock')
    r.ai = Broken(classifier=None)
    for _ in range(300):
        E.step(r)
    assert r.tick == 300
    assert 'forecast' in r.ai.disabled


# ---------- copilot ----------
def test_copilot_offline_answers_from_data_and_guard():
    from app.sessions import Session
    from knock.ai import copilot as C
    s = Session('crisis')
    s.step(500)
    out = s.copilot.run(s, 'brief')
    assert out['provider'] == 'offline' and 'KNOCK' in out['text']
    es = s.copilot.run(s, 'homeowner', home=20, language='es')
    assert 'batería' in es['text']
    assert 'cannot approve' in C._guard('I approved the hold on H103.')


# ---------- API ----------
def test_api_smoke(secure_app):
    c = signed_in('sup')
    with c:
        assert c.get('/api/health').json()['ok']
        f = c.post('/api/sessions', json={'scenario': 'crisis'}).json()
        sid = f['id']
        f = c.post('/api/sessions/%s/control' % sid, json={'cmd': 'step', 'value': 300}).json()
        assert f['tick'] == 300 and len(f['knock']['looks']) == 144
        assert c.post('/api/sessions/%s/twist' % sid, json={'key': 'heat_spike'}).status_code == 200
        assert c.post('/api/sessions/%s/twist' % sid, json={'key': 'nope'}).status_code == 400
        assert c.get('/api/sessions/%s/homes/49' % sid).json()['id'] == 'H050'
        assert c.post('/api/sessions/%s/copilot' % sid, json={'task': 'ask', 'question': ''}).status_code == 400
        assert c.post('/api/sessions/%s/decide' % sid, json={'action_id': 999999, 'approve': True}).status_code == 409
        assert c.get('/api/sessions/doesnotexist').status_code == 404


# ---------- Twist 2: regions ----------
def test_every_region_builds_a_distinct_crisis():
    from knock import regions as R
    world = E.build_world(42)
    names = set()
    for key in R.ORDER:
        sc = E.scenario_defs(world, key)['crisis']
        assert sc['region'] == key and len(sc['streets']) == E.N_XF and sc['events']
        names.add(sc['name'])
    assert len(names) == len(R.ORDER)
    with pytest.raises(ValueError):
        E.scenario_defs(world, 'atlantis')


def test_los_angeles_unchanged_by_region_support():
    world = E.build_world(42)
    assert E.scenario_defs(world) == E.scenario_defs(world, 'la')


def test_johannesburg_load_reduction_knock_beats_blunt_move():
    runs = run_pair('crisis', region='jhb', modes=('knock_ai', 'baseline'))
    k, b = summary(runs['knock_ai']), summary(runs['baseline'])
    assert k['darkPeak'] == 0 and k['crit'] == 0
    assert b['darkPeak'] >= 10
    assert any('load reduction' in l['text'] and '120 min' in l['text'] for l in runs['baseline'].log)
    assert any(a['kind'] == 'PREP' for a in runs['knock_ai'].feed)   # pre-charged for the announced cut


def test_global_results_file_is_present_and_consistent():
    import json
    path = os.path.join(os.path.dirname(__file__), '..', '..', 'models', 'global_results.json')
    data = json.load(open(path))
    from knock import regions as R
    for key in R.ORDER:
        s = data['regions'][key]['summary']
        assert s['knock_ai']['darkPeak'] <= s['baseline']['darkPeak']
        assert s['knock_ai']['crit'] <= s['baseline']['crit']


# ---------- Twist 1: funding ----------
def test_funding_evidence_and_pitch():
    from knock import funding as F
    from app.sessions import Session
    s = Session('crisis')
    s.step(600)
    ev = F.evidence(s.A.m.to_dict(), s.runs['baseline'].m.to_dict(), None)
    assert ev['darkPeak']['knock'] <= ev['darkPeak']['usual']
    assert {p['id'] for p in F.PROGRAMS} >= {'nsf', 'estcp'}
    out = s.copilot.run(s, 'pitch')
    assert 'PROJECT PITCH' in out['text'] and 'Santa Ana' in out['text']


# ---------- dashboard endpoints ----------
def test_feed_filter_and_report_endpoints(secure_app):
    with signed_in('sup') as c:
        sid = c.post('/api/sessions', json={'scenario': 'crisis', 'region': 'jhb'}).json()['id']
        f = c.post('/api/sessions/%s/control' % sid, json={'cmd': 'step', 'value': 400}).json()
        counts = {x['cat']: x['total'] for x in f['feedCounts']}
        assert counts['knock'] > 0
        r = c.get('/api/sessions/%s/feed?cat=knock' % sid).json()
        assert r['total'] == counts['knock'] and all(a['cat'] == 'knock' for a in r['items'])
        assert c.get('/api/sessions/%s/feed?cat=nope' % sid).status_code == 400
        rep = c.post('/api/sessions/%s/report' % sid).json()
        assert rep['summary']['knock_ai']['darkPeak'] <= rep['summary']['baseline']['darkPeak']
        assert c.get('/api/regions').json()['regions'][0]['key'] == 'la'
        assert c.get('/api/sessions/%s/funding' % sid).json()['programs']


# ---------- security ----------
def test_api_requires_sign_in(secure_app):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        assert c.get('/api/meta').status_code == 401
        assert c.post('/api/sessions', json={'scenario': 'crisis'}, headers={'X-Knock-Request': '1'}).status_code == 401
        assert c.post('/api/login', json={'username': 'sup', 'password': 'wrong-password'}, headers={'X-Knock-Request': '1'}).status_code == 401
        assert c.get('/').status_code == 200                        # the sign-in page itself is public
        assert c.get('/').headers['x-frame-options'] == 'DENY'


def test_forged_or_tampered_cookie_is_rejected(secure_app):
    from fastapi.testclient import TestClient
    from app.main import app
    good = secure_app.issue_token({'name': 'view', 'role': 'viewer', 'regions': ['*']})
    body, sig = good.split('.')
    import base64, json as _j
    raw = _j.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    raw['r'] = 'supervisor'                                          # try to promote yourself
    forged = base64.urlsafe_b64encode(_j.dumps(raw).encode()).decode().rstrip('=') + '.' + sig
    assert secure_app.read_token(forged) is None
    assert secure_app.read_token(good)['role'] == 'viewer'
    with TestClient(app) as c:
        c.cookies.set(secure_app.COOKIE, forged)
        assert c.get('/api/me').status_code == 401


def test_roles_are_enforced(secure_app):
    sup = signed_in('sup')
    sid = sup.post('/api/sessions', json={'scenario': 'crisis'}).json()['id']
    view, disp = signed_in('view'), signed_in('disp')
    assert view.get('/api/sessions/%s' % sid).status_code == 200                                     # can watch
    assert view.post('/api/sessions/%s/control' % sid, json={'cmd': 'play'}).status_code == 403      # cannot drive
    assert view.post('/api/sessions/%s/copilot' % sid, json={'task': 'brief'}).status_code == 403
    assert disp.post('/api/sessions/%s/control' % sid, json={'cmd': 'step', 'value': 6}).status_code == 200
    assert disp.post('/api/sessions/%s/twist' % sid, json={'key': 'heat_spike'}).status_code == 403  # supervisor only
    assert disp.post('/api/sessions/%s/policy' % sid, json={'auto_approve_ticks': 36}).status_code == 403
    assert disp.get('/api/sessions/%s/homes/3?truth=true' % sid).status_code == 403
    assert sup.get('/api/sessions/%s/homes/3?truth=true' % sid).status_code == 200


def test_csrf_header_required_for_changes(secure_app):
    c = signed_in('sup')
    sid = c.post('/api/sessions', json={'scenario': 'crisis'}).json()['id']
    del c.headers['X-Knock-Request']
    assert c.post('/api/sessions/%s/control' % sid, json={'cmd': 'play'}).status_code == 403


def test_region_tenancy(secure_app):
    sup = signed_in('sup')
    la = sup.post('/api/sessions', json={'scenario': 'crisis', 'region': 'la'}).json()['id']
    jhb_room = sup.post('/api/sessions', json={'scenario': 'crisis', 'region': 'jhb'}).json()['id']
    j = signed_in('jhb')
    assert j.get('/api/sessions/%s' % la).status_code == 403
    assert j.get('/api/sessions/%s' % jhb_room).status_code == 200
    assert j.post('/api/sessions', json={'scenario': 'crisis', 'region': 'la'}).status_code == 403
    assert {x['region'] for x in j.get('/api/sessions').json()['sessions']} == {'jhb'}


def test_two_person_rule_for_firmware_rollback(secure_app):
    sup = signed_in('sup')
    sid = sup.post('/api/sessions', json={'scenario': 'crisis'}).json()['id']
    f = None
    for _ in range(14):                                               # run to about 16:20, past the rollback proposal
        f = sup.post('/api/sessions/%s/control' % sid, json={'cmd': 'step', 'value': 60}).json()
    roll = [a for a in f['awaiting'] if a['kind'] == 'COHORT_ROLLBACK']
    assert roll and roll[0]['twoPerson']
    aid = roll[0]['id']
    d1, d2 = signed_in('disp'), signed_in('disp2')
    r1 = d1.post('/api/sessions/%s/decide' % sid, json={'action_id': aid, 'approve': True}).json()
    assert next(a for a in r1['awaiting'] if a['id'] == aid)['approvers'] == ['disp']               # still pending
    assert d1.post('/api/sessions/%s/decide' % sid, json={'action_id': aid, 'approve': True}).status_code == 409  # same person
    r2 = d2.post('/api/sessions/%s/decide' % sid, json={'action_id': aid, 'approve': True}).json()
    assert all(a['id'] != aid for a in r2['awaiting'])                                               # applied
    acts = [e['action'] for e in sup.get('/api/audit').json()['entries']]
    assert 'approve_1_of_2' in acts and 'approve_2_of_2' in acts


def test_audit_log_is_tamper_evident(secure_app, tmp_path):
    log = secure_app.AuditLog(str(tmp_path / 'a.jsonl'))
    for i in range(5):
        log.record({'name': 'disp', 'role': 'dispatcher'}, 'approve', {'action': i})
    assert log.verify() == {'ok': True, 'entries': 5, 'head': log.last_hash[:16]}
    lines = open(log.path).read().splitlines()
    lines[2] = lines[2].replace('"action":2', '"action":99')         # quietly rewrite history
    open(log.path, 'w').write('\n'.join(lines) + '\n')
    v = secure_app.AuditLog(str(tmp_path / 'a.jsonl')).verify()
    assert v['ok'] is False and v['brokenAt'] == 3


def test_login_lockout(secure_app):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        h = {'X-Knock-Request': '1'}
        for _ in range(5):
            assert c.post('/api/login', json={'username': 'disp', 'password': 'nope-nope-nope'}, headers=h).status_code == 401
        r = c.post('/api/login', json={'username': 'disp', 'password': 'correct-horse-disp'}, headers=h)
        assert r.status_code == 429                                   # even the right password waits out the lockout


def test_model_file_is_verified_before_loading(tmp_path):
    import shutil
    from knock.ai.classifier import FaultClassifier, DEFAULT_PATH
    if FaultClassifier.load() is None:
        pytest.skip('no trained model on this machine yet (make test trains one)')
    p = tmp_path / 'm.pkl'
    shutil.copy(DEFAULT_PATH, p)
    shutil.copy(DEFAULT_PATH + '.sig', str(p) + '.sig')
    assert FaultClassifier.load(str(p)) is not None
    with open(p, 'ab') as f:
        f.write(b'tampered')
    assert FaultClassifier.load(str(p)) is None and 'signature' in FaultClassifier.last_error
    (tmp_path / 'm.pkl.sig').unlink()
    shutil.copy(DEFAULT_PATH, p)
    assert FaultClassifier.load(str(p)) is None and 'unsigned' in FaultClassifier.last_error


def test_two_person_actions_are_never_auto_approved(secure_app):
    sup = signed_in('sup')
    sid = sup.post('/api/sessions', json={'scenario': 'crisis'}).json()['id']
    sup.post('/api/sessions/%s/policy' % sid, json={'auto_approve_ticks': 6})
    f = None
    for _ in range(16):
        f = sup.post('/api/sessions/%s/control' % sid, json={'cmd': 'step', 'value': 60}).json()
    assert any(a['kind'] == 'COHORT_ROLLBACK' for a in f['awaiting'])
    assert not any(a['kind'] != 'COHORT_ROLLBACK' and not a.get('twoPerson') for a in f['awaiting'])
