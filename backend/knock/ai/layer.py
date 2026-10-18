"""AILayer: plugs the learned components into the KNOCK engine through ``run.ai``.

Hooks the engine calls:
  forecast(run, home)        -> learned load forecast (or None to fall back to the physics prior)
  second_opinion(run, home)  -> classifier verdict after each knock (engine raises REVIEW on disagreement)
  on_tick(run)               -> learn from this tick, then fleet insights and predictions
  degrade(component, error)  -> fault containment: a failing component is switched off, never the core
"""
from __future__ import annotations

import weakref
from typing import Optional

from .. import engine as E
from .classifier import FaultClassifier, extract
from .forecaster import LoadModel
from .insights import InsightEngine


class AILayer:
    instances: 'weakref.WeakSet[AILayer]' = weakref.WeakSet()

    def __init__(self, classifier: Optional[FaultClassifier] = None, use_forecaster: bool = True, use_insights: bool = True):
        self.classifier = classifier
        AILayer.instances.add(self)
        self.load = LoadModel() if use_forecaster else None
        self.insights = InsightEngine(self.load) if use_insights else None
        self.disabled = set()
        self.errors = {}
        self.alerts = []
        self.opinions = {'agree': 0, 'disagree': 0}

    @classmethod
    def default(cls) -> 'AILayer':
        return cls(classifier=FaultClassifier.load())

    # ----- engine hooks -----
    def forecast(self, run: E.Run, h: E.Home):
        if self.load is None or 'forecast' in self.disabled:
            return None
        return self.load.predict_home(run, h)

    def second_opinion(self, run: E.Run, h: E.Home):
        if self.classifier is None or 'classifier' in self.disabled:
            return None
        k = h.k
        if k.probe is None or run.tick - k.probe['ended'] > 30 * E.TPM:
            return None
        verdict = self.classifier.predict(extract(run, h))
        agree = verdict['top'] == k.top
        if run.tick - k.probe['ended'] <= 2:
            self.opinions['agree' if agree else 'disagree'] += 1
        verdict['agree'] = agree
        return verdict

    def on_tick(self, run: E.Run) -> None:
        if self.load is not None and 'forecast' not in self.disabled:
            try:
                self.load.update(run)
            except Exception as e:
                self.degrade('forecast', e)
        if self.insights is not None and 'insights' not in self.disabled:
            self.insights.on_tick(run)

    def degrade(self, component: str, error: Exception) -> None:
        n = self.errors.get(component, 0) + 1
        self.errors[component] = n
        if n >= 3 and component not in self.disabled:
            self.disabled.add(component)
            self.alerts.append({'component': component, 'error': str(error)[:200],
                                'text': 'AI component "%s" switched off after repeated errors. Core KNOCK keeps running.' % component})

    # ----- status for the UI -----
    def status(self) -> dict:
        meta = self.classifier.meta if self.classifier else {}
        return {
            'forecaster': self.load.quality() if self.load else None,
            'classifier': {'loaded': self.classifier is not None,
                           'heldOutAccuracy': meta.get('heldOutAccuracy'),
                           'trainSamples': meta.get('trainSamples'),
                           'importances': meta.get('importances'),
                           'opinions': self.opinions},
            'insights': self.insights.items[:30] if self.insights else [],
            'disabled': sorted(self.disabled),
            'alerts': self.alerts[-5:],
        }
