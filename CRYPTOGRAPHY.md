# Continuity and secret encryption

Veyra Core uses three data tiers and two separate cryptographic identities.
It requires `age`, `age-keygen`, Python 3, `rsync` and `tar`; snapshotting also
uses GitHub CLI to verify that the remote remains public.

## Trust hierarchy

1. Public doctrine remains plaintext in Git. It defines Veyra's identity,
   behaviour, authority boundaries and the encryption procedures themselves.
2. Private continuity is encrypted to Alice's age recipient. Alice keeps the
   corresponding identity behind a passphrase. Veyra can create new encrypted
   snapshots using the public recipient, but recovery requires Alice.
3. Authorised secrets are encrypted to Veyra's independently rotating age
   recipient. The active identity is local to an authorised machine. An
   Alice-encrypted recovery copy is retained in Git.

Authentication credentials are vault items. They are not reused as either
encryption identity; authentication and encryption keys have separate jobs.

## Repository layout

```text
crypto/
  alice-continuity.recipient
  alice-continuity.identity.age
continuity/
  current.tar.age
vault/
  GENERATION
  veyra-vault.recipient
  recovery/current-identity.age
  entries/<opaque-id>/{metadata.age,secret.age}
  retired/<opaque-id>.metadata.age
```

Secret names, purposes and fingerprints live in encrypted metadata. Only
opaque identifiers and cryptographic generation numbers are exposed by the
repository layout.

## Local state

Veyra's active vault identity is stored at:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/veyra-core/vault-identity.txt
```

It must be mode `0600`. It is never committed or copied into ordinary Codex
memory. The repository contains an Alice-encrypted recovery copy instead.

## Bootstrap and recovery

Run `scripts/bootstrap-encryption.sh` once. It creates both age identities,
asks Alice to protect her identity with a passphrase, installs Veyra's local
identity and writes only encrypted private material to the repository.

For the initial migration, `scripts/seal-existing-private-state.sh` encrypts
the previously versioned checkpoints and memory snapshot. It deliberately
does not delete the plaintext sources; decrypt and verify the archive first,
then remove those exact sources as a separately reviewed migration step.
`scripts/verify-continuity.sh` performs that verification with one interactive
Alice password prompt and removes all temporary plaintext when it exits.

On a recovered machine, Alice can use her passphrase-protected identity to
decrypt `vault/recovery/current-identity.age`, then install the recovered
Veyra identity with `scripts/recover-vault-identity.sh`. Private continuity
can be restored with `scripts/unlock-continuity.sh TARGET`.

## Secret operations

```text
scripts/veyra-vault.py list [--all] [--json]
scripts/veyra-vault.py put-file FILE --name NAME --kind KIND \
  --purpose PURPOSE --scope SCOPE --authorisation AUTHORISATION
scripts/veyra-vault.py get-file ID OUTPUT
scripts/veyra-vault.py forget ID --confirm ID
scripts/veyra-vault.py rotate
scripts/veyra-vault.py audit
```

Secret values are never emitted by `list` or `audit`. `get-file` writes an
exact file with mode `0600` and refuses to overwrite it unless `--force` is
supplied.

Forgetting a secret also rotates Veyra's vault identity and re-encrypts every
surviving entry. The old local identity is removed. Old Git artefacts can then
be recovered only through Alice's encrypted continuity boundary, which makes
such recovery a new, explicit grant from Alice.

## Public lineage

The public repository begins with a clean root created only after private
continuity was encrypted and machine-specific plaintext was removed. The
pre-public history remains in a separate private archive and must never be
merged, pushed or otherwise attached to the public repository.
