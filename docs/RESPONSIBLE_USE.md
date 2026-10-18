# Responsible use

**How it could be misused:** fleet commands, knocks and firmware rollbacks could be abused to manipulate home batteries or stress the grid. Per-home telemetry and meter data can reveal when people are home, and an LLM could be prompted to overstate what it knows.

**How we limit that:**
- Knocks are capped at 0.5 kW and net zero energy, at most once per battery per 5 minutes, and never on a stressed transformer.
- Every hold, islanding, technician dispatch and firmware rollback needs a person's approval. The emergency policy is opt-in and visible, acts only when a fuse is 90 seconds from blowing, and never includes life-support or medical homes.
- The AI layer can only add caution: a model disagreement creates a review card, never an action.
- The copilot is read-only, receives an aggregated snapshot rather than raw meter streams, and is checked for claims of having acted.
- Every action is logged with its evidence. AI components are fault-contained and switch off on repeated errors.

**Access and integrity:**
- Sign-in with roles and per-region accounts.
- A two-person rule for fleet-wide changes.
- A tamper-evident audit log.
- A signed model file.
- One deployment per country, so data stays in-country.

Full threat model in `docs/SECURITY.md`.

**Two lines for Devpost:** KNOCK's commands and data could be abused to manipulate home batteries or infer occupancy. We cap and rate-limit probes, require sign-in with roles and region-scoped accounts, need two people for fleet-wide changes, keep the AI advisory and the copilot read-only, and record every action in a tamper-evident audit log.
