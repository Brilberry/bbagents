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
| Chapter 9: memory | `memory`, `governed_memory`, `revisions`, `provenance`, `persistence`: scoped access, versioned records, retrieval, checkpoints, history and rollback. Domain consolidation remains application-defined. |
| Chapter 10: runtime learning | `learning` and `promotion`: generalization, validation, versioned promotion evidence, activation history and rollback. Applications supply evaluation and promotion authority. |
| Chapter 11: organizations | `organization`, `safety`, `intervention`: typed roles, messages, governance and delegated approval; transport and scheduling remain adapters. |
| Chapter 12: optimization | `models`, `optimization`, `context`: selection constraints, caching, context budget. Distributed scheduling and speculative execution remain outside the library. |
| Chapter 13: safety | Capabilities, intervention fingerprints/expiry, provenance, trace redaction. Skills remain guidance and do not grant tool authority. Authentication, tenant isolation, OS restrictions, and configuration review must be supplied by the host. |
| Chapter 14: languages | Discussion and illustrative syntax; no language implementation is implied. |

## Remaining implementation boundaries

The seven implementation phases are complete; see [IMPLEMENTATION.md](IMPLEMENTATION.md).
The following deployment responsibilities and reference-backend limitations remain:

- AsyncActionRuntime enforces cooperative deadlines; SubprocessAdapter kills and
  reaps direct child processes. The legacy synchronous dispatcher does not enforce
  deadlines. External side effects and descendants require application controls;
  timeouts produce uncertain outcomes that must be reconciled before replay.
- ExecutionJournal and RecoverableActionRuntime now persist intent and confirmed
  results, bind action/state fingerprints, protect live claims, and recheck current
  authorization. Reconciler implementations establish external status; an unknown
  result blocks replay, and no exactly-once guarantee is inferred from a journal.
- TaskLifecycle now persists task progress, acceptance evidence and measured budgets.
  HookRunner provides bounded traced boundaries; application/orchestrator code owns
  scheduling, budget measurement and recovery against the actual environment.
- FilesystemSkillSource parses reviewed SKILL.md files with optional PyYAML, verifies
  scope, digest and path containment, and authorizes resources separately. Hosts
  supply trusted manifests and identity; scripts remain subject to execution policy.
- Dataset trials now have isolated fixture lifecycle, held-out selection, configuration
  checks, calibration reports and empirical variability. Wilson intervals require
  an explicit independence assumption. Hosts still implement environment isolation
  and supply representative datasets, human labels and domain graders. Unknown
  grades count as trials without demonstrated success in the empirical rate.
- ScopedMemoryStore and RevisionRepository enforce host-defined access before
  ranking and retain revision/activation history. ProcedurePromoter records validation
  and rollback. Provenance labels still require trustworthy host assignment;
  source records alone cannot prove summary fidelity.
- SQLiteStateStore now supplies transactional version checks across competing
  writers. FileStateStore uses unique temporary files and failure cleanup, but
  remains a single-writer reference backend.

These boundaries apply to both the release-manager and pandemic-response fixture.
The release demo persists approvals/state and reconciles its simulated index.
Release-note generation is deterministic by default; `--model` opts into an installed
local Ollama model. The adapter is tested with an offline transport; a live model
service and real package-index publication are not required or exercised by CI.
