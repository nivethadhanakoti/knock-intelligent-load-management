"""Headless comparison: KNOCK + AI vs core KNOCK vs the usual way, with optional twists.

    python scripts/demo.py                      # heat-wave day
    python scripts/demo.py tuesday              # ordinary Tuesday
    python scripts/demo.py crisis 60:comms_outage 95:heat_spike
    python scripts/demo.py crisis region=jhb      # Johannesburg (regions: la jhb adl chiba zj)
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from knock.harness import run_pair, table

if __name__ == '__main__':
    scen = sys.argv[1] if len(sys.argv) > 1 else 'crisis'
    region = next((a.split('=')[1] for a in sys.argv[2:] if a.startswith('region=')), 'la')
    twists = [(int(a.split(':')[0]), a.split(':')[1]) for a in sys.argv[2:] if ':' in a]
    runs = run_pair(scen, twists=twists, modes=('knock_ai', 'knock', 'baseline'), region=region)
    print('Scenario: %s   region: %s   twists: %s   (approvals auto-granted after 5 sim minutes)\n' % (scen, region, twists or 'none'))
    print(table(runs))
    ai = runs['knock_ai'].ai
    print('\nAI insights:')
    for it in ai.insights.items[::-1]:
        print('  %s  %s' % (it['clock'][:5], it['title']))
