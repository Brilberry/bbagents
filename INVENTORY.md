# Manuscript snippet inventory

The current manuscript contains 102 Python code fences (audited 2026-10-21).
Chapter-local sketches are adapted into portable contracts rather than copied
verbatim. See [MANUSCRIPT_ALIGNMENT.md](MANUSCRIPT_ALIGNMENT.md) for the current
coverage and explicit production limitations.

| Chapter | Fences | Library disposition |
|---|---:|---|
| 2 - Models | 2 | `core`, `decision`; provider-neutral proposal boundary |
| 3 - Retrieval | 2 | `retrieval`, `context`; progressive loading and summary provenance |
| 4 — Tools | 20 | `tools`; vendor-specific calling loops omitted |
| 5 — Observability | 6 | `observability`, `evaluation`; optional Langfuse and LangSmith trace adapters included |
| 6 — Multi-agent systems | 8 | `core`, `models`, `organization`; application-owned coordination |
| 7 — Planning | 4 | `planning`; provider prompts and LangGraph wiring remain application code |
| 8 — Harnesses and runtimes | 16 | `runtime`, `safety`, `skills`; application harness composes contracts |
| 9 — Memory | 14 | `memory`; external vector and embedding providers represented by injection points |
| 10 — Learning | 8 | `learning`; model-driven generalization represented by an injected callable |
| 11 — Organizations | 6 | `organization`; domain-specific department examples omitted |
| 12 — Optimization | 7 | `models`, `optimization`; speculative/distributed schedulers omitted |
| 13 — Safety | 9 | `safety`; OS sandbox implementation and incident integrations omitted |
| 14 — Languages | 0 | Architectural discussion; no language runtime |

## Reconciled definitions

- Chapter-local `Observation` definitions became `core.Observation`; memory safety uses
  the separate and more specific `memory.MemoryRecord`.
- Tool calls and registries from Chapters 4 and 7 became `tools.ToolCall` and
  `tools.ToolRegistry`.
- The several `Action` and `Runtime` sketches in Chapter 8 became `runtime.Action`,
  `runtime.ActionResult`, and `runtime.ActionRuntime`.
- The abstract and concrete memory-manager sketches became `memory.MemoryStore`, a
  deliberately small reference backend with replaceable embedding behavior.
- Repeated `Generalizer`, `Validator`, and `LearningRuntime` sketches became generic
  callables plus `learning.LearningRuntime`.
- Context-window and stateful-execution concepts are represented by `context`:
  applications project their own state while the library applies a budget and an
  optional injected summarizer.
- The language-model decision boundary is represented by `decision`: a provider
  adapter receives a typed task and bounded context, while validation and tracing
  remain deterministic runtime behavior.
- Chapter 6 heterogeneous specialization is represented by `models`: organizational
  agent profiles constrain model identity, while application policies select from
  those models and can require reviewer independence.
- Runtime and safety concepts also include `intervention`: applications supply an
  authenticated human interface and identity resolver while the library preserves
  portable requests, responses, delegation, and pending action state.
- Chapter 3 retrieval concepts are represented by `retrieval.Retriever` and
  `RetrievalAugmenter`, which normalize evidence and pass it through the shared
  context budget without selecting a document store or embedding provider.
- Durable execution boundaries are represented by `persistence.StateStore` and its
  versioned `StateSnapshot`; production encodings and backends remain application
  adapters.
- Policy decisions have a generic, contextual `safety.Policy` protocol; the existing
  capability policy remains the action-runtime implementation.
- Trace events carry portable run and span correlation identifiers without coupling
  the package to a trace backend.
- Chapter 11 and Chapter 13 both define `Policy`; these were separated into
  `organization.GovernancePolicy` and `safety.CapabilityPolicy` because they enforce
  different boundaries.

## Deliberate omissions

The seven production-contract phases now add `execution`, `recovery`, `hooks`,
`lifecycle`, `revisions`, `governed_memory`, `promotion`, `experiments`, and
`compaction`, plus filesystem-skill and local Ollama adapters. The implementation
and migration details are in [IMPLEMENTATION.md](IMPLEMENTATION.md). These are
portable adaptations, not new chapter-local snippets or a separate orchestrator.

The following snippets are educational integration examples rather than portable
library code: OpenAI/Anthropic/LangGraph calls, shell and repository mutation, browser
and computer use, deployment commands, concrete vector databases, distributed
coordination, and chapter-specific case studies. Including them would introduce
vendor coupling, unsafe defaults, or false production guarantees. The package exposes
interfaces where applications can add those integrations explicitly.
