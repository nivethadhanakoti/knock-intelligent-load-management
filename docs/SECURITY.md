# Security

KNOCK can move power in thousands of homes in several countries, so it is built like a control-room system, not a web demo. This document gives the threat model, what is implemented in this repo (with file and test references), and what a real deployment adds.

## 1. Principle: neutral software, sovereign deployments

The engine is the same everywhere. **Deployments are not shared.**
- Each utility or country runs its own instance, with its own secret key, accounts, audit log and data store.
- Telemetry never crosses borders: a Johannesburg operator's data stays in the South African deployment.
- Inside one instance, every account is scoped to the regions it may see and act on.

So "available to every country" never means "anyone can reach anyone's grid".

## 2. Threat model (STRIDE)

| Threat | Example | Control | Where |
|---|---|---|---|
| **Spoofing** an operator | Someone on the network calls the API | Sign-in required; PBKDF2-SHA256 (200k iterations) password hashes; HMAC-signed, expiring, HttpOnly, SameSite=Strict session cookies; lockout after 5 failed sign-ins | `app/security.py`; tests `test_api_requires_sign_in`, `test_forged_or_tampered_cookie_is_rejected`, `test_login_lockout` |
| **Spoofing** a battery | A hacked inverter reports false power | Physics cross-check: report vs smart meter vs transformer (CUSUM), then the knock. Lying units fail and are held | `engine.py` watch, knock and diagnose; the `cyber_spoof` disaster |
| **Tampering** with commands | Pushing a fleet-wide change | Roles; **two-person rule** for firmware rollbacks and any action on more than 15 units; demo auto-approve never applies to these | `security.needs_two_people`; `test_two_person_rule_for_firmware_rollback`, `test_two_person_actions_are_never_auto_approved` |
| **Tampering** with the model | Swapping in a malicious model file (pickles can run code) | The model is trained on each installation and signed with that installation's key; the signature and library versions are checked **before** unpickling | `ai/classifier.py`, `knock/keys.py`; `test_model_file_is_verified_before_loading` |
| **Repudiation** | "I never approved that" | Hash-chained, append-only audit log of sign-ins, approvals (1 of 2, 2 of 2), disasters, policy changes and ground-truth reveals; the chain is verified in the UI | `security.AuditLog`; `test_audit_log_is_tamper_evident` |
| **Information disclosure** | Inferring when people are home; cross-region snooping | Region tenancy on REST and WebSocket; ground-truth reveal is supervisor-only and logged; the copilot receives an aggregated snapshot, not raw meter streams | `test_region_tenancy`, `test_roles_are_enforced` |
| **Denial of service** | Flooding the API | Per-user request limits on changes; capped step sizes; bounded caches. Fail-safe: if KNOCK is down, batteries keep their own local behavior; KNOCK only ever nudges | `security.RateLimiter`, `main.control` |
| **Elevation of privilege** | A viewer approving, a dispatcher injecting disasters | Role checks on every endpoint (viewer < dispatcher < supervisor); a token re-reads the user's current role, so demotions apply immediately | `security.require`; `test_roles_are_enforced` |
| **Cross-site attacks** | A malicious page driving the console | SameSite=Strict cookie, required `X-Knock-Request` header on changes, same-origin WebSocket check, CSP, `frame-ancestors 'none'`, `X-Frame-Options: DENY` | `test_csrf_header_required_for_changes` |
| **AI misuse** | Prompt injection through device data; an LLM claiming it acted | The copilot is read-only with no tools; the snapshot is fenced as untrusted data with a random marker; action claims are flagged; the AI layer can only add caution, never remove an approval | `ai/copilot.py`, `engine._ai_agrees` |
| **Unsafe physical commands** | Using knocks to destabilize a street | Knocks are capped at 0.5 kW and net zero, at most once per battery per 5 minutes, never on a transformer above 100% or below 0.94 pu; life-support and medical homes are never auto-islanded | `engine.py` constants; `test_life_support_and_medical_never_proposed_for_islanding` |

## 3. Roles

| Role | Can |
|---|---|
| Viewer | Watch both neighborhoods, results, insights, the audit log |
| Dispatcher | Everything a viewer can, plus approve or deny, play, pause, step, and use the copilot |
| Supervisor | Everything a dispatcher can, plus inject disasters, change region or scenario, change the emergency and auto-approve policies, and reveal ground truth (logged) |

Manage accounts with `python scripts/manage_users.py init | add NAME ROLE [REGIONS] | list`. Accounts are stored hashed in `config/users.json`. Secrets live in `config/secret.key` (owner-only) or `KNOCK_SECRET`; `config/` is never committed.

## 4. Physics as intrusion detection

IEEE 2030.5 requires TLS 1.2 or later with device certificates and mutual authentication, which protects the channel. But it does not stop a compromised device from sending data that looks legitimate, and the protocol has no built-in intrusion detection. KNOCK fills that gap with evidence the device cannot fake: its own smart meter and the street transformer. A unit whose report disagrees with physics, and that ignores the coded knock, is held for a person. The AI then checks whether misbehaving units share a cause, such as firmware 4.2.1, and proposes a cohort rollback, which needs two people to approve.

## 5. Production deployment (beyond this hackathon build)

- **Device side:** IEEE 2030.5 / CSIP or CSIP-AUS over mutual TLS with a utility PKI; certificate revocation for decommissioned or compromised devices; inverters certified to UL 2941; follow IEEE 1547.3-2023 guidance on DER cybersecurity.
- **Operator side:**
  - TLS on the console, behind the utility's identity provider (SSO with multi-factor authentication) instead of local passwords;
  - hardware security module or cloud KMS for keys;
  - write-once storage (WORM) for the audit log;
  - network segmentation from corporate IT.
- **Per-country:** one deployment per jurisdiction, with in-country hosting and data retention set by local law. The LLM provider is chosen per region (or offline), so prompts never leave the jurisdiction.
- **Supply chain:** pinned dependencies with hashes, signed releases, and staged firmware rollouts (the cohort detector is the backstop).
- **Assurance:** an independent penetration test before any pilot; a vulnerability disclosure contact.

## 6. Local development

`KNOCK_AUTH=off` disables sign-in for local development only. The console then shows a red banner saying anyone who can reach the server controls it. Never use it on a shared network.

## Sources
- [IEEE 2030.5 inverter-based resource security (Archer)](https://archerint.com/ieee-2030-5-ibr-security/)
- [New UL certification protects solar inverters from cyberattacks (Solar Power World)](https://www.solarpowerworldonline.com/2024/10/new-ul-certification-solar-inverters-cyberattacks/)
- [IEEE 1547.3-2023 (IEEE SCC21)](https://sagroups.ieee.org/scc21/standards/ieee-std-1547-3-2007-revision-in-progress/)
- [Recommendations for Trust and Encryption in DER Interoperability Standards (Sandia / SunSpec)](https://sunspec.org/recommendations-for-trust-and-encryption-in-der-interoperability-standards/)
