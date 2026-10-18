"""Run the crisis scenario in every region with all three controllers and save the results for the dashboard.

    python scripts/global_results.py          # writes models/global_results.json (about 3 minutes)
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from knock import regions as R
from knock.harness import run_pair, summary

OUT = os.path.join(os.path.dirname(__file__), '..', 'models', 'global_results.json')

if __name__ == '__main__':
    out = {'generated': time.strftime('%Y-%m-%d %H:%M'), 'seed': 42, 'approveAfterMin': 5, 'regions': {}}
    for key in R.ORDER:
        t0 = time.time()
        runs = run_pair('crisis', region=key, modes=('knock_ai', 'knock', 'baseline'))
        a = runs['knock_ai']
        out['regions'][key] = {
            'region': R.public(key), 'scenarioName': a.scen['name'],
            'summary': {m: summary(r) for m, r in runs.items()},
            'insights': [{'clock': i['clock'][:5], 'title': i['title']} for i in a.ai.insights.items[::-1]],
            'events': [{'clock': '%02d:%02d' % divmod(a.scen['start'] + e['at'], 60), 'text': e['label']} for e in a.scen['events']],
        }
        print('%-6s %.0fs' % (key, time.time() - t0), flush=True)
    with open(OUT, 'w') as f:
        json.dump(out, f, indent=1, default=str)
    print('wrote', os.path.abspath(OUT))
