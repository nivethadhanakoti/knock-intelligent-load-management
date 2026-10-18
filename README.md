# KNOCK — check before you shed

**Energy & Grid track, InnovateLA Dual-Use Hackathon (Base Power).**

KNOCK is a decision-support system for the dispatcher of a home-battery fleet. When batteries go silent or misbehave, the usual move is to assume they're dead and cut the street. KNOCK **knocks first**: it sends tiny coded power nudges and reads the answer on the transformer meter. That tells apart *mute* (alive, comms down), *tripped*, *broken* and *rogue* units. KNOCK then climbs a gentle ladder (dispatch → slow → island → hold → hand off) only as far as the situation needs.

An **AI layer** on top learns the fleet:

| Component | What it does | Technique |
|---|---|---|
| Load fingerprint | Learns each home's load from its own smart meter; powers the consistency check and the overload forecast | Online recursive least squares per home, priors, outlier rejection, lockout guard (numpy, vectorized) |
| Fault second opinion | Checks every diagnosis; KNOCK only acts alone when physics and the model agree, otherwise a person decides | Gradient-boosted classifier (scikit-learn), trained on 30 simulated worlds, balanced classes, held-out by world |
| Fleet insights | Finds bad firmware cohorts and rolls them back before they misbehave; predicts street overloads 45 min ahead and pre-charges; recognizes network outages | Hypergeometric enrichment test, model roll-forward with the weather forecast, temporal clustering |
| Copilot | Explains decisions, writes shift summaries, drafts multilingual homeowner texts; cannot act | LLM via IBM watsonx.ai (Granite), Anthropic, or OpenAI-compatible; offline composer fallback; grounded snapshot; action-claim guard |

## The dashboard

| Tab | What it shows |
|---|---|
| **Live operations** | Two illustrated neighborhoods (usual way vs KNOCK + AI), a clickable chart of every decision by type that filters the list, the copilot, the battery inspector, and disasters you can inject live |
| **Results and insights** | Small-multiple charts comparing KNOCK + AI, KNOCK core and the usual way, live or fast-forwarded to the end of the afternoon (what `make demo` prints), the AI insights timeline, and model quality |
| **Around the world** (Twist 2) | Five regions, each its own simulation: Los Angeles, Johannesburg, Adelaide, Chiba and Zhejiang, with a precomputed comparison and a "Run it live" button for each |
| **Funding pathway** (Twist 1) | NSF SBIR as primary and DoD ESTCP as dual-use, an evidence pack filled live from the run, and a one-click NSF Project Pitch draft |

The region picker in the header switches the whole simulation. The live disasters are scenario events, separate from the hackathon twists; see `docs/TWISTS.md` for what each hackathon twist changed in the build.

## Results (seed 42, approvals after 5 sim minutes; `make demo`)

| Heat-wave day | KNOCK + AI | KNOCK core | Usual way |
|---|---|---|---|
| Peak homes without power | **0** | 0 | 15 |
| Medical homes that lost power | **0** | 0 | 6 |
| Batteries that went rogue | **3** | 12 | 12 |
| Rogue batteries prevented | **9** | 0 | 0 |
| Home-minutes in eco mode | **2,247** | 2,851 | 0 |
| Technician trips | 3 | 3 | 11 |

| Ordinary Tuesday | KNOCK + AI | Usual way |
|---|---|---|
| Demand-response revenue | **$485** | $442 |
| Technician trips | **1** | 15 |
| Failing inverter caught early | **1** | 0 |

**Around the world:** in each region's own crisis, KNOCK + AI peaks at 0 homes without power, versus 15 in Los Angeles, 26 in Johannesburg, 19 in Adelaide, 13 in Chiba and 13 in Zhejiang the usual way (`make global`).

**Fault classifier, held out by world:** physics (Bayes) alone is 95.9% correct. The model agrees with it on 97.6% of knocks, and is 97.1% correct when it does. When they disagree, physics is right only 45% of the time. Those cases go to a person, which is exactly where human judgment adds value.

All numbers come from a simulated neighborhood with illustrative loads and prices.

## Run it

```bash
make setup            # install, train and sign the model on this machine, create sign-in accounts (passwords printed once)
make dev              # open http://localhost:8000 and sign in
make test             # 36 tests, about 2 min (trains the model first if this machine has none)
make demo             # headless comparison table (python scripts/demo.py crisis region=jhb for other regions)
make global           # recompute the five-region comparison (about 3 min)
make train            # retrain the fault classifier (about 2 min)
```

**Copilot with IBM Granite:** `cp .env.example .env`, set `LLM_PROVIDER=watsonx`, `WATSONX_API_KEY` and `WATSONX_PROJECT_ID`, then `make dev`. With no key it uses the offline composer, so the demo never depends on Wi-Fi.

## Layout

```
backend/knock/engine.py        simulator + KNOCK + baseline controllers (ported from the tested JS reference)
backend/knock/ai/forecaster.py learned load fingerprint + overload forecast
backend/knock/ai/classifier.py fault classifier: features, training data generator, training
backend/knock/ai/insights.py   cohort detection, overload pre-charge, outage pattern
backend/knock/ai/copilot.py    LLM copilot with providers, grounding and guardrails
backend/knock/ai/layer.py      wires AI into the engine with fault containment
backend/knock/regions.py       Twist 2: five region presets (events, blunt move, protocol, units, languages)
backend/knock/funding.py       Twist 1: funding pathway data, evidence pack, NSF pitch draft
models/global_results.json     precomputed five-region comparison
backend/app/                   FastAPI sessions, REST, WebSocket
frontend/index.html            dispatcher console (illustrated neighborhoods, feed, AI panel, copilot, inspector)
models/fault_classifier.pkl    trained and signed locally by `make setup` (never shipped: pickles can run code)
backend/app/security.py        sign-in, roles, region tenancy, two-person rule, audit chain, rate limits, headers
docs/                          architecture, responsible use, twists log, demo script, Bob log
```

## Security

KNOCK is locked down like a control room (details: `docs/SECURITY.md`):
- **Sign-in:** required for everything. Passwords are hashed; session cookies are signed and expiring.
- **Roles:** viewer < dispatcher < supervisor, checked on every API call.
- **Region tenancy:** a Johannesburg account cannot see or touch Los Angeles. Each country runs its own deployment.
- **Two-person rule:** firmware rollbacks and large actions need two different approvers.
- **Audit log:** hash-chained and tamper-evident, viewable on the **Security** tab.
- **Signed model:** the model file is verified before it is loaded.
- **Physics-based intrusion detection:** hacked batteries that lie are caught by their own meter and the knock.

Accounts: `python scripts/manage_users.py init | add NAME ROLE [REGIONS] | list`.

## Safety design

- The AI can only add caution. It never removes a human approval.
- Holds, islanding, technicians and firmware rollbacks need a person's approval. The one exception is an opt-in emergency policy that acts when a fuse is 90 seconds from blowing, and it never touches life-support or medical homes.
- The copilot is read-only.
- Every AI component is fault-contained: three errors and it switches itself off, and core KNOCK keeps running.

See `docs/RESPONSIBLE_USE.md`.
