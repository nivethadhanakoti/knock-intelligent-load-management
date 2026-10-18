"""Scaffold a live twist: a fault hook, a test, and a TWISTS.md entry.

    python scripts/new_twist.py substation_fire
Then fill in the TODOs, run `make test`, and commit with tag twist-N.
"""
import os, re, sys, datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
name = sys.argv[1] if len(sys.argv) > 1 else ''
if not re.fullmatch(r'[a-z][a-z0-9_]{2,40}', name):
    sys.exit('Give a snake_case twist name, e.g. python scripts/new_twist.py substation_fire')

hook = os.path.join(ROOT, 'backend', 'knock', 'twists_custom.py')
if not os.path.exists(hook):
    open(hook, 'w').write('"""Custom live twists. Each function mutates a Run; register it in engine.TWISTS + inject_twist."""\n'
                          'from . import engine as E\n\n')
with open(hook, 'a') as f:
    f.write('\n\ndef %s(run: E.Run) -> None:\n    """TWIST: describe what changed in the world."""\n'
            '    # TODO: change hidden truth here (states, loads, energization). Controllers must discover it themselves.\n'
            '    run.log.append({"t": run.tick, "text": "TWIST: %s", "kind": "twist"})\n' % (name, name.replace('_', ' ')))

test = os.path.join(ROOT, 'backend', 'tests', 'test_twist_%s.py' % name)
open(test, 'w').write('''import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from knock.harness import run_pair, summary


def test_%s_knock_copes_better_than_baseline():
    runs = run_pair('crisis', twists=[(60, '%s')], modes=('knock_ai', 'baseline'))
    k, b = summary(runs['knock_ai']), summary(runs['baseline'])
    assert k['darkPeak'] <= b['darkPeak']
    assert k['crit'] <= b['crit']
''' % (name, name))

doc = os.path.join(ROOT, 'docs', 'TWISTS.md')
with open(doc, 'a') as f:
    f.write('\n## Twist: %s  (%s)\n\n**What changed in the problem:** TODO\n\n**What we changed in the build:** TODO (files, functions)\n\n'
            '**Why this is the gentlest response:** TODO\n\n**Proof:** `make test` (test_twist_%s.py) and `python scripts/demo.py crisis 60:%s`\n\n'
            '| metric | KNOCK + AI | usual way |\n|---|---|---|\n| peak homes dark |  |  |\n| medical homes hit |  |  |\n\n**Commit:** tag `twist-N`\n'
            % (name, datetime.date.today(), name, name))
print('Created:\n  backend/knock/twists_custom.py (hook)\n  %s\n  docs/TWISTS.md entry\nNext: register "%s" in engine.TWISTS and inject_twist, implement the hook, run make test.' % (os.path.relpath(test, ROOT), name))
