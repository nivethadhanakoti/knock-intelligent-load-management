"""Train the fault classifier on simulated fleet history and save it to models/fault_classifier.pkl."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from knock.ai.classifier import train

if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    t0 = time.time()
    train(seeds=range(1, n + 1))
    print('done in %.0f s' % (time.time() - t0))
