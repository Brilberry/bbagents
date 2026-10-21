# Production contract implementation

Each phase is tested, committed, and pushed before the next phase starts.

1. Persistence: implemented SQLite transactional checkpoints and failure-safe file
   replacement. FileStateStore remains a single-writer reference backend.
2. Deadlines and retries: implemented cooperative async deadlines, allowlisted
   subprocess cancellation, typed failures and conservative retry policy.
3. Recovery and idempotency: implemented durable intent, action/state binding,
   leases, reconciliation, confirmed result reuse, and current-policy checks.
4. Task lifecycle and enforcement: implemented persisted acceptance/progress state,
   evidence completion policy, measured budgets, and bounded traced hooks.
5. Skills, memory, and learning governance: implemented reviewed filesystem skills,
   scope-bound access before ranking, revision history, promotion and rollback.
6. Evaluation execution and compaction checks: pending.
7. Cross-domain demonstrations: pending.

## Migration notes

- Existing StateStore implementations and call sites remain valid. Use
  `SQLiteStateStore` for concurrent local checkpoint updates. JSON codecs work as
  with FileStateStore; database corruption is surfaced rather than reset silently.
- Run `PYTHONPATH=src python examples/durable_checkpoints.py` for a restart and
  stale-writer example.
- Use `execution.AsyncActionRuntime` for bounded execution. Cancellation is
  cooperative for Python handlers and propagates to the caller; subprocess children
  are killed and reaped. Neither guarantees reversal of external effects. Legacy
  synchronous ActionRuntime remains available and does not enforce deadlines.
- `SafeRetryPolicy` retries confirmed transient failures only when `retry_safe`
  is explicitly set. An idempotency key alone does not prove a provider honors it.
- `RecoverableActionRuntime` requires stable execution IDs and JSON-compatible
  action metadata/results. Uncertain outcomes and expired leases require an
  application reconciler. A NOT_EXECUTED answer must rule out a late side effect;
  the journal cannot discover this on its own. It never promises exactly-once.
- TaskLifecycle persists progress but does not schedule work. Hosts measure/report
  time and spend and check budgets before dispatch. Domain completion rules implement
  CompletionPolicy. HookRunner exposes before-model/action/result/completion boundaries;
  AsyncActionRuntime wires action/result hooks and TaskLifecycle wires completion.
  Only observational after-result hooks may fail open. Callbacks must honor cancellation.
- Install `[skills]` for YAML parsing. FilesystemSkillSource accepts a trusted
  digest manifest and policy; discovery is lazy and resources need separate access.
  It never executes scripts. Keep manifests outside agent-writable storage.
- RevisionRepository binds trusted identity/scope and retains candidate/activation
  history. ScopedMemoryStore ranks only authorized scoped items. ProcedurePromoter
  requires validation evidence; rejected proposals never replace the active version.
  Use separate namespaces for memory and procedures. The reference aggregate is
  intended for small local stores, not large distributed corpora.
