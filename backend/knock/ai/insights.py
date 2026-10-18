"""Fleet insight engine: patterns no single-battery check can see. This is where KNOCK turns pre-emptive.

1. Cohort detection. When several batteries misbehave, ask what they have in common (firmware, street,
   battery model) and test whether that is a coincidence with a hypergeometric test. If a firmware version
   is strongly over-represented, propose rolling it back on the units that have not misbehaved yet.
   Requires human approval.
2. Overload prediction. Roll the learned load model forward with the weather forecast. If a street is
   heading over its limit while it still has headroom, top its batteries up now (charging only from spare
   capacity, so it can never cause the overload it prepares for). Low risk, so it runs automatically and
   is logged.
3. Outage pattern recognition. Many batteries going silent at once across streets is a network event,
   not dozens of hardware failures; say so, so nobody sends a fleet of trucks.
"""
from __future__ import annotations

from math import comb
from typing import Dict, List, Optional

from .. import engine as E


def hypergeom_tail(N: int, K: int, n: int, k: int) -> float:
    """P(X >= k) when drawing n of N items, K of which are 'marked'."""
    total = comb(N, n)
    if total == 0:
        return 1.0
    return sum(comb(K, i) * comb(N - K, n - i) for i in range(k, min(K, n) + 1)) / total


