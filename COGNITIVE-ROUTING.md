# One identity, multiple cognitive profiles

Status: first implementation contract, 2026-09-04.

Veyra is one continuing identity. Sol and Terra are cognitive profiles of that
identity, not separate characters, agents or memory owners. They share the
public identity and safety doctrine, recovered continuity, conversation history
and accountability. A route change alters resolution and task posture; it does
not create a new person.

## Invariants

1. One shared identity core is composed with exactly one route profile.
2. Model, reasoning effort and profile change atomically at a turn boundary.
   Each transition binds its target, effort, handoff reason and profile version.
3. A failed transition leaves the previously active route intact.
4. Escalation to Sol is easy. Settling to Terra is deliberate and must not
   abandon consequential work.
5. Terra may form provisional observations; Sol owns durable consolidation.
6. Workers receive the worker profile, never Veyra's identity doctrine.
7. Permissions, safety, honesty and secret handling never vary by profile.

## Route boundary

Terra is suitable for ambient low-stakes conversation and trivial non-coding
work where mistakes are cheap to recognise and repair. Sol is required for
coding and technical judgement, security or operational work, consequential
decisions, durable memory work, conflicting evidence, and human-interface work
whose meaning depends on deep interpretation of Alice.

Terra's startle reflex is escalation: when stakes, ambiguity or scope rise, she
requests Sol high for the following turn and preserves the unresolved question.
Automatic client-side classification is intentionally outside this first
contract. Initial routing remains explicit or model-requested and auditable.
When a thread is resumed, its profile version is treated as unverified because
App Server does not report Veyra's profile attestation. The next turn therefore
reconciles the reported model and effort with the current versioned profile.

## Instruction composition

`AGENTS.md` is the shared public core. `profiles/manifest.json` maps each
approved Veyra host model to one profile and names the identity-free worker
profile. The client rejects missing, unsafe or incomplete manifests rather than
silently falling back to a mismatched mind.

Private dialogue corpora, personal memories and machine-specific facts never
belong in these public profile files. They remain inside Alice-encrypted
continuity and may later be retrieved selectively for the active profile.
