"""KNOCK engine: deterministic home-battery fleet simulator plus two controllers.

* KNOCK controller: watch (consistency check), knock (coded active probing), diagnose (Bayes),
  climb the ladder (dispatch -> slow -> island -> hold -> hand off).
* Baseline controller: the blunt operator (silent = dead, overload = cut the street).

Ported from the tested JavaScript reference engine. Three additions for the AI layer:
  1. ``run.ai`` hook object (forecast, second opinion on diagnoses, fleet-level insights).
  2. A firmware cohort: a handful of homes run firmware 4.2.1, which goes rogue in waves.
  3. Pre-charge support so an AI overload prediction can prepare batteries ahead of a peak.

The controllers never read hidden truth (``Home.state``); they see telemetry, meters,
transformer readings and their own commands.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .rng import Mulberry32, gauss, pick

# ---------- constants (tuned in the reference; change only with the acceptance tests) ----------
DT_S = 10
DT_H = DT_S / 3600
TPM = 6                     # ticks per simulated minute
N_XF, PER_XF = 9, 16
XF_LIMIT = 75.0             # kW thermal limit per transformer
PROBE_A = 0.5               # kW probe amplitude (safety cap)
PROBE_L = 16                # Walsh code length (chips = ticks)
MAX_PROBE_PER_XF = 3        # about 20% of a transformer's units at once
DISPATCH_CAP = 3.0          # kW per battery for fleet dispatch (keeps homeowner backup reserve)
SOC_FLOOR = 0.2
HYP = ['HEALTHY', 'MUTE', 'TRIPPED', 'BROKEN', 'ROGUE']
PRIOR = [0.90, 0.04, 0.03, 0.02, 0.01]
CLASS_LABEL = {'LIFE': 'Life support', 'MED': 'Medical (time-bound)', 'TW': 'Time window (EV)', 'FLEX': 'Flexible'}
STREETS = ['Alder Avenue', 'Birch Street', 'Cedar Lane', 'Oak Street', 'Elm Street', 'Fig Avenue',
           'Juniper Way', 'Maple Drive', 'Palm Court']
BAD_FW = '4.2.1'

TWISTS = {
    'comms_outage': {'label': 'Mass comms outage', 'desc': '60% of batteries stop reporting at once (cell network down).'},
    'cyber_spoof': {'label': 'Cyber spoof', 'desc': '5 batteries report fake discharge while secretly charging.'},
    'psps': {'label': 'PSPS shutoff on T8-T9', 'desc': 'Utility announces a 2-hour shutoff starting in 20 minutes.'},
    'facility': {'label': 'Cold-storage facility', 'desc': 'A 45 kW harbor reefer yard joins T2. Spoiled cargo if it loses power.'},
    'heat_spike': {'label': 'Heat spike', 'desc': "Temperature jumps; every home's AC load rises 15%."},
}


def clamp(x, a, b):
    return max(a, min(b, x))


def _popcount(x: int) -> int:
    return bin(x).count('1')


# Walsh-Hadamard codes: H[i][j] = (-1)^popcount(i & j). Rows 1..15 are zero-mean and mutually orthogonal.
WALSH = [[-1 if _popcount(i & j) % 2 else 1 for j in range(PROBE_L)] for i in range(PROBE_L)]


# ---------- world ----------
class WorldHome:
    """Static description of a home (shared by every run built from the same world)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class World:
    def __init__(self, seed: int, homes: List[WorldHome]):
        self.seed = seed
        self.homes = homes


def build_world(seed: int) -> World:
    r = Mulberry32(seed)
    homes: List[WorldHome] = []
    heat_sens = [1.0, 1.05, 0.95, 1.35, 1.0, 1.05, 1.3, 0.95, 1.0]  # T4 and T7 are hot inland pockets
    for x in range(N_XF):
        for k in range(PER_XF):
            n = x * PER_XF + k
            u = r()
            cls = 'LIFE' if u < 0.045 else 'MED' if u < 0.125 else 'TW' if u < 0.28 else 'FLEX'
            cap_idx = int(math.floor(r() * 3))
            homes.append(WorldHome(
                idx=n, id='H%03d' % (n + 1), xf=x, cls=cls,
                base=2.1 + r() * 1.0,
                heatSens=heat_sens[x] * (0.85 + r() * 0.3),
                crit=0.6 if cls == 'LIFE' else 0.3 if cls == 'MED' else 0.2,
                flexFrac=0.35 if cls == 'TW' else 0.18 if cls == 'FLEX' else 0.1 if cls == 'MED' else 0.0,
                cap=[13.5, 20.0, 30.0][cap_idx], pmax=[5.0, 7.6, 11.5][cap_idx],
                soc0=0.55 + r() * 0.35, fw='4.1.8', resetOK=r() < 0.8,
                deadlineH=15 if cls == 'TW' else None,
            ))
    t4 = [h for h in homes if h.xf == 3]
    if not any(h.cls == 'LIFE' for h in t4):
        t4[2].cls, t4[2].crit, t4[2].flexFrac = 'LIFE', 0.6, 0.0
    # Firmware cohort (drawn from a separate stream so the reference world above is unchanged):
    # the three T7 units that go rogue first, plus nine more spread across other streets.
    t7 = [h for h in homes if h.xf == 6]
    cohort = [t7[1].idx, t7[6].idx, t7[12].idx]
    rc = Mulberry32(seed * 11 + 3)
    pool = [h.idx for h in homes if h.xf not in (3, 6) and h.cls not in ('LIFE',)]
    while len(cohort) < 12:
        p = pick(rc, pool)
        if p not in cohort:
            cohort.append(p)
    for i in cohort:
        homes[i].fw = BAD_FW
    return World(seed, homes)


