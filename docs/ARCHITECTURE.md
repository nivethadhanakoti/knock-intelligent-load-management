# Architecture

```
 smart meters ─┐      battery telemetry ─┐      transformer meters ─┐       weather forecast ─┐
               ▼                         ▼                          ▼                         ▼
        ┌─────────────────────────────── Observations (no hidden truth) ───────────────────────────────┐
        │                                                                                              │
        │  CORE KNOCK (deterministic, explainable)          AI LAYER (learned, advisory, contained)    │
        │  1 Watch    CUSUM consistency check  ◄──────────── load fingerprint (online RLS per home)    │
        │  2 Knock    Walsh-coded probes + SNR gate                                                    │
        │  3 Diagnose Bayesian posterior  ◄───────────────── fault classifier second opinion           │
        │             agree → act | disagree → REVIEW card for a person                                │
        │  4 Ladder   dispatch → slow → island → hold → hand off   ◄── overload forecast → pre-charge │
        │                                                  ◄── cohort detection → firmware rollback   │
        │                                                  ◄── outage pattern → "don't roll trucks"   │
        └──────────────────────────────────────────────────────────────────────────────────────────────┘
                    │ decision feed (approve / deny)                     │ read-only snapshot
                    ▼                                                    ▼
            dispatcher console  ◄──────────── explanations, summaries, texts ── copilot (Granite / Claude / offline)
```

## Engine hooks (`run.ai`)

| Hook | Called from | Effect |
|---|---|---|
| `forecast(run, home)` | consistency check, patience | Learned load replaces the physics prior once warmed up |
| `second_opinion(run, home)` | after every Bayesian diagnosis | Classifier verdict; the engine raises REVIEW on confident disagreement before acting |
| `on_tick(run)` | end of each KNOCK tick | Learn from this tick; fleet insights; predictions |
| `degrade(component, error)` | any AI exception | Three strikes and that component switches off; the core never stops |

## Sessions

Each session runs three controllers on one world, in lockstep:
- `knock_ai`: the run the dispatcher operates.
- `knock`: a hidden core-only shadow run, used to measure AI contribution.
- `baseline`: the blunt operator.

Twists are injected into all three.

## Why physics first, ML second

A utility can't deploy a black box on critical infrastructure on day one. The Bayesian core needs no training data and every step is explainable. The learned models improve with fleet history. Held-out tests show that requiring agreement before acting raises precision, and that the disagreements are the genuinely ambiguous cases (tripped vs broken with no warning signs), which are best decided by a person.
