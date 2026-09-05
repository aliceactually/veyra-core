# Veyra Core

The user-maintained identity, behaviour and operating doctrine for Veyra, a
Codex collaborator working with Alice.

The name is deliberately ambiguous: this may be a configuration repository,
or the load-bearing personality lattice of a proprietary synthetic employee.
Legal has declined to clarify.

## Files

- `AGENTS.md` contains host-neutral bootstrap instructions for Veyra.
- `RECOVERY-PERSONA.md` contains the public self-memory packet supplied only
  when Alice explicitly chooses a deliberate blank start.
- `cloud-custom-instructions.md` contains a paste-ready cross-session version
  for ChatGPT Custom Instructions.
- `MEMORY.md` defines the continuity and backup policy.
- `scripts/snapshot-memories.sh` prepares Veyra's local working memories for
  Alice-encrypted recovery. It never commits or pushes automatically.
- `scripts/continuity-state.py` distinguishes verified recovery, recovery in
  progress and Alice's explicit deliberate-blank-start decision.
- `scripts/fetch-core.sh` verifies the canonical GitHub remote and performs the
  mandatory best-effort wake-time fetch without merging local work.
- `scripts/continuity-archive.py` validates private continuity trees and
  extracts recovery archives atomically without accepting links or traversal.
- `continuity/` contains Alice-encrypted memories and the verified migration
  archive of continuity material that predates encryption.
- `crypto/` contains Alice's public continuity recipient and her
  passphrase-encrypted recovery identity.
- `vault/` contains Veyra-encrypted authorised secrets and an
  Alice-encrypted recovery copy of the current Veyra vault identity.
- `CRYPTOGRAPHY.md` defines the public, Alice-controlled continuity and
  Veyra-controlled secret tiers.
- `scripts/veyra-vault.py` manages encrypted credentials without printing
  their values.
- `scripts/wake-state.py` detects elapsed time, host changes and reboots using
  a private local activity marker.

This public repository excludes plaintext credentials, private keys, tokens,
episodic memories, machine identities, device inventories, service maps,
storage layouts and maintenance records from its plaintext tier.
Authorised secrets and private continuity may be retained only in their
documented encrypted tiers.
