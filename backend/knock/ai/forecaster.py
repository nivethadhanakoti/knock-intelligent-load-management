"""Learned load model: one online regression per home, trained from that home's own smart meter.

Why: the consistency check ("does the battery's story match the meter?") needs to know what each home
*would* draw. A real operator does not know each home's thermal sensitivity, so KNOCK learns it.

Model (per home):   load = theta0 + theta1 * s + theta2 * s * (heat - 1)
where s is the time-of-day shape and heat is the weather factor (from the forecast, known in advance).
Training: recursive least squares with a forgetting factor, vectorized across all homes with numpy.
Robustness: samples that disagree by more than OUTLIER_KW are skipped, so an appliance step or a
lying battery cannot teach the model a wrong baseline.

The same model, rolled forward with the weather forecast, predicts each transformer's load 5 to 45
minutes ahead. That powers the pre-emptive "top up batteries before the peak" insight.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .. import engine as E

LAMBDA = 0.9995        # forgetting factor (adapts over hours, not minutes)
OUTLIER_KW = 1.5       # skip samples this far off once warmed up
WARMUP = 20            # samples before the model is trusted
LOCKOUT = 12           # consecutive rejected samples (2 min) = a real level shift, so relearn
PRIOR_THETA = np.array([0.0, 2.6, 2.8])          # fleet-average home: base 2.6 kW, heat sensitivity about 1.05
PRIOR_P = np.diag([0.5, 1.0, 0.6])                # how far each coefficient may move from the prior
BOUNDS_LO = np.array([-1.5, 0.8, 0.0])            # physically plausible range for each coefficient
BOUNDS_HI = np.array([1.5, 6.0, 8.0])


class LoadModel:
    def __init__(self, n_homes: int = E.N_XF * E.PER_XF):
        self.n = n_homes
        self.theta = np.tile(PRIOR_THETA, (n_homes, 1))
        self.P = np.tile(PRIOR_P, (n_homes, 1, 1))
        self.count = np.zeros(n_homes, dtype=int)
        self.rejects = np.zeros(n_homes, dtype=int)
        self.abs_err = np.full(n_homes, np.nan)     # running mean absolute error, for the UI

    @staticmethod
    def features(run: E.Run, tick: int, heat: float) -> np.ndarray:
        s = E.shape(E.clock_min(run, tick) / 60)
        return np.array([1.0, s, s * (heat - 1.0)])

    def predict_home(self, run: E.Run, h: E.Home, tick: Optional[int] = None, heat: Optional[float] = None) -> Optional[float]:
        i = h.w.idx
        if self.count[i] < WARMUP:
            return None
        t = run.tick if tick is None else tick
        if heat is None:
            heat = E.heat_at(run.scen, t / E.TPM) + run.heatBoost
        y = float(self.features(run, t, heat) @ self.theta[i])
        if h.slowed:
            y *= (1 - h.w.flexFrac)
        return y

    def update(self, run: E.Run) -> None:
        """One vectorized RLS step using this tick's trusted observations."""
        heat = E.heat_now(run)
        phi = self.features(run, run.tick, heat)
        idx, ys = [], []
        for h in run.homes:
            if not h.tele or h.islanded or h.k.flagged or h.k.probing:
                continue
            y = h.meter + h.tele['p']           # home load = what it drew from the grid + what the battery supplied
            if h.slowed:
                y /= max(0.2, 1 - h.w.flexFrac)
            idx.append(h.w.idx)
            ys.append(y)
        if not idx:
            return
        idx = np.array(idx)
        y = np.array(ys)
        th = self.theta[idx]
        pred = th @ phi
        err = y - pred
        warm = self.count[idx] >= WARMUP
        ok = np.where(warm, np.abs(err) < OUTLIER_KW, np.abs(err) < 2.5)
        # lockout guard: if a home has been rejected for 2 minutes straight, its load really changed; relearn
        self.rejects[idx] = np.where(ok, 0, self.rejects[idx] + 1)
        locked = self.rejects[idx] >= LOCKOUT
        if locked.any():
            li = idx[locked]
            self.P[li] = PRIOR_P * 4.0
            self.count[li] = 0
            self.rejects[li] = 0
            ok = ok | locked
        if not ok.any():
            return
        idx, err = idx[ok], err[ok]
        P = self.P[idx]                                   # (m,3,3)
        Pphi = P @ phi                                     # (m,3)
        denom = LAMBDA + Pphi @ phi                        # (m,)
        K = Pphi / denom[:, None]                          # (m,3)
        self.theta[idx] = np.clip(self.theta[idx] + K * err[:, None], BOUNDS_LO, BOUNDS_HI)
        self.P[idx] = (P - np.einsum('mi,mj->mij', K, Pphi)) / LAMBDA
        self.count[idx] += 1
        prev = self.abs_err[idx]
        self.abs_err[idx] = np.where(np.isnan(prev), np.abs(err), 0.98 * prev + 0.02 * np.abs(err))

    def relearn(self, i: int) -> None:
        """Called when a knock proves the battery honest but the meter still disagrees: the load model is wrong."""
        self.P[i] = PRIOR_P * 4.0
        self.count[i] = 0
        self.rejects[i] = 0

    def predict_xf_peak(self, run: E.Run, x: int, horizon_min: int = 45, step_min: int = 5) -> Optional[dict]:
        """Gross transformer load (before batteries) over the next ``horizon_min`` minutes."""
        units = run.homes[x * E.PER_XF:(x + 1) * E.PER_XF]
        if any(self.count[h.w.idx] < WARMUP for h in units):
            return None
        best = None
        for dm in range(step_min, horizon_min + 1, step_min):
            t = run.tick + dm * E.TPM
            heat = E.heat_at(run.scen, t / E.TPM) + run.heatBoost
            phi = self.features(run, t, heat)
            total = 0.0
            for h in units:
                y = float(phi @ self.theta[h.w.idx])
                total += y * ((1 - h.w.flexFrac) if h.slowed else 1)
            if run.facility and run.facility['xf'] == x:
                total += run.facility['load']
            if best is None or total > best['kw']:
                best = {'kw': total, 'in_min': dm, 'tick': t}
        return best

    def quality(self) -> dict:
        warm = int((self.count >= WARMUP).sum())
        mae = float(np.nanmean(self.abs_err)) if warm else None
        return {'homesLearned': warm, 'homes': self.n, 'meanAbsErrorKw': mae}