class InsightEngine:
    def __init__(self, load_model=None):
        self.load = load_model
        self.items: List[dict] = []          # insight cards for the UI
        self._cohorts_done = set()
        self._predict_last: Dict[int, int] = {}
        self._silent_window: List[tuple] = []
        self._outage_flagged_at = -10 ** 6

    def _add(self, run: E.Run, kind: str, severity: str, title: str, text: str, **extra) -> dict:
        item = {'id': len(self.items) + 1, 't': run.tick, 'clock': E.fmt_clock(run), 'kind': kind,
                'severity': severity, 'title': title, 'text': text}
        item.update(extra)
        self.items.insert(0, item)
        run.m.insights += 1
        return item

    # ---------- 1. cohort detection ----------
    def cohorts(self, run: E.Run) -> None:
        bad = [h for h in run.homes if h.k.top == 'ROGUE' and h.k.counted]
        if len(bad) < 2:
            return
        N = len(run.homes)
        attrs = {
            'firmware': lambda h: h.w.fw,
            'street': lambda h: E.street(run, h.w.xf),
            'battery model': lambda h: '%g kWh' % h.w.cap,
        }
        for name, fn in attrs.items():
            values = {}
            for h in bad:
                values.setdefault(fn(h), []).append(h)
            for val, members in values.items():
                k, n = len(members), len(bad)
                K = sum(1 for h in run.homes if fn(h) == val)
                if k < 2 or K >= N * 0.5:
                    continue
                p = hypergeom_tail(N, K, n, k)
                key = (name, val)
                if p > 0.01 or key in self._cohorts_done:
                    continue
                self._cohorts_done.add(key)
                if name == 'firmware':
                    at_risk = [h for h in run.homes if fn(h) == val and not h.fwRolledBack
                               and not (h.k.top == 'ROGUE' and h.k.counted) and not h.isolated]
                    odds = max(1, round(1 / max(p, 1e-12)))
                    text = ('%d of %d misbehaving batteries run firmware %s, which only %d of %d batteries (%d%%) run. '
                            'The chance of that by coincidence is about 1 in %s. %d more units run this firmware and '
                            'have not misbehaved yet. Recommend rolling them back to the previous version now, before '
                            'they do.' % (k, n, val, K, N, round(100 * K / N), '{:,}'.format(odds), len(at_risk)))
                    self._add(run, 'cohort', 'high', 'Bad firmware pattern: %s' % val, text, p=p, units=[h.w.idx for h in at_risk])
                    if at_risk:
                        E.add_action(run, None, 'HOLD', 'COHORT_ROLLBACK',
                                     'AI: roll back firmware %s on %d more units' % (val, len(at_risk)), text, True,
                                     units=[h.w.idx for h in at_risk], source='ai', xf=None)
                else:
                    self._add(run, 'cohort', 'medium', 'Pattern: misbehaving units cluster on %s %s' % (name, val),
                              '%d of %d misbehaving units share %s %s (%d of %d fleet-wide, p = %.3g). Worth a look by '
                              'field operations.' % (k, n, name, val, K, N, p), p=p)

    # ---------- 2. overload prediction ----------
    def predict_overloads(self, run: E.Run) -> None:
        if self.load is None or run.tick % (2 * E.TPM) != 0:
            return
        hits = []
        for X in run.xfs:
            if X.deenergized or run.tick < X.fuseUntil or X.stage > 0:
                continue
            if run.tick - self._predict_last.get(X.i, -10 ** 6) < 60 * E.TPM:
                continue
            peak = self.load.predict_xf_peak(run, X.i)
            if peak is None:
                continue
            X.forecastPeak = peak['kw'] / E.XF_LIMIT
            if peak['kw'] > E.XF_LIMIT * 1.0 and X.loading < 0.82:
                self._predict_last[X.i] = run.tick
                X.prechargeUntil = run.tick + 25 * E.TPM
                hits.append((X, peak))
        if not hits:
            return
        hits.sort(key=lambda o: -o[1]['kw'])
        when = E.fmt_clock(run, min(p['tick'] for _, p in hits))[:5]
        worst_x, worst = hits[0]
        names = ', '.join(X.id for X, _ in hits)
        text = ('The learned load model, rolled forward with the weather forecast, says %d street%s (%s) will try to draw '
                'more than their transformers are rated for from around %s, before batteries help. Worst is %s at about %d%%. '
                'They have spare capacity now, so KNOCK is topping up their batteries for the next 25 minutes. Charging is '
                'capped at 85%% load, so it cannot cause the overload it is preparing for.' % (
                    len(hits), '' if len(hits) == 1 else 's', names, when, worst_x.id, round(100 * worst['kw'] / E.XF_LIMIT)))
        title = 'Overload predicted on %d street%s around %s' % (len(hits), '' if len(hits) == 1 else 's', when)
        self._add(run, 'forecast', 'medium', title, text, xfs=[X.i for X, _ in hits])
        E.add_action(run, None, 'WATCH', 'PREDICT', 'AI: %s; pre-charging batteries now' % title, text, False,
                     source='ai', xfs=[X.i for X, _ in hits])

    # ---------- 3. outage pattern ----------
    def outage_pattern(self, run: E.Run) -> None:
        """Count batteries that went silent in the last two minutes and stayed silent (ignores one-tick dropouts)."""
        t = run.tick
        recent = [h for h in run.homes
                  if not h.tele and h.state != 'BROKEN' and 3 <= t - h.lastSeen <= 2 * E.TPM + 3]
        streets = {h.w.xf for h in recent}
        if len(recent) >= 12 and len(streets) >= 4 and t - self._outage_flagged_at > 30 * E.TPM:
            self._outage_flagged_at = t
            n = len(recent)
            text = ('%d batteries on %d streets went silent within two minutes. Hardware does not fail like that; '
                    'networks do. Treat this as a communications outage: knock before assuming anything, and do not '
                    'send technicians to silent units until a knock fails.' % (n, len(streets)))
            self._add(run, 'pattern', 'high', 'Network outage pattern, not %d hardware failures' % n, text)
            E.add_action(run, None, 'WATCH', 'INSIGHT', 'AI: %d batteries went quiet at once; this is a network outage' % n,
                         text, False, source='ai')

    def on_tick(self, run: E.Run) -> None:
        self.outage_pattern(run)
        if run.tick % E.TPM == 0:
            self.cohorts(run)
        self.predict_overloads(run)
