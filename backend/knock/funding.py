"""Twist 1: the federal funding pathway, built into the product.

The dashboard's Funding tab shows the pathway and an *evidence pack*: live simulation results mapped to
what each program's reviewers score. The copilot's "pitch" task drafts an NSF Project Pitch from the same
numbers. Facts were checked on Oct 17, 2026; sources are in docs/FUNDING.md. Dates move, so every program
links to its official page and the UI says "verify before applying".
"""
from __future__ import annotations

PROGRAMS = [
    {
        'id': 'nsf', 'role': 'Primary', 'agency': 'National Science Foundation',
        'program': "NSF SBIR/STTR Phase I (America's Seed Fund, NSF 26-510)",
        'amount': 'Up to $305,000 for 6 to 18 months; Phase II about $1M+',
        'when': 'Three-page Project Pitch first (windows roughly March, July, November); invited teams submit a full proposal',
        'fit': 'Funds high-risk deep technology with a commercial path and no fixed topic list. KNOCK\'s coded probing and '
               'physics-plus-ML diagnosis is the technical risk; fleet operators and utilities are the market.',
        'evidence': ['recovered', 'diagnosis', 'trucks', 'dr'],
        'url': 'https://seedfund.nsf.gov/',
    },
    {
        'id': 'estcp', 'role': 'Dual-use (military)', 'agency': 'Department of War (DoD)',
        'program': 'ESTCP Installation Energy and Water (demonstrations on military installations)',
        'amount': 'Demonstration contracts, typically hundreds of thousands to several million dollars',
        'when': 'Open-topics BAA W912HQ26S0051 accepts white papers through Aug 20, 2027 (verify on the official notice)',
        'fit': 'Topics include energy resilience and cybersecurity of facility energy systems. A base\'s family-housing '
               'batteries and microgrid are a KNOCK fleet: keep mission and medical loads powered, catch rogue firmware.',
        'evidence': ['critical', 'rogue', 'fuses', 'darkPeak'],
        'url': 'https://serdp-estcp.mil/',
    },
    {
        'id': 'dow-sbir', 'role': 'Alternative', 'agency': 'Department of War (DoD)',
        'program': 'DoW SBIR/STTR FY27 Release 1',
        'amount': 'Air Force Phase I up to $300,000; Direct to Phase II up to $2M',
        'when': 'Opens Oct 28, 2026; closes Nov 25, 2026 at 12:00 p.m. ET',
        'fit': 'No topic is about home batteries. The closest are cyber resiliency for edge devices and reserve power; '
               'apply only if a topic fits, otherwise use ESTCP.',
        'evidence': ['rogue', 'diagnosis'],
        'url': 'https://www.dodsbirsttr.mil/',
    },
    {
        'id': 'doe', 'role': 'Alternative', 'agency': 'Department of Energy',
        'program': 'DOE SBIR/STTR (Phase I Releases 1 and 2 each year)',
        'amount': 'Phase I and II grants by topic; FY2027 topics expected after reauthorization',
        'when': 'Watch for Office of Electricity grid-edge topics; DOE has funded "secure communications at the '
                'edge-distribution system interface" before (DE-FOA-0003198)',
        'fit': 'Topic-driven: strong fit if a distribution or DER-coordination topic is released.',
        'evidence': ['recovered', 'darkPeak', 'dr'],
        'url': 'https://science.osti.gov/sbir',
    },
]

NOT_A_FIT = [
    {'program': 'DOE GRIP round 3 (SPARK, $1.9B)',
     'why': 'Transmission-focused (reconductoring, advanced transmission technology), 50% cost share, utilities and '
            'states as leads; full applications were due May 20, 2026. Worth partnering with a utility later, not as a startup.'},
]

STEPS = [
    {'when': 'Nov 2026', 'what': 'Check DoW SBIR FY27.1 topics (closes Nov 25). Register in SAM.gov. Pilot conversation with a fleet operator.'},
    {'when': 'Mar 2027', 'what': 'Submit the NSF Project Pitch (drafted by the copilot from simulation results).'},
    {'when': 'Mid 2027', 'what': 'NSF Phase I, $305K: field pilot of knocking on a real fleet; publish the accuracy numbers.'},
    {'when': 'By Aug 2027', 'what': 'ESTCP white paper for a demonstration on a military installation (dual-use).'},
    {'when': '2028+', 'what': 'Phase II (about $1M+). Then a Strategic Breakthrough Award (up to $30M) or Phase III procurement.'},
]

