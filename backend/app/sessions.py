"""Simulation sessions: three controllers on one world, stepped together by a background task.

* knock_ai  - KNOCK with the AI layer. This is the one the dispatcher operates.
* knock     - core KNOCK without AI, a hidden shadow run that measures what the AI layer adds.
* baseline  - the blunt operator.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Dict, List, Optional

from knock import engine as E
from knock.ai.classifier import FaultClassifier
from knock.ai.copilot import Copilot
from knock.ai.layer import AILayer

_CLASSIFIER: Optional[FaultClassifier] = None
_STATUS = {'state': 'unknown', 'detail': ''}
_TRAIN_LOCK = threading.Lock()


def classifier() -> Optional[FaultClassifier]:
    """The shared fault classifier. If the saved model is missing or incompatible with this machine's
    numpy/scikit-learn, start a one-off background retrain; KNOCK runs without the second opinion meanwhile."""
    global _CLASSIFIER
    if _CLASSIFIER is None and _STATUS['state'] not in ('training', 'failed'):
        _CLASSIFIER = FaultClassifier.load()
        if _CLASSIFIER is not None:
            _STATUS.update(state='ready', detail='')
        else:
            _start_training(FaultClassifier.last_error or 'model unavailable')
    return _CLASSIFIER


def classifier_status() -> dict:
    return dict(_STATUS)


def _start_training(reason: str) -> None:
    if not _TRAIN_LOCK.acquire(blocking=False):
        return
    _STATUS.update(state='training', detail='Saved model unusable here (%s). Retraining on this machine, about a minute.' % reason)

    def work():
        global _CLASSIFIER
        try:
            from knock.ai.classifier import train
            clf = train(seeds=range(1, 13), verbose=False)
            _CLASSIFIER = clf
            for layer in list(AILayer.instances):        # give running sessions the new model
                if layer.classifier is None:
                    layer.classifier = clf
            _STATUS.update(state='ready', detail='Retrained locally on 12 simulated worlds.')
        except Exception as e:                           # pragma: no cover
            _STATUS.update(state='failed', detail='Retraining failed: %s. Run `make train`.' % e)
        finally:
            _TRAIN_LOCK.release()

    threading.Thread(target=work, daemon=True, name='knock-train').start()


SHADOW_APPROVE_TICKS = 30


class Session:
    def __init__(self, scenario: str = 'crisis', seed: int = 42, region: str = 'la'):
        if scenario not in ('crisis', 'tuesday'):
            raise ValueError("Unknown scenario '%s'. Use 'crisis' or 'tuesday'." % scenario)
        from knock import regions as R
        R.get(region)
        self.region = region
        self.report_cache = {}
        self.id = uuid.uuid4().hex[:10]
        self.scenario, self.seed = scenario, int(seed)
        self.lock = threading.RLock()
        self.playing = False
        self.speed = 6                 # ticks per real second (6 = 60x)
        self.auto_approve_ticks = 0    # 0 = a person must approve; >0 = demo auto-approve after N ticks
        self.copilot = Copilot()
        self.twist_log: List[dict] = []
        self.created = time.time()
        self.last_seen = time.time()
        self.alerts: List[str] = []
        self._build()

    def _build(self):
        world = E.build_world(self.seed)
        scen = E.scenario_defs(world, self.region)[self.scenario]
        self.world, self.scen = world, scen
        self.runs: Dict[str, E.Run] = {
            'knock_ai': E.make_run(world, scen, 'knock'),
            'knock': E.make_run(world, scen, 'knock'),
            'baseline': E.make_run(world, scen, 'baseline'),
        }
        self.runs['knock_ai'].ai = AILayer(classifier=classifier())

    # ----- control -----
    def reset(self, scenario: Optional[str] = None, region: Optional[str] = None):
        with self.lock:
            if region:
                from knock import regions as R
                R.get(region)
                self.region = region
            if scenario:
                if scenario not in ('crisis', 'tuesday'):
                    raise ValueError("Unknown scenario '%s'." % scenario)
                self.scenario = scenario
            self.twist_log = []
            self.playing = False
            self._build()

    @property
    def A(self) -> E.Run:
        return self.runs['knock_ai']

    @property
    def done(self) -> bool:
        return self.A.done

    def step(self, n: int = 1):
        with self.lock:
            for _ in range(n):
                if self.done:
                    self.playing = False
                    return
                for name, r in self.runs.items():
                    E.step(r)
                # the hidden core run approves on a fixed delay so its numbers are comparable
                core = self.runs['knock']
                for a in list(core.feed[:60]):
                    if a['status'] == 'AWAITING' and core.tick - a['t'] > SHADOW_APPROVE_TICKS:
                        E.decide(core, a['id'], True)
                if self.auto_approve_ticks:
                    from .security import needs_two_people
                    for a in list(self.A.feed[:80]):
                        if a['status'] == 'AWAITING' and self.A.tick - a['t'] > self.auto_approve_ticks and not needs_two_people(a):
                            E.decide(self.A, a['id'], True)
                            a['auto'] = True
                            a['autoReason'] = 'demo auto-approve'

    def twist(self, key: str):
        with self.lock:
            if key not in E.TWISTS:
                raise ValueError("Unknown twist '%s'. Known: %s" % (key, ', '.join(E.TWISTS)))
            if self.done:
                raise ValueError('The scenario has finished. Restart it to inject twists.')
            if key != 'heat_spike' and key in self.A.firedTwists:
                raise ValueError("Twist '%s' is already active." % key)
            for r in self.runs.values():
                E.inject_twist(r, key)
            self.twist_log.append({'key': key, 'tick': self.A.tick})

    def decide(self, action_id: int, approve: bool) -> bool:
        with self.lock:
            return E.decide(self.A, action_id, approve)

    def set_policy(self, emergency_auto: Optional[bool] = None, auto_approve_ticks: Optional[int] = None):
        with self.lock:
            if emergency_auto is not None:
                for r in self.runs.values():
                    r.policy['emergencyAuto'] = bool(emergency_auto)
            if auto_approve_ticks is not None:
                self.auto_approve_ticks = max(0, int(auto_approve_ticks))


class Registry:
    def __init__(self, max_sessions: int = 40):
        self.sessions: Dict[str, Session] = {}
        self.max = max_sessions
        self._task: Optional[asyncio.Task] = None

    def create(self, scenario: str, seed: int, region: str = 'la') -> Session:
        if len(self.sessions) >= self.max:
            oldest = min(self.sessions.values(), key=lambda s: s.last_seen)
            self.sessions.pop(oldest.id, None)
        s = Session(scenario, seed, region)
        self.sessions[s.id] = s
        return s

    def get(self, sid: str) -> Session:
        s = self.sessions.get(sid)
        if s is None:
            raise KeyError(sid)
        s.last_seen = time.time()
        return s

    async def loop(self):
        """Advance every playing session at its own speed. Errors are contained per session."""
        acc: Dict[str, float] = {}
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.05)
            now = time.perf_counter()
            dt, last = now - last, now
            for s in list(self.sessions.values()):
                if not s.playing:
                    acc.pop(s.id, None)
                    continue
                acc[s.id] = acc.get(s.id, 0.0) + dt * s.speed
                n = int(acc[s.id])
                if n <= 0:
                    continue
                acc[s.id] -= n
                try:
                    await asyncio.to_thread(s.step, min(n, 30))
                except Exception as e:  # pragma: no cover - containment path
                    s.playing = False
                    s.alerts.append('Simulation paused after an internal error: %s' % e)
