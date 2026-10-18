# Live twists: what changed in the build

Judging rule: *how well the final submission takes the twist in*. Each twist below changed the product itself, and each is covered by tests.

## Twist 1 · Identify the government funding pathway

**What changed in the build:**
- `backend/knock/funding.py` (new): program data (checked Oct 17, 2026), the plan, a ruled-out list, and `evidence()`, which maps live simulation results to what reviewers score.
- `GET /api/sessions/{id}/funding` (new) and the **Funding pathway** dashboard tab:
  - the plan;
  - program cards;
  - an evidence pack that updates live from the run on screen.
- The copilot's new `pitch` task drafts an **NSF SBIR Project Pitch** from the run's numbers, using the offline composer or an LLM. There's a button on the Funding tab and a quick action in the copilot.

**The pathway:**
- **Primary:** NSF SBIR Phase I (America's Seed Fund): up to $305K, via the Project Pitch.
- **Dual-use:** DoD ESTCP Installation Energy & Water demonstration (energy-resilience and cybersecurity topics; open-topics BAA through Aug 20, 2027).
- **Alternatives:** DoW SBIR FY27.1 (closes Nov 25, 2026) and DOE SBIR grid-edge topics.
- **Ruled out:** DOE GRIP round 3, which is transmission-focused.

Sources are in `docs/FUNDING.md`.

**Proof:**
- `test_funding_evidence_and_pitch`
- The Funding tab while a scenario runs.

## Twist 2 · Make it global

**What changed in the build:**
- `backend/knock/regions.py` (new): five region presets.
- `engine.scenario_defs(world, region)` builds a different crisis per region. The engine gained four event types:
  - `scheduled_cut`: an announced outage; KNOCK pre-charges and goes essentials-only.
  - `grid_damage`: an unannounced storm outage.
  - `mass_mute`: a network outage.
  - `mass_trip`: a frequency dip.
- PSPS handling was generalized into a list of cuts.
- The baseline uses each region's own blunt move and its duration, e.g. Johannesburg "load reduction" cuts a street for 2 hours.
- Knock commands name the region's protocol:
  - IEEE 2030.5 in Los Angeles
  - CSIP-AUS in Adelaide
  - ECHONET Lite in Chiba
  - GB/T 47241-2026 platform interface in Zhejiang
  - vendor APIs / SunSpec Modbus in Johannesburg
- Temperatures show in °C outside the US, and street names and homeowner-text languages change per region (isiZulu, Afrikaans, Japanese and others).
- A region picker in the header. The **Around the world** tab shows a precomputed five-region comparison (`scripts/global_results.py` writes `models/global_results.json`) and a "Run it live" button per region.

**Result (peak homes without power, KNOCK + AI vs the usual way):**

| Region | KNOCK + AI | Usual way |
|---|---|---|
| Los Angeles | 0 | 15 |
| Johannesburg | 0 | 26 |
| Adelaide | 0 | 19 |
| Chiba | 0 | 13 |
| Zhejiang | 0 | 13 |

Medical homes cut off: 0 in every region with KNOCK, versus 4 to 6 the usual way.

**Proof:**
- `test_every_region_builds_a_distinct_crisis`
- `test_los_angeles_unchanged_by_region_support`
- `test_johannesburg_load_reduction_knock_beats_blunt_move`
- `test_global_results_file_is_present_and_consistent`

Sources are in `docs/GLOBAL.md`.

## Twist 3 · Poster it

**What changed:** a one-page Letter poster, made as a design canvas and exported as a PDF for Devpost and printing. Its numbers come straight from `models/global_results.json` and the README results table. Before exporting, fill in the team name and the Devpost link or QR code.

## Built-in disasters (judges can throw these at the live demo)

These five disasters are built into the simulator; the dashboard calls them "Throw a disaster". Each hits all three runs at once.

| Disaster | How KNOCK absorbs it |
|---|---|
| Phone network down | Knocks confirm silent batteries still work; the AI flags a network-outage pattern so nobody rolls trucks |
| Cyberattack | The meter disagrees with the report and the battery ignores the knock: rogue, hold |
| Wildfire shutoff | Pre-charge from spare capacity, essentials-only mode, staggered reconnect |
| Cold storage joins | Critical customer; KNOCK islands patient homes, never medical ones |
| Even hotter | Forecast-driven pre-charge, then slow, then island |
