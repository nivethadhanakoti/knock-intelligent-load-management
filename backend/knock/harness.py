"""Headless runner used by tests, the demo script and model training."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from . import engine as E


def run_pair(scenario: str = 'crisis', seed: int = 42, twists: Sequence[Tuple[int, str]] = (),
             auto_approve_after: Optional[int] = 30, ai_factory=None, modes: Sequence[str] = ('knock', 'baseline'),
             region: str = 'la') -> Dict[str, E.Run]:
    """Run several controllers on the same world and seed. Returns {mode_label: Run}.

    ``modes`` may include 'knock', 'knock_ai' (KNOCK with the AI layer) and 'baseline'.
    ``auto_approve_after``: ticks after which pending approvals are granted (None = never).
    """
    world = E.build_world(seed)
    scen = E.scenario_defs(world, region)[scenario]
    runs: Dict[str, E.Run] = {}
    for m in modes:
        r = E.make_run(world, scen, 'baseline' if m == 'baseline' else 'knock')
        if m == 'knock_ai':
            if ai_factory is None:
                from .ai.layer import AILayer
                r.ai = AILayer.default()
            else:
                r.ai = ai_factory()
        runs[m] = r
    total = scen['duration'] * E.TPM
    for i in range(total):
        for minute, key in twists:
            if i == minute * E.TPM:
                for r in runs.values():
                    E.inject_twist(r, key)
        for r in runs.values():
            E.step(r)
            if auto_approve_after is not None and r.mode == 'knock':
                for a in list(r.feed):
                    if a['status'] == 'AWAITING' and i - a['t'] > auto_approve_after:
                        E.decide(r, a['id'], True)
    return runs


def summary(run: E.Run) -> dict:
    m = run.m
    return {
        'darkPeak': m.darkPeak, 'crit': len([x for x in m.critAffected if x != 'FAC']), 'custMin': round(m.custMin),
        'unservedKwh': round(m.unservedKwh, 1), 'recovered': m.recovered, 'probes': m.probes, 'fuses': m.fuseTrips,
        'trucks': m.truckRolls, 'dr': round(m.drRevenue), 'maint': m.maintCatches, 'rogueS': m.rogueDetectS,
        'rogues': m.roguesActivated, 'slowMin': round(m.slowHomeMin), 'islands': m.islandRequests, 'prevented': m.roguesPrevented, 'reviews': m.reviews, 'insights': m.insights,
        'spoiled': m.spoiled,
    }


def table(runs: Dict[str, E.Run]) -> str:
    keys = ['darkPeak', 'crit', 'custMin', 'unservedKwh', 'fuses', 'slowMin', 'islands', 'recovered', 'trucks', 'dr', 'maint', 'rogues',
            'prevented', 'rogueS', 'reviews', 'insights', 'probes']
    rows = {k: summary(v) for k, v in runs.items()}
    out: List[str] = ['%-12s' % 'metric' + ''.join('%14s' % k for k in rows)]
    for key in keys:
        out.append('%-12s' % key + ''.join('%14s' % rows[k][key] for k in rows))
    return '\n'.join(out)