CONTEXT = ('SBIR/STTR were reauthorized on April 13, 2026 (S.3971) through September 30, 2031, after a six-month lapse. '
           'New rules: tighter foreign-ownership and security screening, per-company proposal caps from FY2027, and '
           'Strategic Breakthrough Awards of up to $30M for Phase II companies with matching capital.')


def evidence(m_ai: dict, m_base: dict, classifier_meta: dict | None) -> dict:
    """Map live results to the claims a reviewer will ask about."""
    cmp = (classifier_meta or {}).get('comparison') or {}
    hrs = lambda m: m['custMin'] / 60.0
    return {
        'darkPeak': {'label': 'Peak homes without power', 'knock': m_ai['darkPeak'], 'usual': m_base['darkPeak'],
                     'claim': 'Fewer homes dark at the worst moment'},
        'outageHours': {'label': 'Outage hours, all homes', 'knock': round(hrs(m_ai), 1), 'usual': round(hrs(m_base), 1),
                        'claim': 'Customer outage hours avoided'},
        'critical': {'label': 'Life-support / medical homes cut off', 'knock': m_ai['critAffected'], 'usual': m_base['critAffected'],
                     'claim': 'Critical loads protected'},
        'recovered': {'label': 'Silent batteries proven alive', 'knock': m_ai['recovered'], 'usual': 0,
                      'claim': 'Capacity recovered by knocking instead of writing off'},
        'trucks': {'label': 'Technician trips', 'knock': m_ai['truckRolls'], 'usual': m_base['truckRolls'],
                   'claim': 'Needless truck rolls avoided'},
        'rogue': {'label': 'Batteries that went rogue', 'knock': m_ai['roguesActivated'], 'usual': m_base['roguesActivated'],
                  'claim': 'Cyber/firmware threat contained (AI cohort rollback)'},
        'fuses': {'label': 'Transformer fuses blown', 'knock': m_ai['fuseTrips'], 'usual': m_base['fuseTrips'],
                  'claim': 'Equipment protected'},
        'dr': {'label': 'Demand-response revenue (illustrative)', 'knock': round(m_ai['drRevenue']), 'usual': round(m_base['drRevenue']),
               'claim': 'Earns money on ordinary days'},
        'diagnosis': {'label': 'Diagnosis accuracy when physics and ML agree',
                      'knock': round(100 * cmp['accuracyWhenAgree'], 1) if cmp.get('accuracyWhenAgree') else None,
                      'usual': None, 'claim': 'Technical feasibility, held out by world'},
    }


def pitch_text(snap: dict, ev: dict) -> str:
    """Offline NSF Project Pitch draft (the LLM version uses the same facts)."""
    g = lambda k, f='usual': ev[k][f]
    return (
        'NSF SBIR PROJECT PITCH (draft from simulation results)\n\n'
        '1. The technology innovation\n'
        'KNOCK is decision software for home-battery fleets. When a battery goes silent, it sends a tiny coded power nudge '
        '(a Walsh-code "knock") through existing control channels and decodes the answer from the transformer meter, '
        'telling mute, tripped, broken and rogue units apart. A physics-based Bayesian diagnosis is cross-checked by a '
        'learned classifier; disagreements go to a human. A learned load model forecasts overloads 45 minutes ahead and '
        'pre-charges batteries, and fleet analytics catch bad firmware cohorts before they spread.\n\n'
        '2. The technical objectives and challenges\n'
        'Phase I will (a) validate knock decoding on a real fleet under real meter latency and noise, (b) show diagnosis '
        'accuracy at or above the %s%% we measure in simulation when physics and ML agree, and (c) prove the probe stays '
        'within safety limits (0.5 kW, net zero energy, never on a stressed transformer).\n\n'
        '3. The market opportunity\n'
        'Home batteries are joining the grid fast, and operators still treat a silent battery as a dead one. In a '
        'simulated %s, the usual approach left %s homes dark at peak, cut off %s medical homes and sent %s technicians; '
        'with KNOCK the numbers were %s, %s and %s. On an ordinary day the same software keeps quiet batteries earning '
        'demand-response revenue. Customers: battery fleet operators, utilities, and military installations (dual-use).\n\n'
        '4. The company and team\n'
        '[Team names, roles and relevant experience. Pilot partner: a home-battery fleet operator.]'
        % (g('diagnosis', 'knock') or 'about 97', snap.get('scenario', 'heat event') + ' in ' + snap.get('region', 'Los Angeles'), g('darkPeak'), g('critical'), g('trucks'),
           g('darkPeak', 'knock'), g('critical', 'knock'), g('trucks', 'knock'))
    )
