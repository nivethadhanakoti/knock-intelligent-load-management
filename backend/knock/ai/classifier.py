"""Learned fault classifier: a second opinion on every diagnosis.

KNOCK's primary diagnosis is a hand-built Bayesian model (transparent, works on day one). This module adds
a gradient-boosted classifier trained on labelled fault episodes from the simulator. In production the same
pipeline would train on fleet history (technician reports and confirmed outcomes are the labels).

Safety rule (enforced in the engine): the model can only add caution. If it confidently disagrees with the
Bayesian diagnosis, KNOCK does not act on its own; it raises a REVIEW card for a person.

Features use only what KNOCK can observe, never hidden truth:
  telemetry present, seconds silent, knock correlation r and SNR, whether a knock exists, recent voltage
  sag, last reported inverter temperature, temperature-trend flag, consistency residual, CUSUM score,
  and the meter's offset from forecast given the command (does the battery's command show up at the meter?).
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
from typing import Dict, List, Optional

import numpy as np

from .. import engine as E
from .. import keys

FEATURES = ['tele', 'silent_s', 'has_probe', 'probe_r', 'probe_snr', 'sag', 'last_temp', 'trend',
            'resid_abs', 'cusum', 'meter_offset']
def _versions() -> dict:
    import sklearn
    return {'numpy': np.__version__, 'sklearn': sklearn.__version__}


DEFAULT_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'models', 'fault_classifier.pkl')


def extract(run: E.Run, h: E.Home) -> List[float]:
    k, t, X = h.k, run.tick, run.xfs[h.w.xf]
    probe = k.probe if (k.probe and t - k.probe['ended'] < 30 * E.TPM) else None
    lm = getattr(run.ai, 'load', None) if run.ai is not None else None
    fc = (lm.predict_home(run, h) if lm is not None else None) or E.physics_forecast(run, h)
    meter_offset = h.meter - (fc - h.cmd)          # ~0 if the battery is doing what it was told
    return [
        1.0 if h.tele else 0.0,
        float(min(3600, (t - h.lastSeen) * E.DT_S)),
        1.0 if probe else 0.0,
        float(probe['r']) if probe else 0.0,
        float(probe.get('snr') or 0.0) if probe else 0.0,
        1.0 if t < X.sagUntil else 0.0,
        float(h.lastTele['temp']) if h.lastTele else 31.0,
        1.0 if k.trendFlag else 0.0,
        float(abs(k.resid or 0.0)) if h.tele else 0.0,
        float(k.cusum),
        float(meter_offset),
    ]


class FaultClassifier:
    def __init__(self, model, meta: Optional[dict] = None):
        self.model = model
        self.meta = meta or {}
        self.classes = list(model.classes_)

    last_error: Optional[str] = None

    @classmethod
    def load(cls, path: str = DEFAULT_PATH) -> Optional['FaultClassifier']:
        """Load the trained model safely. Returns None (never raises) when the model is missing, unsigned,
        tampered with, or was trained with different numpy/scikit-learn versions; callers then retrain.

        Security: Python pickles can execute code when loaded, so the file is verified BEFORE it is
        unpickled. The sidecar .sig holds the file's SHA-256 and library versions, signed with this
        installation's secret key. A model copied from another machine, or swapped by an attacker, is refused.
        """
        path = os.path.abspath(path)
        sig_path = path + '.sig'
        if not os.path.exists(path):
            cls.last_error = 'model file not found'
            return None
        if not os.path.exists(sig_path):
            cls.last_error = 'model file is unsigned (not trained on this installation)'
            return None
        try:
            with open(path, 'rb') as f:
                raw = f.read()
            with open(sig_path) as f:
                sig = json.load(f)
            digest = hashlib.sha256(raw).hexdigest()
            payload = json.dumps({'sha256': digest, 'versions': sig.get('versions')}, sort_keys=True).encode()
            if sig.get('sha256') != digest or not keys.verify(payload, 'model', sig.get('hmac', '')):
                cls.last_error = 'model signature check failed (file changed or signed by another installation)'
                return None
            if sig.get('versions') != _versions():
                cls.last_error = 'model trained with %s, this machine has %s' % (sig.get('versions'), _versions())
                return None
            blob = pickle.loads(raw)
            clf = cls(blob['model'], blob.get('meta'))
            clf.predict([0.0] * len(FEATURES))          # smoke test
            cls.last_error = None
            return clf
        except Exception as e:                           # corrupted file, ...
            cls.last_error = '%s: %s' % (type(e).__name__, str(e)[:160])
            return None

    def save(self, path: str = DEFAULT_PATH) -> None:
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        raw = pickle.dumps({'model': self.model, 'meta': self.meta})
        with open(path, 'wb') as f:
            f.write(raw)
        digest = hashlib.sha256(raw).hexdigest()
        versions = _versions()
        payload = json.dumps({'sha256': digest, 'versions': versions}, sort_keys=True).encode()
        with open(path + '.sig', 'w') as f:
            json.dump({'sha256': digest, 'versions': versions, 'hmac': keys.sign(payload, 'model')}, f, indent=1)

    def predict(self, feats: List[float]) -> Dict:
        p = self.model.predict_proba(np.array([feats]))[0]
        probs = {c: float(v) for c, v in zip(self.classes, p)}
        top = max(probs, key=probs.get)
        return {'top': top, 'conf': probs[top], 'probs': probs}


# ---------- training data generation ----------
class _Recorder:
    """Training-only AI hook: records (features, true label) once per knock, right after it is decoded."""

    def __init__(self):
        self.X: List[List[float]] = []
        self.y: List[str] = []
        self.group: List[int] = []
        self.bayes: List[str] = []
        self.seen = set()
        self.seed = 0
        self.load = None

    def forecast(self, run, h):
        return None

    def second_opinion(self, run, h):
        k = h.k
        if k.probe is None:
            return None
        key = (self.seed, run.scen['key'], h.w.idx, k.probe['ended'])
        if key in self.seen:
            return None
        self.seen.add(key)
        label = h.state if h.state in E.HYP else 'HEALTHY'
        self.X.append(extract(run, h))
        self.y.append(label)
        self.group.append(self.seed)
        self.bayes.append(k.top)
        return None

    def on_tick(self, run):
        pass

    def degrade(self, comp, e):
        raise e


def _random_faults(run: E.Run, rnd) -> None:
    """Every 3 minutes, break a couple of random healthy batteries in a random way (training data only)."""
    if run.tick % (3 * E.TPM) != 0 or run.tick < 5 * E.TPM:
        return
    healthy = [h for h in run.homes if h.state == 'HEALTHY' and not h.truck and not h.islanded]
    for h in rnd.sample(healthy, min(2, len(healthy))):
        u = rnd.random()
        if u < 0.35:
            h.state = 'MUTE'
        elif u < 0.55:
            h.state = 'TRIPPED'
            if rnd.random() < 0.6:
                run.xfs[h.w.xf].sagUntil = run.tick + 30 * E.TPM
        elif u < 0.75:
            if rnd.random() < 0.5:
                h.state = 'BROKEN'                      # sudden failure, no warning
            else:
                h.degradeFrom, h.failAt = run.tick, run.tick + rnd.randint(8, 25) * E.TPM
        else:
            h.state = 'ROGUE'
    # occasional silent repairs keep the fleet from emptying out
    for h in run.homes:
        if h.state in ('MUTE', 'TRIPPED') and rnd.random() < 0.002:
            h.state = 'HEALTHY'


def generate_dataset(seeds=range(1, 31), verbose: bool = False):
    import random
    rec = _Recorder()
    twist_pool = ['comms_outage', 'cyber_spoof', 'psps', 'facility', 'heat_spike']
    for seed in seeds:
        rnd = random.Random(seed)
        for scen in ('crisis', 'tuesday'):
            world = E.build_world(seed)
            sc = E.scenario_defs(world)[scen]
            run = E.make_run(world, sc, 'knock')
            rec.seed = seed
            run.ai = rec
            twists = [(rnd.randint(15, 150), k) for k in rnd.sample(twist_pool, rnd.randint(0, 1))]
            total = sc['duration'] * E.TPM
            for i in range(total):
                for m, key in twists:
                    if i == m * E.TPM:
                        E.inject_twist(run, key)
                _random_faults(run, rnd)
                E.step(run)
                for a in list(run.feed):
                    if a['status'] == 'AWAITING' and i - a['t'] > 30:
                        E.decide(run, a['id'], True)
        if verbose:
            from collections import Counter
            print('seed', seed, 'samples', len(rec.y), dict(Counter(rec.y)))
    return np.array(rec.X), np.array(rec.y), np.array(rec.group), np.array(rec.bayes)


def train(seeds=range(1, 31), out_path: str = DEFAULT_PATH, verbose: bool = True) -> FaultClassifier:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.metrics import accuracy_score, confusion_matrix

    X, y, g, bayes = generate_dataset(seeds, verbose=verbose)
    test_mask = g % 5 == 0                       # hold out every fifth seed (whole worlds, no leakage)
    model = GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.1, random_state=0)
    # balance classes so rare faults (broken, rogue) count as much as common ones (mute)
    from collections import Counter
    cnt = Counter(y[~test_mask])
    weights = np.array([len(y[~test_mask]) / (len(cnt) * cnt[v]) for v in y[~test_mask]])
    model.fit(X[~test_mask], y[~test_mask], sample_weight=weights)
    pred = model.predict(X[test_mask])
    labels = [str(v) for v in sorted(set(y))]
    acc = float(accuracy_score(y[test_mask], pred))
    cm = confusion_matrix(y[test_mask], pred, labels=labels).tolist()
    from sklearn.metrics import balanced_accuracy_score
    bacc = float(balanced_accuracy_score(y[test_mask], pred))
    yt, bt = y[test_mask], bayes[test_mask]
    agree = pred == bt
    comparison = {
        'bayesAccuracy': float((bt == yt).mean()),
        'modelAccuracy': acc,
        'agreementRate': float(agree.mean()),
        'accuracyWhenAgree': float((pred[agree] == yt[agree]).mean()) if agree.any() else None,
        'bayesAccuracyWhenDisagree': float((bt[~agree] == yt[~agree]).mean()) if (~agree).any() else None,
    }
    meta_cmp = comparison
    meta = {'comparison': meta_cmp, 'versions': _versions(), 'features': FEATURES, 'labels': labels, 'heldOutAccuracy': acc, 'balancedAccuracy': bacc, 'confusion': cm,
            'trainSamples': int((~test_mask).sum()), 'testSamples': int(test_mask.sum()),
            'importances': dict(zip(FEATURES, [float(v) for v in model.feature_importances_]))}
    clf = FaultClassifier(model, meta)
    clf.save(out_path)
    if verbose:
        print('held-out accuracy %.3f, balanced accuracy %.3f on %d samples' % (acc, bacc, meta['testSamples']))
        print('comparison', comparison)
        print('labels', labels)
        for lab, row in zip(labels, cm):
            print('  %-8s %s' % (lab, row))
    return clf
