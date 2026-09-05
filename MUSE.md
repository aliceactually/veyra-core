# Muse memory consolidation

Muse is an identity-free local worker protocol for preparing memory
consolidation proposals. It is not Veyra, does not receive Veyra's authority,
and cannot directly modify continuity.

The design separates storage growth from prompt growth. Source episodes remain
in encrypted continuity, while a bounded retrieval view supplies only relevant
memory kernels and sufficiently confident facets.

## Memory classes

- Directives are outside the Muse data model and outside every Muse write path.
- Core memories have durable kernels. Their peripheral facets may become less
  accessible as confidence decays, but their kernels have no automatic expiry.
- Semantic memories are durable consolidated knowledge with ordinary recall
  decay.
- Provisional memories are disposable retrieval hints stored only in a local
  cache. They are never continuity state.
- Source episodes remain authoritative evidence. Muse never deletes them.

Durability, recall priority and epistemic confidence are independent. An
important memory can therefore remain core while details around its edges
become less specific.

## Review boundary

Muse emits JSON proposals linked to exact source paths and SHA-256 hashes.
Deterministic validation rejects unknown fields, missing provenance, stale
revisions, path traversal and oversized records.

Terra may approve only provisional operations. Applying a Terra review changes
the disposable cache and cannot write to the durable ledger. Sol may approve
semantic and core operations. Durable application appends a reviewed batch to
the ledger; prior events and source episodes are never rewritten.

Changing an existing core kernel requires a reason in the proposal and Sol
review. Contradictions should normally mark a record `contested` rather than
silently replacing it.

## Local-worker flow

1. `prepare` creates a bounded, identity-free worker job from explicitly named
   source files and the current hot memory view.
2. A local worker receives the job's `worker_prompt` and returns only the
   requested proposal JSON. The existing Veyra Client `run_local_agent` tool is
   suitable for this bounded generation step.
3. `validate` canonicalises the worker output and checks its provenance.
4. `review` records a Terra or Sol decision over an explicit operation scope.
5. `apply` enforces the route boundary and updates either the disposable cache
   or append-only durable ledger.
6. `recall` returns a query-ranked, character-bounded slice. The whole archive
   is never loaded merely because it exists.

Example commands:

```text
scripts/muse-memory.py init --memory-dir DIR --cache-dir CACHE
scripts/muse-memory.py prepare --memory-dir DIR --cache-dir CACHE \
  --source extensions/ad_hoc/notes/example.md
scripts/muse-memory.py validate --memory-dir DIR --cache-dir CACHE \
  --job CACHE/jobs/JOB.json --proposal worker-output.json
scripts/muse-memory.py review --cache-dir CACHE --proposal PROPOSAL \
  --profile terra --decision approve --scope provisional \
  --rationale "Rebuildable retrieval hints only"
scripts/muse-memory.py apply --memory-dir DIR --cache-dir CACHE \
  --proposal PROPOSAL --review REVIEW
scripts/muse-memory.py recall --memory-dir DIR --cache-dir CACHE \
  --query "temporal continuity" --max-characters 4000
```

Jobs, worker responses, reviews and provisional indexes are private local cache
material. Only Sol-approved ledger batches become part of the encrypted memory
snapshot.
