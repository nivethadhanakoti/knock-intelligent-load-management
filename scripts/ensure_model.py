"""Make sure a signed, compatible fault classifier exists on this machine; train one if not (about a minute).

Run automatically by `make setup` and `make test`. The model is never downloaded or shipped: pickles can run
code when loaded, so each installation trains and signs its own.
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from knock.ai.classifier import FaultClassifier, train

if __name__ == '__main__':
    if FaultClassifier.load() is not None:
        print('fault classifier: ok (signed, compatible)')
        sys.exit(0)
    print('fault classifier: %s -> training a new one on this machine...' % FaultClassifier.last_error, flush=True)
    t0 = time.time()
    seeds = int(os.environ.get('KNOCK_TRAIN_SEEDS', '30'))
    train(seeds=range(1, seeds + 1), verbose=False)
    ok = FaultClassifier.load() is not None
    print('fault classifier: %s in %.0f s' % ('trained and signed' if ok else 'FAILED: ' + str(FaultClassifier.last_error), time.time() - t0))
    sys.exit(0 if ok else 1)
