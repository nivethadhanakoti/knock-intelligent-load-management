# IBM Bob playbook

**The Best Build with IBM Bob prize is judged on how central Bob was to what you built, and you must say how on Devpost.** The hackathon rules also require listing every AI tool used.

This repo's starting point was generated with Claude. Say so truthfully on Devpost. Your path to the Bob prize is to make Bob the tool that carries the project from here, through the live twists, the AI extensions and the hardening, and to log it as you go in `docs/BOB_LOG.md`.

## Setup (10 minutes, everyone)

1. Install IBM Bob and start the free trial (link in the Participant Hub).
2. Open this repo folder in Bob.
3. First prompt, to give Bob the context:

   > Read README.md, docs/ARCHITECTURE.md, backend/knock/engine.py and backend/knock/ai/*.py. Summarize how KNOCK works, the engine hooks the AI layer uses, and the safety rules (AI only adds caution; holds, islands, trucks and rollbacks need human approval). Then run `make test` and report the result.

4. Save Bob's summary into `docs/BOB_LOG.md` as entry 1.

## Bob tasks, in priority order

Each prompt is ready to paste. After each one:
- run `make test`;
- commit with a message starting `bob:`;
- add a BOB_LOG entry: the prompt, what Bob changed, and what you changed by hand.

### 1. Live twists (highest value: also wins Best Shift Adapter)
When a twist drops, paste the organizer's twist text into this prompt:
> A new twist just dropped: "<paste twist text>". Run `python scripts/new_twist.py <short_name>`. Then implement the twist so it changes hidden truth in the simulation (never give the controllers direct knowledge of it). Register it in engine.TWISTS and inject_twist. Decide whether KNOCK's existing ladder handles it, or whether it needs a new predicate, action or AI insight. Implement the gentlest response. Keep every safety rule. Finish the generated test so it asserts KNOCK + AI does at least as well as the baseline. Fill in docs/TWISTS.md with the before/after table from `python scripts/demo.py crisis 60:<short_name>`.

### 2. Real IBM Granite for the copilot
> In backend/knock/ai/copilot.py, verify WatsonxProvider against the current watsonx.ai chat API, fix any field names, and add retry with backoff for 429/5xx. Add a test that mocks httpx and checks the request shape. Then add a `/api/copilot/health` endpoint that reports which provider is active and whether a test call succeeds.

### 3. A new AI insight: transformer aging
> Add an insight to backend/knock/ai/insights.py that tracks how many minutes each transformer spends above 100% load and estimates insulation aging with a simple Arrhenius-style rule (aging doubles for every 6 °C of hot-spot rise above rated). Show it as a fleet insight, and a column in the console's transformer tooltip. Advisory only. Add a test.

### 4. Classifier v2
> Improve backend/knock/ai/classifier.py. Add features for knock-response trend across repeated knocks and the battery's recent state of charge. Re-run `make train`, compare held-out balanced accuracy and the "accuracy when agree" metric with the current model, and keep the new model only if both improve. Record both sets of numbers in docs/BOB_LOG.md.

### 5. Hardening
> Review backend/app/main.py and sessions.py for thread-safety and failure modes: concurrent decide and step, a WebSocket client that disappears mid-send, sessions that are never closed. Fix what you find, add tests, and add a replay mode: `GET /api/sessions/{id}/export` returns the twist and decision log so a run can be replayed exactly.

### 6. Accessibility pass on the console
> Audit frontend/index.html for keyboard navigation (the canvas houses need a keyboard path), color-only signals, and screen-reader labels for the decision feed. Fix the issues and keep the visual design.

## BOB_LOG.md entry template

```
## <n>. <task name>   (<time>, <who>)
Prompt: <paste>
What Bob produced: <files/functions, one or two lines>
What we changed by hand: <honest>
Result: make test -> <N passed>; demo numbers before/after if relevant
```

## Devpost "How we built with IBM Bob" section (fill in from the log)

> We started from a working simulator and spec, then used IBM Bob as our primary engineering partner for everything that followed. Bob <implemented each of our three live-twist responses / integrated IBM Granite on watsonx.ai into the copilot / added the transformer-aging insight / hardened the API>. Every Bob change was test-gated (`make test`) and logged with its prompt in docs/BOB_LOG.md (<N> entries). Other AI tools used: Claude (initial scaffold and spec).
