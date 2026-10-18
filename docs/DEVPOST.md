# Devpost text (paste and edit)

**Name:** KNOCK
**Lens:** Energy & Grid
**One line:** Check before you cut the power. KNOCK knocks on silent home batteries with tiny coded power nudges, works out whether they're mute, tripped, broken or rogue, and climbs a gentle ladder instead of cutting the street.

## What it does
When a home battery goes silent, operators assume it's dead and cut the whole street. KNOCK sends a ±0.5 kW nudge in a unique Walsh-code rhythm through the control channels fleets already use (IEEE 2030.5 or a vendor API), then decodes the answer from the transformer meter. A physics-based Bayesian diagnosis picks the gentlest action:
- keep using a mute battery;
- restart a tripped one;
- send one truck to a broken one;
- hold a rogue one, with a person's approval.

An AI layer learns each home's load from its meter, forecasts overloads 45 minutes ahead and pre-charges batteries, double-checks every diagnosis with a trained classifier, and spots bad firmware cohorts before they spread. A copilot explains decisions and drafts homeowner texts; it cannot act.

## Results (simulated, 144 homes, same seed for every run)
- **LA heat wave:** 0 homes dark vs 15 the usual way; 0 medical homes cut off vs 6.
- **Rogue firmware:** 3 batteries went rogue vs 12, because the AI rolled the cohort back.
- **Ordinary Tuesday:** 1 technician trip vs 15, and $485 vs $442 in demand-response revenue.

## Twists
1. **Funding:** NSF SBIR Phase I ($305K) is the primary path; a DoD ESTCP demonstration on a military installation is the dual-use path. Both are built into the dashboard with a live evidence pack and a one-click NSF Project Pitch.
2. **Global:** five region presets, each its own simulation: Los Angeles, Johannesburg (Eskom load reduction), Adelaide (45 °C, state islanded), Chiba (typhoon) and Zhejiang (VPP peak). 0 homes dark with KNOCK in every region, vs 13–26 the usual way.
3. **Poster:** attached.

## Security
- **Sign-in:** required, with viewer, dispatcher and supervisor roles.
- **Region-scoped accounts:** one deployment per country, so grid data stays in-country.
- **Two-person rule:** needed for firmware rollbacks and other fleet-wide actions.
- **Audit log:** hash-chained and tamper-evident, verified live on the Security tab.
- **Signed model:** the model is verified before loading.
- **Intrusion detection:** encrypted protocols like IEEE 2030.5 protect the channel but not against a hacked battery that lies. KNOCK catches liars with physics: their own meter and the transformer disagree, and they ignore the knock.

## How we built it
Python and FastAPI engine; numpy online learning; scikit-learn gradient-boosted classifier; WebSocket dashboard; IBM watsonx.ai (Granite), Anthropic or offline copilot.

**How we built with IBM Bob:** [fill in from docs/BOB_LOG.md]

**AI tools used:** Claude (scaffold, spec, engine, AI layer, twists), IBM Bob ([fill in]).

## Responsible use
KNOCK's commands and data could be abused to manipulate home batteries or infer when people are home. We cap and rate-limit probes, require sign-in with roles and region-scoped accounts, need two people for fleet-wide changes, keep the AI advisory and the copilot read-only, and record every action in a tamper-evident audit log.
