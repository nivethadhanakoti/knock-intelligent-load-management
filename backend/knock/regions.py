"""Region presets (Twist 2: "make it global").

KNOCK's core idea (knock before you shed) does not depend on where the grid is. What changes from place to
place is the local blunt move, the kind of crisis, the command protocol, units, languages and who owns the
batteries. Each preset below changes the *simulation itself*: the crisis events, the start time, the weather
load curve, and how long the blunt operator cuts a street for. Los Angeles is the reference and is unchanged.

Facts behind each preset are cited in docs/GLOBAL.md.
"""
from __future__ import annotations

from typing import Dict, List

# Event types used here beyond the Los Angeles set (handled in engine.apply_event):
#   scheduled_cut  utility announces in advance that streets will be de-energized (KNOCK prepares)
#   grid_damage    streets lose grid power without warning (storm damage)
#   mass_mute      a share of healthy batteries lose their uplink at once (network down)
#   mass_trip      a frequency or voltage disturbance trips inverters on several streets

REGIONS: Dict[str, dict] = {
    'la': {
        'key': 'la', 'name': 'Los Angeles, USA', 'short': 'Los Angeles',
        'streets': ['Alder Avenue', 'Birch Street', 'Cedar Lane', 'Oak Street', 'Elm Street', 'Fig Avenue',
                    'Juniper Way', 'Maple Drive', 'Palm Court'],
        'grid': '60 Hz, 120/240 V split phase', 'protocol': 'IEEE 2030.5 (California Rule 21)',
        'tempUnit': 'F', 'tempBase': 80, 'tempSlope': 40,
        'bluntName': 'rotating outage', 'bluntCutMin': 15,
        'languages': ['en', 'es', 'zh', 'ko', 'vi', 'tl', 'hy'], 'defaultLang': 'es',
        'crisisName': 'Santa Ana heat event', 'fleet': 'Home batteries run by a fleet operator such as Base Power',
        'localBlunt': 'Rotating outages and Public Safety Power Shutoffs (PSPS) during fire weather',
        'why': 'Wildfire weather and heat waves stress the grid; silent batteries are written off and whole streets are cut.',
    },
    'jhb': {
        'key': 'jhb', 'name': 'Johannesburg, South Africa', 'short': 'Johannesburg',
        'streets': ['Acacia Street', 'Baobab Road', 'Jacaranda Avenue', 'Protea Street', 'Marula Lane', 'Kiaat Road',
                    'Msasa Street', 'Aloe Avenue', 'Fynbos Close'],
        'grid': '50 Hz, 230 V', 'protocol': 'Vendor cloud APIs and SunSpec Modbus (no national DER command protocol)',
        'tempUnit': 'C', 'tempBase': 14, 'tempSlope': -16,         # winter evening: colder means more heating load
        'bluntName': 'load reduction', 'bluntCutMin': 120,
        'languages': ['en', 'zu', 'af', 'st'], 'defaultLang': 'zu',
        'crisisName': 'Winter evening peak and load reduction',
        'fleet': 'Home inverter-battery systems bought for backup during load shedding',
        'localBlunt': 'Load reduction: Eskom cuts whole suburbs for 4 to 5 hours when a local transformer is overloaded',
        'why': 'The blunt move is literally KNOCK\'s target: overloaded transformer, so the whole area goes dark. '
               'KNOCK turns that into slow, island and pre-charge, street by street.',
        'start': 17 * 60, 'heat': [[0, 1.05], [45, 1.5], [120, 1.55], [180, 1.3]],
        'events': lambda W: [
            {'at': 0, 'type': 'scheduled_cut', 'xfs': [1, 5], 'from': 60, 'to': 180,
             'label': 'Load-reduction notice: Baobab Road and Kiaat Road will be cut from 18:00 to 20:00'},
            {'at': 20, 'type': 'trip', 'xf': 3, 'units': W['trip'], 'label': 'Voltage dip on Protea Street trips 4 inverters'},
            {'at': 28, 'type': 'mute', 'units': W['mute'], 'label': 'Mobile network congestion: 6 batteries stop reporting'},
            {'at': 70, 'type': 'rogue', 'units': W['rogue1'], 'label': 'Firmware 4.2.1 starts unscheduled charging on 3 units'},
            {'at': 95, 'type': 'rogue', 'units': W['rogue2'], 'label': 'Firmware 4.2.1 activates on more units'},
            {'at': 125, 'type': 'rogue', 'units': W['rogue3'], 'label': 'Firmware 4.2.1 activates on the rest of the cohort'},
        ],
    },
    'adl': {
        'key': 'adl', 'name': 'Adelaide, Australia', 'short': 'Adelaide',
        'streets': ['Banksia Street', 'Wattle Avenue', 'Grevillea Road', 'Bottlebrush Lane', 'Jarrah Street',
                    'Karri Road', 'Mallee Avenue', 'Saltbush Court', 'Coolibah Way'],
        'grid': '50 Hz, 230 V', 'protocol': 'CSIP-AUS (the Australian profile of IEEE 2030.5)',
        'tempUnit': 'C', 'tempBase': 27, 'tempSlope': 25,
        'bluntName': 'rotational load shedding', 'bluntCutMin': 30,
        'languages': ['en', 'zh', 'vi', 'it'], 'defaultLang': 'zh',
        'crisisName': '45 °C heatwave with the state islanded from the grid',
        'fleet': 'Rebated home batteries (Cheaper Home Batteries); most are not yet in a virtual power plant',
        'localBlunt': 'Rotational load shedding, and the emergency backstop that remotely switches rooftop solar off',
        'why': 'Hundreds of thousands of new home batteries, but only about one in ten is coordinated. KNOCK makes the '
               'uncoordinated ones safe to rely on.',
        'start': 15 * 60, 'heat': [[0, 1.1], [60, 1.7], [150, 1.7], [180, 1.45]],
        'events': lambda W: [
            {'at': 20, 'type': 'trip', 'xf': 3, 'units': W['trip'], 'label': 'Voltage dip on Bottlebrush Lane trips 4 inverters'},
            {'at': 35, 'type': 'mass_trip', 'xfs': [2, 4, 7], 'count': 3,
             'label': 'Interconnector trips and South Australia is islanded: a frequency dip trips inverters on 3 streets'},
            {'at': 50, 'type': 'mute', 'units': W['mute'], 'label': 'VPP cloud outage: 6 batteries stop reporting'},
            {'at': 70, 'type': 'rogue', 'units': W['rogue1'], 'label': 'Firmware 4.2.1 starts unscheduled charging on 3 units'},
            {'at': 95, 'type': 'rogue', 'units': W['rogue2'], 'label': 'Firmware 4.2.1 activates on more units'},
            {'at': 125, 'type': 'rogue', 'units': W['rogue3'], 'label': 'Firmware 4.2.1 activates on the rest of the cohort'},
        ],
    },
    'chiba': {
        'key': 'chiba', 'name': 'Chiba, Japan', 'short': 'Chiba',
        'streets': ['Sakura-dori', 'Matsu-dori', 'Ume-dori', 'Kaede-dori', 'Take-dori', 'Kiku-dori', 'Fuji-dori',
                    'Hinoki-dori', 'Ayame-dori'],
        'grid': '50 Hz, 100/200 V (eastern Japan)', 'protocol': 'ECHONET Lite (home energy management standard)',
        'tempUnit': 'C', 'tempBase': 26, 'tempSlope': 18,
        'bluntName': 'planned outage', 'bluntCutMin': 30,
        'languages': ['ja', 'en', 'zh', 'ko', 'vi'], 'defaultLang': 'ja',
        'crisisName': 'Typhoon landfall',
        'fleet': 'Home batteries paired with rooftop solar whose feed-in contracts have ended',
        'localBlunt': 'Storm damage leaves streets dark for days; batteries that stop reporting are assumed lost',
        'why': 'Typhoons take out cell towers and power lines together. Silence is the norm, not a fault, so knocking '
               'matters most here.',
        'start': 13 * 60, 'heat': [[0, 1.2], [60, 1.5], [150, 1.55], [180, 1.4]],   # hot, humid air behind the storm
        'events': lambda W: [
            {'at': 15, 'type': 'mass_mute', 'frac': 0.5, 'label': 'Typhoon winds knock out cell towers: half of all batteries go silent'},
            {'at': 25, 'type': 'grid_damage', 'xfs': [2], 'minutes': 155,
             'label': 'Fallen trees cut the line to Ume-dori: the street loses grid power without warning'},
            {'at': 30, 'type': 'trip', 'xf': 3, 'units': W['trip'], 'label': 'Voltage dip on Kaede-dori trips 4 inverters'},
            {'at': 70, 'type': 'rogue', 'units': W['rogue1'], 'label': 'Firmware 4.2.1 starts unscheduled charging on 3 units'},
            {'at': 95, 'type': 'rogue', 'units': W['rogue2'], 'label': 'Firmware 4.2.1 activates on more units'},
            {'at': 125, 'type': 'rogue', 'units': W['rogue3'], 'label': 'Firmware 4.2.1 activates on the rest of the cohort'},
        ],
    },
    'zj': {
        'key': 'zj', 'name': 'Zhejiang, China', 'short': 'Zhejiang',
        'streets': ['Hehua Lu', 'Guihua Lu', 'Meihua Lu', 'Lanhua Lu', 'Zhuyuan Lu', 'Songlin Lu', 'Liuxi Lu',
                    'Taoyuan Lu', 'Chaxiang Lu'],
        'grid': '50 Hz, 220 V', 'protocol': 'Provincial VPP platform interface (national VPP standard GB/T 47241-2026)',
        'tempUnit': 'C', 'tempBase': 30, 'tempSlope': 18,
        'bluntName': 'orderly power-use rationing', 'bluntCutMin': 60,
        'languages': ['zh', 'en'], 'defaultLang': 'zh',
        'crisisName': 'Summer peak with operator network faults',
        'fleet': 'Distributed batteries aggregated into a provincial virtual power plant; the same engine fits '
                 'telecom base-station backup batteries',
        'localBlunt': 'Orderly power-use rationing that takes whole areas off supply in rotation at peak',
        'why': 'China is building virtual power plants at national scale. A VPP is only as good as its knowledge of '
               'which batteries are really there; KNOCK supplies that.',
        'start': 14 * 60, 'heat': [[0, 1.05], [60, 1.6], [150, 1.65], [180, 1.4]],
        'events': lambda W: [
            {'at': 20, 'type': 'trip', 'xf': 3, 'units': W['trip'], 'label': 'Voltage dip on Lanhua Lu trips 4 inverters'},
            {'at': 28, 'type': 'mass_mute', 'frac': 0.15, 'label': 'Operator network fault: about 15% of batteries stop reporting'},
            {'at': 40, 'type': 'degrade', 'units': [W['broken']], 'failAt': 65, 'label': 'An inverter on Guihua Lu starts overheating'},
            {'at': 70, 'type': 'rogue', 'units': W['rogue1'], 'label': 'Firmware 4.2.1 starts unscheduled charging on 3 units'},
            {'at': 95, 'type': 'rogue', 'units': W['rogue2'], 'label': 'Firmware 4.2.1 activates on more units'},
            {'at': 125, 'type': 'rogue', 'units': W['rogue3'], 'label': 'Firmware 4.2.1 activates on the rest of the cohort'},
        ],
    },
}

ORDER: List[str] = ['la', 'jhb', 'adl', 'chiba', 'zj']


def get(key: str) -> dict:
    if key not in REGIONS:
        raise ValueError("Unknown region '%s'. Known: %s" % (key, ', '.join(ORDER)))
    return REGIONS[key]


def public(key: str) -> dict:
    """JSON-safe description for the UI (no lambdas)."""
    r = get(key)
    return {k: v for k, v in r.items() if k not in ('events', 'heat')}


def temperature(region: dict, heat: float) -> int:
    return round(region['tempBase'] + (heat - 1) * region['tempSlope'])