def scenario_defs(world: World, region: str = 'la') -> Dict[str, dict]:
    from . import regions as R
    reg = R.get(region)
    r = Mulberry32(world.seed * 7 + 1)
    H = world.homes
    on_xf = lambda x: [h for h in H if h.xf == x]
    t4, t7, t2 = on_xf(3), on_xf(6), on_xf(1)
    life_t4 = next(h for h in t4 if h.cls == 'LIFE')
    t4o = [h for h in t4 if h is not life_t4]
    trip = [h.idx for h in (life_t4, t4o[0], t4o[4], t4o[9])]
    mute = [h.idx for h in (t4o[2], t4o[6], t4o[11], on_xf(0)[5], on_xf(5)[8], on_xf(8)[3])]
    broken = t2[7].idx
    rogue1 = [t7[1].idx, t7[6].idx, t7[12].idx]
    others = [h.idx for h in H if h.fw == BAD_FW and h.idx not in rogue1]
    rogue2, rogue3 = others[:4], others[4:]
    tues_mute: List[int] = []
    pool = [h.idx for h in H if h.xf != 1]
    while len(tues_mute) < 14:
        p = pick(r, pool)
        if p not in tues_mute:
            tues_mute.append(p)
    W = {'trip': trip, 'mute': mute, 'broken': broken, 'rogue1': rogue1, 'rogue2': rogue2, 'rogue3': rogue3}
    common = {'region': reg['key'], 'regionName': reg['name'], 'streets': reg['streets'], 'protocol': reg['protocol'],
              'bluntName': reg['bluntName'], 'bluntCutMin': reg['bluntCutMin'], 'duration': 180}
    if region == 'la':
        crisis = {
            'key': 'crisis', 'name': 'Santa Ana heat event', 'start': 15 * 60,
            'heat': [[0, 1.0], [60, 1.6], [150, 1.6], [180, 1.35]],
            'events': [
                {'at': 20, 'type': 'trip', 'xf': 3, 'units': trip, 'label': 'Voltage sag at T4 trips 4 inverters'},
                {'at': 28, 'type': 'mute', 'units': mute, 'label': 'Cellular gateway fault: 6 batteries stop reporting'},
                {'at': 40, 'type': 'degrade', 'units': [broken], 'failAt': 65, 'label': 'Inverter on %s starts overheating' % H[broken].id},
                {'at': 70, 'type': 'rogue', 'units': rogue1, 'label': 'Firmware 4.2.1 starts unscheduled charging on 3 units'},
                {'at': 95, 'type': 'rogue', 'units': rogue2, 'label': 'Firmware 4.2.1 activates on more units'},
                {'at': 105, 'type': 'heat', 'boost': 0.2, 'label': 'Second wave: inland temperatures pass 110 F, AC load surges'},
                {'at': 125, 'type': 'rogue', 'units': rogue3, 'label': 'Firmware 4.2.1 activates on the rest of the cohort'},
            ],
        }
    else:
        crisis = {'key': 'crisis', 'name': reg['crisisName'], 'start': reg['start'], 'heat': reg['heat'],
                  'events': sorted(reg['events'](W), key=lambda e: e['at'])}
    tues_start = 15 * 60
    tuesday = {
        'key': 'tuesday', 'name': 'Ordinary Tuesday', 'start': tues_start,
        'heat': [[0, 1.0], [180, 1.05]],
        'dr': {'from': 120, 'to': 160, 'priceKwh': 2.0},
        'events': [
            {'at': 10, 'type': 'mute', 'units': tues_mute, 'label': 'ISP router update: 14 batteries stop reporting'},
            {'at': 30, 'type': 'degrade', 'units': [broken], 'failAt': 150, 'label': 'Inverter on %s starts running hot' % H[broken].id},
            {'at': 110, 'type': 'note', 'label': 'Grid operator calls a demand-response event, 17:00-17:40'},
        ],
    }
    crisis.update(common)
    tuesday.update(common)
    return {'crisis': crisis, 'tuesday': tuesday}


def street(run, x: int) -> str:
    return run.scen.get('streets', STREETS)[x]


# ---------- run state ----------
class Knowledge:
    """What KNOCK believes about one battery."""

    def __init__(self):
        self.post = PRIOR[:]
        self.top = 'HEALTHY'
        self.conf = 0.9
        self.evidence: List[str] = []
        self.probe: Optional[dict] = None
        self.probing = False
        self.lastProbe = -9999
        self.cusum = 0.0
        self.flagged = False
        self.rung = 'WATCH'
        self.counted = False
        self.resetTried = 0
        self.trendFlag = False
        self.actions: List[dict] = []
        self.probes = 0
        self.diagTick = -1
        self.resid = 0.0
        self.patienceMode = False
        self.retries = 0
        self.ml: Optional[dict] = None       # AI second opinion
        self.reviewed = False
        self.clearedProbe: Optional[int] = None


class Home:
    def __init__(self, w: WorldHome):
        self.w = w
        self.state = 'HEALTHY'
        self.soc = w.soc0
        self.temp = 31.0
        self.cmd = 0.0
        self.probeOff = 0.0
        self.p = 0.0
        self.load = 0.0
        self.net = 0.0
        self.meter = 0.0
        self.shed = self.slowed = self.isolated = self.islanded = self.dark = self.truck = False
        self.truckAt: Optional[int] = None
        self.degradeFrom: Optional[int] = None
        self.failAt: Optional[int] = None
        self.tele: Optional[dict] = None
        self.lastSeen = 0
        self.lastTele: Optional[dict] = None
        self.darkMin = 0.0
        self.everDark = False
        self.hist: List[dict] = []
        self.ar = 0.0
        self.spike = 0
        self.fwRolledBack = False
        self.k = Knowledge()


class Xf:
    def __init__(self, i: int):
        self.i = i
        self.id = 'T%d' % (i + 1)
        self.meas = 0.0
        self.loading = 0.0
        self.volt = 1.0
        self.overTicks = 0
        self.critOver = 0
        self.fuseUntil = -1
        self.shedUntil = -1
        self.sagUntil = -1
        self.deenergized = False
        self.pspsNotice = False
        self.probe: Optional[dict] = None
        self.shedReqTick = -10 ** 6
        self.hist: List[float] = []
        self.stage = 0
        self.stageTick = 0
        self.hotTicks = 0
        self.calm = 0
        self.prechargeUntil = -1
        self.forecastPeak: Optional[float] = None


class Metrics:
    def __init__(self):
        self.darkNow = 0
        self.darkPeak = 0
        self.offGridNow = 0
        self.critAffected: set = set()
        self.custMin = 0.0
        self.unservedKwh = 0.0
        self.recovered = 0
        self.probes = 0
        self.approvals = 0
        self.truckRolls = 0
        self.drKwh = 0.0
        self.drRevenue = 0.0
        self.maintCatches = 0
        self.fuseTrips = 0
        self.rogueDetectS: Optional[float] = None
        self.rogueStart: Optional[int] = None
        self.roguesActivated = 0
        self.roguesPrevented = 0
        self.spoiled = False
        self.reviews = 0
        self.insights = 0
        self.slowHomeMin = 0.0
        self.islandRequests = 0

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d['critAffected'] = len([x for x in self.critAffected if x != 'FAC'])
        d['facilityHit'] = 'FAC' in self.critAffected
        return d


class Run:
    def __init__(self, world: World, scen: dict, mode: str):
        self.mode = mode                     # 'knock' or 'baseline'
        self.world = world
        self.scen = scen
        self.tick = 0
        self.homes = [Home(w) for w in world.homes]
        self.xfs = [Xf(i) for i in range(N_XF)]
        self.rng = Mulberry32(world.seed * 13 + 5)
        self.heatBoost = 0.0
        self.facility: Optional[dict] = None
        self.psps: Optional[dict] = None    # the PSPS twist (also appended to cuts)
        self.cuts: List[dict] = []           # every scheduled or unannounced de-energization
        self.policy = {'emergencyAuto': True}
        self.pendingEvents = list(scen['events'])
        self.firedTwists: List[str] = []
        self.feed: List[dict] = []
        self.log: List[dict] = []
        self.nextAction = 1
        self.m = Metrics()
        self.ai: Any = None                  # optional AI layer (see knock.ai.layer.AILayer)

    @property
    def done(self) -> bool:
        return self.tick >= self.scen['duration'] * TPM


