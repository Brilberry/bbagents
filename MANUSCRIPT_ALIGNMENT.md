# Manuscript alignment audit

Audited against the local manuscript on 2026-10-21, including the harness, skills,
and evaluation revision in `c7c1bd8`. Subsequent chapter changes through `2f8c240`
predominantly concern figure formats and print presentation. Chapter-local Python
sketches illustrate architecture; the package does not promise identical classes
or production implementations of every sketch.

| Book concepts | Library support and disposition |
|---|---|
| Chapters 1-2: model/runtime separation | `core`, `decision`, `runtime`: typed proposals, schema validation, policy-controlled effects. |
| Chapter 3: retrieval, progressive loading, compaction | `retrieval`, `context`, `skills`: bounded assembly, attributable evidence, lazy skill-source protocol. Summaries retain source items separately from model-facing text. |
| Chapter 4: tools and protocols | `tools`: injectable deterministic invocation; provider APIs, MCP connections, and code-execution sandboxes remain adapters. |
| Chapter 5: observable execution and evaluation | `observability` plus optional tracing adapters; `evaluation` records configuration, response, transcript, actual outcome, duration, and cost. Graders can return insufficient evidence. Repeated-trial reporting separates any success from all successes. |
| Chapter 6: heterogeneous specialization | `models` catalog/selector, role-scoped candidates, independent reviewer exclusion; application owns coordination. |
| Chapter 7: explicit plans | `planning`: dependencies, cycle rejection, evidence, one-step executor; `reference` supplies the local synchronous runner. |
| Chapter 8: harness composition and skills | Existing primitives compose into application harnesses. `skills` adds version, owner, scope, provenance, resource references, and discovery/loading contracts. No universal harness or second graph scheduler is added. |
| Chapter 9: memory | `memory`, `provenance`, `persistence`: versioned records, retrieval, checkpoints, trust labels. Production scoping, access enforcement, audit history, consolidation, and rollback remain application responsibilities. |
| Chapter 10: runtime learning | `learning`: injected generalization and validation, idempotent evidence assignment. Procedures carry version, owner, and scope. Promotion history, rollback, and evaluation-driven deployment are not implemented by the local runtime. |
| Chapter 11: organizations | `organization`, `safety`, `intervention`: typed roles, messages, governance and delegated approval; transport and scheduling remain adapters. |
| Chapter 12: optimization | `models`, `optimization`, `context`: selection constraints, caching, context budget. Distributed scheduling and speculative execution remain outside the library. |
| Chapter 13: safety | Capabilities, intervention fingerprints/expiry, provenance, trace redaction. Skills remain guidance and do not grant tool authority. Authentication, tenant isolation, OS restrictions, and configuration review must be supplied by the host. |
| Chapter 14: languages | Discussion and illustrative syntax; no language implementation is implied. |

## Remaining implementation boundaries

The library is a reference framework, not the complete production system described
by the book. The following gaps need explicit design before production use:

- AsyncActionRuntime enforces cooperative deadlines; SubprocessAdapter kills and
  reaps direct child processes. The legacy synchronous dispatcher does not enforce
  deadlines. External side effects and descendants require application controls;
  timeouts produce uncertain outcomes that must be reconciled before replay.
- ExecutionJournal and RecoverableActionRuntime now persist intent and confirmed
  results, bind action/state fingerprints, protect live claims, and recheck current
  authorization. Reconciler implementations establish external status; an unknown
  result blocks replay, and no exactly-once guarantee is inferred from a journal.
- Hooks, durable cross-session task lifecycle, completion gates, and no-progress
  budgets are application/orchestrator responsibilities. No library hook engine
  currently provides deadline, failure, and trace semantics.
- `SkillSource` is a contract, not an Agent Skills filesystem parser or permission
  engine. The host verifies scope, revision, owner, and provenance before loading.
- Evaluation records and reports are implemented; dataset execution, environment
  reset, held-out case selection, judge calibration, and statistical confidence
  estimation remain application responsibilities. Unknown grades count as trials
  without demonstrated success in the empirical success rate.
- Summary source records preserve attribution but do not prove summary fidelity or
  enforce trust. Memory access and provenance labels also require host policies.
- SQLiteStateStore now supplies transactional version checks across competing
  writers. FileStateStore uses unique temporary files and failure cleanup, but
  remains a single-writer reference backend.

These boundaries apply to both the release-manager and pandemic-response demos.
The release demo still simulates publication and release-note generation; it does
not demonstrate an actual LLM call or a live package-index release.
