# Production contract implementation

Each phase is tested, committed, and pushed before the next phase starts.

1. Persistence: implemented SQLite transactional checkpoints and failure-safe file
   replacement. FileStateStore remains a single-writer reference backend.
2. Deadlines and retries: implemented cooperative async deadlines, allowlisted
   subprocess cancellation, typed failures and conservative retry policy.
3. Recovery and idempotency: pending.
4. Task lifecycle and enforcement: pending.
5. Skills, memory, and learning governance: pending.
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