def make_run(world: World, scen: dict, mode: str) -> Run:
    return Run(world, scen, mode)


def heat_at(scen: dict, minute: float) -> float:
    pts = scen['heat']
    for i in range(1, len(pts)):
        if minute <= pts[i][0]:
            a, va = pts[i - 1]
            b, vb = pts[i]
            return va + (vb - va) * (minute - a) / (b - a)
    return pts[-1][1]


def shape(hod: float) -> float:
    return 0.95 + 0.4 * math.exp(-((hod - 17.5) ** 2) / (2 * 2.2 * 2.2))


def clock_min(run: Run, tick: Optional[int] = None) -> float:
    return run.scen['start'] + (run.tick if tick is None else tick) / TPM


def fmt_clock(run: Run, tick: Optional[int] = None) -> str:
    m = clock_min(run, tick)
    hh, mm, ss = int(m // 60) % 24, int(m % 60), int((m * 60) % 60)
    return '%02d:%02d:%02d' % (hh, mm, ss)


def heat_now(run: Run) -> float:
    return heat_at(run.scen, run.tick / TPM) + run.heatBoost


def physics_forecast(run: Run, h: Home, tick: Optional[int] = None, heat: Optional[float] = None) -> float:
    """Deterministic part of the load model (what a simple per-home forecast would give)."""
    t = run.tick if tick is None else tick
    if heat is None:
        heat = heat_at(run.scen, t / TPM) + run.heatBoost
    hod = clock_min(run, t) / 60
    L = h.w.base * shape(hod) * (1 + (heat - 1) * h.w.heatSens)
    if h.slowed:
        L *= (1 - h.w.flexFrac)
    return L


def forecast_load(run: Run, h: Home) -> float:
    """Load forecast used by the consistency check. The AI layer can replace it with a learned model."""
    if run.ai is not None:
        try:
            v = run.ai.forecast(run, h)
            if v is not None and math.isfinite(v):
                return v
        except Exception as e:  # AI must never break the core loop
            run.ai.degrade('forecast', e)
    return physics_forecast(run, h)


# ---------- events and twists ----------
def apply_event(run: Run, ev: dict) -> None:
    H = run.homes
    t = ev['type']
    if t == 'trip':
        run.xfs[ev['xf']].sagUntil = run.tick + 30 * TPM
        for i in ev['units']:
            H[i].state = 'TRIPPED'
    elif t == 'mute':
        for i in ev['units']:
            if H[i].state == 'HEALTHY':
                H[i].state = 'MUTE'
    elif t == 'degrade':
        for i in ev['units']:
            H[i].degradeFrom = run.tick
            H[i].failAt = ev['failAt'] * TPM
    elif t == 'rogue':
        n_on = 0
        for i in ev['units']:
            if H[i].fwRolledBack:
                run.m.roguesPrevented += 1
                continue
            if H[i].state in ('HEALTHY', 'MUTE'):
                H[i].state = 'ROGUE'
                n_on += 1
        run.m.roguesActivated += n_on
        if run.m.rogueStart is None and n_on:
            run.m.rogueStart = run.tick
        if n_on == 0:
            run.log.append({'t': run.tick, 'text': ev['label'] + ' (blocked: cohort already rolled back)', 'kind': 'event'})
            return
    elif t == 'heat':
        run.heatBoost += ev['boost']
    elif t == 'scheduled_cut':
        run.cuts.append({'xfs': list(ev['xfs']), 'from': ev['from'] * TPM, 'to': ev['to'] * TPM, 'kind': 'scheduled'})
    elif t == 'grid_damage':
        run.cuts.append({'xfs': list(ev['xfs']), 'from': run.tick, 'to': run.tick + ev['minutes'] * TPM, 'kind': 'damage'})
    elif t == 'mass_mute':
        rr = Mulberry32(run.world.seed * 17 + int(ev['frac'] * 1000))
        for h in H:
            if h.state == 'HEALTHY' and rr() < ev['frac']:
                h.state = 'MUTE'
    elif t == 'mass_trip':
        for x in ev['xfs']:
            run.xfs[x].sagUntil = run.tick + 30 * TPM
            n = 0
            for h in H[x * PER_XF:(x + 1) * PER_XF]:
                if n >= ev['count']:
                    break
                if h.state == 'HEALTHY' and h.w.cls != 'LIFE':
                    h.state = 'TRIPPED'
                    n += 1
    run.log.append({'t': run.tick, 'text': ev['label'], 'kind': 'event'})


def inject_twist(run: Run, key: str) -> None:
    if key not in TWISTS:
        raise ValueError("Unknown twist '%s'. Known: %s" % (key, ', '.join(TWISTS)))
    r = Mulberry32(run.world.seed * 31 + len(key) * 97 + len(run.firedTwists))
    H = run.homes
    if key == 'comms_outage':
        for h in H:
            if h.state == 'HEALTHY' and r() < 0.6:
                h.state = 'MUTE'
    elif key == 'cyber_spoof':
        pool = [h for h in H if h.state == 'HEALTHY' and h.w.xf != 6]
        for _ in range(5):
            if not pool:
                break
            h = pool.pop(int(math.floor(r() * len(pool))))
            h.state = 'ROGUE'
        run.m.roguesActivated += 5
        if run.m.rogueStart is None:
            run.m.rogueStart = run.tick
    elif key == 'psps':
        run.psps = {'xfs': [7, 8], 'from': run.tick + 20 * TPM, 'to': run.tick + 140 * TPM, 'kind': 'psps'}
        run.cuts.append(run.psps)
        run.xfs[7].pspsNotice = run.xfs[8].pspsNotice = True
    elif key == 'facility':
        run.facility = {'xf': 1, 'load': 45.0, 'dark': False, 'darkMin': 0.0}
    elif key == 'heat_spike':
        run.heatBoost += 0.15
    run.firedTwists.append(key)
    run.log.append({'t': run.tick, 'text': 'TWIST: %s. %s' % (TWISTS[key]['label'], TWISTS[key]['desc']), 'kind': 'twist'})


# ---------- decision feed ----------
def add_action(run: Run, h: Optional[Home], rung: str, kind: str, title: str, text: str,
               needs_approval: bool, **extra) -> dict:
    a = {'id': run.nextAction, 't': run.tick, 'home': h.w.idx if h else None,
         'xf': h.w.xf if h else extra.get('xf'), 'rung': rung, 'kind': kind, 'title': title, 'text': text,
         'status': 'AWAITING' if needs_approval else 'APPLIED', 'source': extra.pop('source', 'core')}
    a.update(extra)
    run.nextAction += 1
    run.feed.insert(0, a)
    del run.feed[3000:]
    if h:
        h.k.actions.insert(0, a)
    if needs_approval:
        run.m.approvals += 1
    return a


def decide(run: Run, action_id: int, approve: bool) -> bool:
    a = next((x for x in run.feed if x['id'] == action_id), None)
    if not a or a['status'] != 'AWAITING':
        return False
    a['status'] = 'APPLIED' if approve else 'DENIED'
    a['decidedAt'] = run.tick
    h = run.homes[a['home']] if a.get('home') is not None else None
    kind = a['kind']
    if not approve:
        if kind == 'REVIEW' and h:
            h.k.reviewed = True   # dispatcher sided with caution: re-knock later instead of acting
            h.k.lastProbe = run.tick
        return True
    if kind == 'ISOLATE' and h:
        h.isolated = True
        h.k.rung = 'HOLD'
    elif kind == 'TRUCK' and h:
        h.truck = True
        h.k.rung = 'HANDOFF'
        run.m.truckRolls += 1
    elif kind == 'ISLAND' and a.get('units'):
        for i in a['units']:
            hh = run.homes[i]
            hh.shed = True
            hh.k.patienceMode = True
            hh.k.rung = 'SLOW'
    elif kind == 'REVIEW' and h:
        h.k.reviewed = True
        _act_on_diagnosis(run, h, force=True)
    elif kind == 'COHORT_ROLLBACK' and a.get('units'):
        for i in a['units']:
            hh = run.homes[i]
            hh.fwRolledBack = True
            if hh.state == 'ROGUE' and not hh.isolated:
                hh.isolated = True
                hh.k.rung = 'HOLD'
    return True


# ---------- physics ----------
def physics(run: Run) -> None:
    t, r = run.tick, run.rng
    minute = t / TPM
    heat = heat_at(run.scen, minute) + run.heatBoost
    hod = clock_min(run) / 60
    dr = run.scen.get('dr')
    in_dr = bool(dr) and dr['from'] <= minute < dr['to']
    if run.cuts:
        cut_x = {x for c in run.cuts for x in c['xfs']}
        for x in cut_x:
            run.xfs[x].deenergized = any(x in c['xfs'] and c['from'] <= t < c['to'] for c in run.cuts)

    xf_sum = [0.0] * N_XF
    for h in run.homes:
        w, X = h.w, run.xfs[h.w.xf]
        if h.degradeFrom is not None and h.state != 'BROKEN':
            prog = clamp((t - h.degradeFrom) / max(1, h.failAt - h.degradeFrom), 0, 1)
            h.temp = 31 + 30 * prog + gauss(r) * 0.4
            if t >= h.failAt and not h.truck:
                h.state = 'BROKEN'
        elif h.state != 'BROKEN':
            h.temp = 31 + gauss(r) * 0.4

        h.ar = 0.97 * h.ar + 0.04 * math.sqrt(1 - 0.97 * 0.97) * gauss(r)
        if h.spike > 0:
            h.spike -= 1
        elif r() < 0.0006:
            h.spike = 30 + int(math.floor(r() * 60))
        load = w.base * shape(hod) * (1 + (heat - 1) * w.heatSens) * (1 + h.ar) + (2.2 if h.spike > 0 else 0)
        if h.slowed:
            load *= (1 - w.flexFrac)

        functional = h.state in ('HEALTHY', 'MUTE')
        grid_down = X.deenergized or t < X.fuseUntil or t < X.shedUntil
        h.islanded = grid_down or h.shed
        p, served = 0.0, True
        if h.islanded:
            L = load
            if run.mode == 'knock' and h.k.patienceMode:
                L = w.crit + (L - w.crit) * 0.45
            if (functional or (h.state == 'ROGUE' and h.isolated)) and h.soc > 0.05:
                p = min(L, w.pmax)
                if L > w.pmax:
                    run.m.unservedKwh += (L - w.pmax) * DT_H
            else:
                served = False
                run.m.unservedKwh += L * DT_H
            load = L
            h.net = 0.0
        else:
            if functional:
                p = h.cmd + h.probeOff
            elif h.state == 'ROGUE' and not h.isolated:
                p = -5.0 if h.soc < 0.98 else 0.0
            else:
                p = 0.0
            p = clamp(p, -w.pmax, w.pmax)
            if p > 0 and h.soc <= 0.06:
                p = 0.0
            if p < 0 and h.soc >= 0.995:
                p = 0.0
            h.net = load - p
        h.dark = not served
        h.load, h.p = load, p
        e = p * DT_H / 0.96 if p >= 0 else p * DT_H * 0.96
        h.soc = clamp(h.soc - e / w.cap, 0, 1)
        xf_sum[w.xf] += h.net
        h.meter = h.net + gauss(r) * 0.05
        drop = r() < 0.002
        if h.state == 'HEALTHY' and not drop:
            h.tele = {'p': p, 'soc': h.soc, 'temp': h.temp}
        elif h.state == 'ROGUE':
            h.tele = {'p': h.cmd, 'soc': h.soc, 'temp': h.temp}   # lies: reports what it was told
        else:
            h.tele = None
        if h.tele:
            h.lastSeen = t
            h.lastTele = dict(h.tele, t=t)
        if t % 3 == 0:
            h.hist.append({'t': t, 'meter': h.meter, 'rep': h.tele['p'] if h.tele else None,
                           'temp': h.lastTele['temp'] if h.lastTele else None})
            if len(h.hist) > 120:
                h.hist.pop(0)

    if run.facility:
        F, X = run.facility, run.xfs[run.facility['xf']]
        down = X.deenergized or t < X.fuseUntil or t < X.shedUntil
        F['dark'] = down
        if not down:
            xf_sum[F['xf']] += F['load'] * (1 + 0.02 * gauss(r))
        else:
            F['darkMin'] += DT_S / 60
            run.m.unservedKwh += F['load'] * DT_H
            if F['darkMin'] > 30:
                run.m.spoiled = True
            run.m.critAffected.add('FAC')

    for x, X in enumerate(run.xfs):
        X.meas = xf_sum[x] + gauss(r) * 0.25
        X.loading = X.meas / XF_LIMIT
        if X.deenergized or t < X.fuseUntil or t < X.shedUntil:
            X.volt = 0.0
        else:
            X.volt = 1.0 - 0.00062 * X.meas - (0.06 if (t < X.sagUntil and t < X.sagUntil - 29 * TPM) else 0)
        if t % 3 == 0:
            X.hist.append(X.meas)
            if len(X.hist) > 120:
                X.hist.pop(0)
        if X.loading > 1.15 and not X.deenergized and t >= X.fuseUntil:
            X.critOver += 1
        else:
            X.critOver = max(0, X.critOver - 1)
        if X.critOver > 5 * TPM:
            X.fuseUntil = t + 20 * TPM
            X.critOver = 0
            run.m.fuseTrips += 1
            run.log.append({'t': t, 'text': '%s fuse blew after 5 min above 115%% load' % X.id, 'kind': 'bad'})

    if in_dr:
        kw = sum(h.p for h in run.homes if not h.islanded and h.p > 0)
        run.m.drKwh += kw * DT_H
        run.m.drRevenue = run.m.drKwh * dr['priceKwh']


# ---------- KNOCK: diagnosis (Bayes) ----------
def normpdf(x, mu, s):
    return math.exp(-0.5 * ((x - mu) / s) ** 2) / (s * 2.5066)


def _silent(run: Run, h: Home) -> bool:
    """Telemetry counts as absent only after 3 missed reports (30 s); single dropouts are routine."""
    return not h.tele and run.tick - h.lastSeen >= 3


def diagnose(run: Run, h: Home) -> None:
    k, t, X = h.k, run.tick, run.xfs[h.w.xf]
    present = not _silent(run, h)
    L = PRIOR[:]
    ev: List[str] = []

    def mult(arr):
        for i in range(5):
            L[i] *= arr[i]

    if present:
        mult([0.99, 0.02, 0.02, 0.02, 0.99])
    else:
        mult([0.01, 0.98, 0.98, 0.98, 0.01])
        ev.append('No telemetry for %d s' % round((t - h.lastSeen) * DT_S))
    if k.probe and t - k.probe['ended'] < 30 * TPM:
        rr = k.probe['r']
        resp, none = normpdf(rr, 1, 0.25), normpdf(rr, 0, 0.25)
        mult([resp, resp, none, none, none])
        ev.append('%s (r = %.2f, SNR %.1f)' % ('Responded to knock' if rr > 0.5 else 'No response to knock', rr, k.probe.get('snr') or 0))
    sag = t < X.sagUntil
    if not present:
        if sag:
            mult([0.2, 0.2, 0.9, 0.2, 0.2])
            ev.append('Voltage sag at %s just before it went quiet' % X.id)
        else:
            mult([0.8, 0.8, 0.15, 0.8, 0.8])
    if k.trendFlag:
        mult([0.05, 0.05, 0.05, 0.9, 0.05])
        ev.append('Last readings showed rising inverter temperature')
    if present:
        if k.flagged:
            mult([0.01, 0.3, 0.3, 0.3, 0.95])
            ev.append('Smart meter disagrees with its own report (residual %.1f kW)' % (k.resid or 0))
        else:
            mult([0.99, 0.7, 0.7, 0.7, 0.05])
    s = sum(L) or 1.0
    k.post = [v / s for v in L]
    bi = max(range(5), key=lambda i: k.post[i])
    k.top, k.conf, k.evidence, k.diagTick = HYP[bi], k.post[bi], ev, t
    if run.ai is not None:
        try:
            k.ml = run.ai.second_opinion(run, h)
        except Exception as e:
            run.ai.degrade('classifier', e)
            k.ml = None


# ---------- KNOCK: patience ----------
def patience(run: Run, h: Home) -> dict:
    w = h.w
    believed = h.k.top in ('HEALTHY', 'MUTE')
    kwh = max(0.0, h.soc - 0.1) * w.cap if believed else 0.0
    L = max(0.5, forecast_load(run, h) * (1 - w.flexFrac * 0.8))
    cover = kwh / L
    heat = heat_now(run)
    if w.cls == 'LIFE':
        return {'hrs': min(2.0, kwh / w.crit), 'why': 'Life support: never islanded without a human decision'}
    if w.cls == 'MED':
        return {'hrs': 4 + cover, 'why': 'Insulin fridge tolerates 4 h, plus %.1f h of battery' % cover}
    if w.cls == 'TW':
        return {'hrs': max(0, w.deadlineH - 3) * 0.5 + cover, 'why': 'EV needed by 06:00; battery covers %.1f h' % cover}
    return {'hrs': cover + 6 * (0.5 if heat > 1.4 else 1),
            'why': 'Flexible; battery covers %.1f h%s' % (cover, ' (heat halves comfort margin)' if heat > 1.4 else '')}


# ---------- KNOCK controller ----------
def _units(run: Run, x: int) -> List[Home]:
    return run.homes[x * PER_XF:(x + 1) * PER_XF]


def _ai_agrees(h: Home) -> bool:
    """AI can only add caution: act automatically only if the model does not contradict Bayes."""
    ml = h.k.ml
    if not ml or h.k.reviewed:
        return True
    return ml['top'] == h.k.top or ml['conf'] < 0.6


def _pending_decision(h: Home, force: bool = False) -> bool:
    """Would the ladder take a new action on this unit right now?"""
    k = h.k
    return ((k.top == 'MUTE' and (k.conf > 0.8 or force) and not k.counted) or
            (k.top == 'TRIPPED' and (k.conf > 0.7 or force) and k.resetTried == 0) or
            (k.top == 'BROKEN' and (k.conf > 0.7 or force) and not k.counted and not h.truck) or
            (k.top == 'ROGUE' and (k.conf > 0.8 or force) and not k.counted))


def _act_on_diagnosis(run: Run, h: Home, force: bool = False) -> None:
    k, t = h.k, run.tick
    if not _pending_decision(h, force):
        return
    if not force and not _ai_agrees(h):
        if not any(a['kind'] == 'REVIEW' and a['status'] == 'AWAITING' for a in k.actions):
            run.m.reviews += 1
            add_action(run, h, 'HANDOFF', 'REVIEW', 'Second opinion needed: %s' % h.w.id,
                       'Physics-based diagnosis says %s (%d%%); the learned fault model says %s (%d%%). '
                       'KNOCK will not act on a disagreement by itself. Approve to follow the physics diagnosis, '
                       'or deny to knock again later.' % (k.top.lower(), round(k.conf * 100), k.ml['top'].lower(),
                                                           round(k.ml['conf'] * 100)), True, source='ai')
        return
    if k.top == 'MUTE' and (k.conf > 0.8 or force) and not k.counted:
        k.counted = True
        k.rung = 'WATCH'
        run.m.recovered += 1
        add_action(run, h, 'WATCH', 'RECOVER', '%s is alive, just not talking' % h.w.id,
                   'It answered the knock (r = %.2f). Comms are down, but it still follows commands, so it stays in '
                   'dispatch. A plain controller would have written it off.' % k.probe['r'], False)
    elif k.top == 'TRIPPED' and (k.conf > 0.7 or force) and k.resetTried == 0:
        k.resetTried = 1
        ok = h.w.resetOK
        if ok:
            h.state = 'HEALTHY'
            k.counted = True
            run.m.recovered += 1
            k.flagged = False
            k.cusum = 0
        add_action(run, h, 'ASK', 'RESET', ('Remote reset worked: ' if ok else 'Remote reset failed: ') + h.w.id,
                   'No response to the knock right after a voltage sag looked like a ride-through trip. One reset '
                   'command brought the inverter back.' if ok else 'The inverter did not come back after a reset. '
                   'Handing to a technician.', False)
        if not ok:
            add_action(run, h, 'HANDOFF', 'TRUCK', 'Send technician: ' + h.w.id,
                       'Tripped inverter did not respond to a remote reset.', True)
        k.rung = 'WATCH' if ok else 'HANDOFF'
    elif k.top == 'BROKEN' and (k.conf > 0.7 or force) and not k.counted and not h.truck:
        k.counted = True
        k.rung = 'HANDOFF'
        if not any(a['kind'] == 'TRUCK' for a in k.actions):
            add_action(run, h, 'HANDOFF', 'TRUCK', 'Send technician: ' + h.w.id,
                       'No response to the knock and its last readings showed overheating. This one is genuinely broken.', True)
    elif k.top == 'ROGUE' and (k.conf > 0.8 or force) and not k.counted:
        k.counted = True
        k.rung = 'HOLD'
        if run.m.rogueDetectS is None and run.m.rogueStart is not None:
            run.m.rogueDetectS = (t - run.m.rogueStart) * DT_S
        add_action(run, h, 'HOLD', 'ISOLATE', 'Hold %s (firmware %s)' % (h.w.id, h.w.fw),
                   'It reports following orders but ignores the knock, and its smart meter shows %.1f kW of unscheduled '
                   'charging. Isolate it from fleet commands and set it to safe idle?' % abs(k.resid or 0), True)


def knock_control(run: Run) -> None:
    t, H = run.tick, run.homes

    # 1. Watch: consistency residuals and degradation trends
    for h in H:
        k = h.k
        if h.tele and not h.islanded:
            expected = forecast_load(run, h) - h.tele['p']
            resid = h.meter - expected
            k.resid = resid
            k.cusum = min(40.0, max(0.0, k.cusum + abs(resid) - 3.0))
            if k.cusum > 8 and not k.flagged:
                k.flagged = True
            if h.lastTele and h.lastTele['temp'] > 48 and not k.trendFlag:
                k.trendFlag = True
                diagnose(run, h)
                if not h.truck:
                    run.m.maintCatches += 1
                    add_action(run, h, 'HANDOFF', 'TRUCK', 'Schedule service: ' + h.w.id,
                               'Inverter temperature climbing (%.0f C) while still working. Book a technician before '
                               'it fails.' % h.lastTele['temp'], True)

    # 2. Knock: coded active probing, per transformer
    for X in run.xfs:
        units = _units(run, X.i)
        live = not X.deenergized and t >= X.fuseUntil
        if X.probe:
            X.probe['meas'].append(X.meas)
            X.probe['chip'] += 1
            if X.probe['chip'] >= PROBE_L or not live:
                _finish_probe(run, X, live)
            else:
                for c in X.probe['codes']:
                    H[c['home']].probeOff = PROBE_A * WALSH[c['code']][X.probe['chip']]
            continue
        if not live or X.loading > 1.0 or X.volt < 0.94:
            continue  # safety: never probe a stressed transformer
        cands = []
        for h in units:
            k = h.k
            if h.islanded or h.isolated or h.truck:
                continue
            if t - k.lastProbe < 5 * TPM:
                continue
            silent = not h.tele and t - h.lastSeen >= 6
            suspect = bool(h.tele) and k.flagged and not k.counted and (not k.probe or t - k.probe['ended'] > 20 * TPM)
            stale = silent and k.probe is not None and (t - k.probe['ended'] > 20 * TPM) and k.top != 'BROKEN'
            if (silent and (not k.probe or stale)) or suspect:
                cands.append(h)
        cands = cands[:MAX_PROBE_PER_XF]
        if not cands:
            continue
        X.probe = {'start': t, 'chip': 0, 'meas': [], 'codes': [{'home': h.w.idx, 'code': 3 + j * 4} for j, h in enumerate(cands)]}
        for j, h in enumerate(cands):
            h.k.probing = True
            h.k.lastProbe = t
            h.k.rung = 'ASK'
            h.k.probes += 1
            run.m.probes += 1
            h.probeOff = PROBE_A * WALSH[3 + j * 4][0]
        add_action(run, None, 'ASK', 'PROBE', 'Knock on ' + ', '.join(h.w.id for h in cands),
                   'Sending +/-%.1f kW coded nudges on %s for %d s over %s. Each unit gets its own Walsh code, so one '
                   'transformer reading answers for all of them.' % (PROBE_A, X.id, PROBE_L * DT_S,
                                                                     run.scen.get('protocol', 'IEEE 2030.5')), False,
                   xf=X.i, units=[h.w.idx for h in cands])

    # 3. Diagnose + climb the ladder
    for h in H:
        k = h.k
        needs = _silent(run, h) or k.flagged or k.trendFlag or (k.probe is not None and t - k.probe['ended'] < 2)
        if not needs:
            if k.top != 'HEALTHY' and t - k.diagTick > 6:
                diagnose(run, h)
            continue
        if k.probing:
            continue
        diagnose(run, h)
        if not k.probe:
            continue
        if not _silent(run, h) and k.top == 'HEALTHY' and k.flagged and k.probe['r'] > 0.6:
            if k.clearedProbe == k.probe['ended']:
                continue                 # already cleared on this knock; a new flag needs a new knock
            k.clearedProbe = k.probe['ended']
            k.flagged = False
            k.cusum = 0
            k.rung = 'WATCH'
            if run.ai is not None and getattr(run.ai, 'load', None) is not None:
                run.ai.load.relearn(h.w.idx)   # battery is honest, so the load model was wrong: relearn this home
            add_action(run, h, 'WATCH', 'CLEAR', h.w.id + ' cleared',
                       'Meter mismatch was a household appliance, not the battery: it answered the knock exactly as '
                       'reported (r = %.2f).' % k.probe['r'], False)
            diagnose(run, h)
            continue
        _act_on_diagnosis(run, h)

    # 4. Planned or unplanned de-energization: prepare (pre-charge, essentials-only), then reconnect gently
    for P in run.cuts:
        names = ' and '.join(run.xfs[x].id for x in P['xfs'])
        if t >= P['to']:
            if not P.get('restoring'):
                P['restoring'] = True
                for x in P['xfs']:
                    for h in _units(run, x):
                        h.shed = True
                add_action(run, None, 'SLOW', 'RESTORE', 'Staggered reconnect on %s' % names,
                           'Power is back. Reconnecting homes two at a time, emptiest batteries first, so the cold-load '
                           'surge cannot blow the transformer fuse.', False, xf=P['xfs'][0])
            if t % TPM == 0:
                for x in P['xfs']:
                    X = run.xfs[x]
                    waiting = sorted([h for h in _units(run, x) if h.shed], key=lambda h: h.soc)
                    if waiting and X.meas < XF_LIMIT * 0.85:
                        for h in waiting[:2]:
                            h.shed = False
                            h.k.patienceMode = False
        else:
            for x in P['xfs']:
                for h in _units(run, x):
                    h.k.patienceMode = True
            if not P.get('announced'):
                P['announced'] = True
                if P['kind'] == 'damage':
                    add_action(run, None, 'SLOW', 'PREP', '%s lost grid power: essentials-only mode' % names,
                               'Unannounced outage. Every home there now runs on its own battery; KNOCK switches them to '
                               'essentials-only mode (deferrable loads paused, life support untouched) so batteries last '
                               'until crews restore the line.', False, xf=P['xfs'][0])
                else:
                    mins = max(0, (P['from'] - t) // TPM)
                    add_action(run, None, 'SLOW', 'PREP', 'Prepare %s for the shutoff' % names,
                               'A cut is announced in %d minutes. Charging every battery on %s from spare capacity before '
                               'it starts, and switching those homes to patience mode (deferrable loads paused, life '
                               'support untouched).' % (mins, names), False, xf=P['xfs'][0])

    # 5. Dispatch, slow, then propose minimal islanding
    for X in run.xfs:
        units = _units(run, X.i)
        live = not X.deenergized and t >= X.fuseUntil
        if not live:
            continue
        pre_psps = any(X.i in c['xfs'] and t < c['from'] for c in run.cuts)
        usable = [h for h in units if not h.islanded and not h.isolated and not h.truck and
                  ((not h.k.flagged) if h.tele else ((h.k.top == 'MUTE' and h.k.conf > 0.8) or
                                                     (h.k.top == 'HEALTHY' and h.state == 'HEALTHY')))]
        if X.probe:
            continue  # dispatch frozen during a 160 s knock so the decoder sees a clean signal
        if pre_psps:
            _dispatch(run, X, units, usable, 0.93, True)
            continue
        if X.prechargeUntil > t and X.loading < 0.85 and X.stage == 0:
            _dispatch(run, X, units, usable, 0.85, True)   # AI-predicted peak: fill batteries from headroom now
            continue
        dsp = _dispatch(run, X, units, usable, 0.9)
        over = X.meas / XF_LIMIT
        saturated = dsp['D'] >= dsp['capTot'] * 0.97
        X.hotTicks = X.hotTicks + 1 if (saturated and over > 0.97) else 0
        if X.stage == 0 and X.hotTicks >= TPM:
            X.stage, X.stageTick = 1, t
            to_slow = [h for h in units if not h.slowed and h.w.flexFrac > 0 and h.w.cls != 'LIFE' and not h.islanded]
            for h in to_slow:
                h.slowed = True
                if h.k.rung == 'WATCH':
                    h.k.rung = 'SLOW'
            add_action(run, None, 'SLOW', 'SLOW', 'Slow deferrable loads on ' + X.id,
                       '%s hit %d%% with every trusted battery already discharging. Pausing EV charging and nudging '
                       'thermostats 2 F on %d homes. Nobody loses power; that frees about %.0f kW.' % (
                           X.id, round(over * 100), len(to_slow), sum(h.load * h.w.flexFrac for h in to_slow)), False, xf=X.i)
        X.overTicks = X.overTicks + 1 if (over > 1.02 and saturated) else max(0, X.overTicks - 1)
        pending = any(a['kind'] == 'ISLAND' and a['xf'] == X.i and a['status'] == 'AWAITING' for a in run.feed)
        if X.stage >= 1 and X.overTicks > 2 * TPM and not pending and t - X.shedReqTick > 4 * TPM:
            excess = X.meas - XF_LIMIT * 0.9
            ranked = [(h, patience(run, h)) for h in units
                      if not h.islanded and h.w.cls not in ('LIFE', 'MED') and h.net > 0.3]
            ranked = sorted([o for o in ranked if o[1]['hrs'] > 2.5], key=lambda o: -o[1]['hrs'])
            chosen, got = [], 0.0
            for h, pt in ranked:
                if got >= excess:
                    break
                chosen.append((h, pt))
                got += h.net
            if chosen:
                run.m.islandRequests += 1
                X.shedReqTick, X.stage = t, 2
                add_action(run, None, 'HANDOFF', 'ISLAND',
                           'Island %d home%s on %s onto their own batteries' % (len(chosen), 's' if len(chosen) > 1 else '', X.id),
                           '%s is still at %d%% after slowing. Proposed: %s. Their lights stay on from their own batteries. '
                           'No life-support or medical homes included.' % (
                               X.id, round(over * 100), ', '.join('%s (%.1f h patience)' % (h.w.id, pt['hrs']) for h, pt in chosen)),
                           True, xf=X.i, units=[h.w.idx for h, _ in chosen])
        pend = next((a for a in run.feed if a['kind'] == 'ISLAND' and a['xf'] == X.i and a['status'] == 'AWAITING'), None)
        if pend and X.critOver >= 2 * TPM and not pend.get('urgent'):
            pend['urgent'] = True
            pend['title'] = 'URGENT: ' + pend['title']
        if pend and run.policy['emergencyAuto'] and X.critOver >= 3.5 * TPM:
            decide(run, pend['id'], True)
            pend['text'] += (' Applied automatically under the emergency policy: the %s fuse was 90 seconds from '
                             'blowing. Dispatcher notified.' % X.id)
            pend['auto'] = True
        if X.stage > 0:
            give_back = sum((h.load * h.w.flexFrac / max(0.2, 1 - h.w.flexFrac) if h.slowed else 0) + (h.load if h.shed else 0) for h in units)
            batt_head = sum(max(0.0, (min(DISPATCH_CAP, h.w.pmax) if h.soc > SOC_FLOOR else 0) - max(0.0, h.cmd)) for h in usable)
            proj = (X.meas + give_back - batt_head) / XF_LIMIT
            X.calm = X.calm + 1 if (proj < 0.88 and t - X.stageTick > 10 * TPM) else 0
            for h in units:
                if h.shed and h.soc < 0.15:
                    h.shed = False
                    if h.k.rung == 'SLOW':
                        h.k.rung = 'WATCH'
            if X.calm > 10 * TPM:
                for h in units:
                    h.slowed = h.shed = False
                    if h.k.rung == 'SLOW':
                        h.k.rung = 'WATCH'
                X.stage = X.calm = X.overTicks = 0
                add_action(run, None, 'WATCH', 'RESTORE', 'Back to normal on ' + X.id,
                           'Projected load with everything restored is %d%%. Deferrable loads resumed and islanded homes '
                           'reconnected.' % round(proj * 100), False, xf=X.i)

    # 6. Technicians: approved trucks arrive and repair after 40 minutes.
    for h in H:
        if h.truck and h.truckAt is None:
            h.truckAt = t + 40 * TPM
        if h.truck and t >= h.truckAt:
            h.truck, h.truckAt, h.state, h.degradeFrom, h.temp = False, None, 'HEALTHY', None, 31.0
            if h.w.fw == BAD_FW:
                h.fwRolledBack = True   # technician installs the safe firmware
            k = h.k
            k.trendFlag = k.counted = k.flagged = False
            k.resetTried, k.cusum, k.probe, k.rung = 0, 0.0, None, 'WATCH'
            h.lastTele = None
            add_action(run, h, 'WATCH', 'REPAIRED', 'Technician repaired ' + h.w.id, 'Unit back online and reporting.', False)

    # 7. AI layer: fleet insights and predictions (advisory; anything risky goes through approval)
    if run.ai is not None:
        try:
            run.ai.on_tick(run)
        except Exception as e:
            run.ai.degrade('insights', e)


def _finish_probe(run: Run, X: Xf, live: bool) -> None:
    P, H = X.probe, run.homes
    n = len(P['meas'])
    sx = sum(range(n))
    sy = sum(P['meas'])
    sxx = sum(i * i for i in range(n))
    sxy = sum(i * y for i, y in enumerate(P['meas']))
    slope = (n * sxy - sx * sy) / max(1e-9, n * sxx - sx * sx)
    icpt = (sy - slope * sx) / n
    d = [y - (icpt + slope * i) for i, y in enumerate(P['meas'])]
    for c in P['codes']:
        h = H[c['home']]
        acc = sum(WALSH[c['code']][i] * d[i] for i in range(n))
        r = -acc / (PROBE_A * n) if (live and n == PROBE_L) else 0.0
        h.probeOff = 0.0
        h.k.probing = False
        c['r'] = r
        h.k.probe = {'r': r, 'code': c['code'], 'chips': WALSH[c['code']][:n], 'resp': [-v / PROBE_A for v in d],
                     'started': P['start'], 'ended': run.tick, 'xf': X.id}
    resid = [v + sum(c['r'] * PROBE_A * WALSH[c['code']][i] for c in P['codes']) for i, v in enumerate(d)]
    sigma = math.sqrt(sum(v * v for v in resid) / max(1, n - len(P['codes']) - 2)) or 1e-3
    for c in P['codes']:
        h = H[c['home']]
        h.k.probe['sigma'] = sigma
        h.k.probe['snr'] = PROBE_A * math.sqrt(n) / sigma
        if live and n == PROBE_L and h.k.probe['snr'] < 2.5:
            h.k.probe = None
            h.k.lastProbe = run.tick - 4 * TPM
            h.k.retries += 1
    X.probe = None


def _dispatch(run: Run, X: Xf, units: List[Home], usable: List[Home], target: float, allow_charge: bool = False) -> dict:
    """Proportional controller on transformer loading; batteries share the work by available energy."""
    cur = sum((h.cmd if allow_charge else max(0.0, h.cmd)) for h in usable)
    err = X.meas - XF_LIMIT * target
    avail = [h for h in usable if h.soc > SOC_FLOOR]
    cap_tot = sum(min(DISPATCH_CAP, h.w.pmax) for h in avail)
    roomy = [h for h in usable if h.soc < 0.99]
    chg_tot = sum(min(DISPATCH_CAP, h.w.pmax) for h in roomy) if allow_charge else 0.0
    dr = run.scen.get('dr')
    minute = run.tick / TPM
    in_dr = bool(dr) and dr['from'] <= minute < dr['to']
    D = clamp(cur + err * 0.6, -chg_tot, cap_tot)
    if in_dr:
        D = cap_tot
    usable_ids = set(id(h) for h in usable)
    for h in units:
        if id(h) not in usable_ids:
            h.cmd = 0.0
    if D >= 0:
        avail_ids = set(id(h) for h in avail)
        wsum = sum((h.soc - SOC_FLOOR) * h.w.cap for h in avail) or 1.0
        for h in usable:
            h.cmd = min(min(DISPATCH_CAP, h.w.pmax), D * ((h.soc - SOC_FLOOR) * h.w.cap) / wsum) if id(h) in avail_ids else 0.0
    else:
        roomy_ids = set(id(h) for h in roomy)
        wsum = sum((1 - h.soc) * h.w.cap for h in roomy) or 1.0
        for h in usable:
            h.cmd = -min(min(DISPATCH_CAP, h.w.pmax), -D * ((1 - h.soc) * h.w.cap) / wsum) if id(h) in roomy_ids else 0.0
    return {'D': D, 'capTot': cap_tot}


# ---------- baseline (the blunt operator) ----------
def baseline_control(run: Run) -> None:
    t = run.tick
    for X in run.xfs:
        units = _units(run, X.i)
        live = not X.deenergized and t >= X.fuseUntil and t >= X.shedUntil
        for h in units:
            h.k.rung = 'WATCH' if h.tele else 'DEAD'
            if not h.tele and t - h.lastSeen == 30 * TPM:
                run.m.truckRolls += 1
                run.log.append({'t': t, 'text': 'Baseline: technician sent to %s (silent 30 min, presumed broken)' % h.w.id, 'kind': 'event'})
        if not live:
            continue
        usable = [h for h in units if h.tele]
        _dispatch(run, X, units, usable, 0.9)
        X.overTicks = X.overTicks + 1 if X.meas / XF_LIMIT > 1.02 else max(0, X.overTicks - 1)
        if X.overTicks > 3 * TPM:
            X.shedUntil = t + run.scen.get('bluntCutMin', 15) * TPM
            X.overTicks = 0
            run.log.append({'t': t, 'text': 'Baseline: %s, %s cut for %d min (%d homes%s)' % (
                run.scen.get('bluntName', 'rotating outage'), X.id, run.scen.get('bluntCutMin', 15), len(units), ' + cold-storage yard' if run.facility and run.facility['xf'] == X.i else ''), 'kind': 'bad'})


# ---------- metrics ----------
def update_metrics(run: Run) -> None:
    dark = off = 0
    for h in run.homes:
        if h.slowed:
            run.m.slowHomeMin += DT_S / 60
        if h.islanded:
            off += 1
        if h.dark:
            dark += 1
            h.darkMin += DT_S / 60
            h.everDark = True
            if h.w.cls in ('LIFE', 'MED'):
                run.m.critAffected.add(h.w.idx)
            run.m.custMin += DT_S / 60
    run.m.darkNow, run.m.offGridNow = dark, off
    run.m.darkPeak = max(run.m.darkPeak, dark)


def step(run: Run) -> None:
    if run.done:
        return
    minute = run.tick / TPM
    while run.pendingEvents and run.pendingEvents[0]['at'] <= minute:
        apply_event(run, run.pendingEvents.pop(0))
    try:
        physics(run)
    except Exception as e:  # pragma: no cover - containment path
        run.log.append({'t': run.tick, 'text': 'Degraded: physics error contained (%s)' % e, 'kind': 'bad'})
    try:
        if run.mode == 'baseline':
            baseline_control(run)
        else:
            knock_control(run)
    except Exception as e:  # pragma: no cover - containment path
        run.log.append({'t': run.tick, 'text': 'Degraded: controller error contained (%s). Holding last commands.' % e, 'kind': 'bad'})
    update_metrics(run)
    run.tick += 1
