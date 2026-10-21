# BrilBerry Agents (`bbagents`)

BrilBerry Agents is a provider-neutral reference toolkit for modeling and enforcing
the plans, policies, memory, learning, and organizational structure of agent systems.
It can run independently or complement orchestration frameworks such as LangGraph
and integration frameworks such as LangChain.

The package turns the conceptual Python examples from *Agent Engineering* into
small, typed, executable components. It deliberately has no model-provider or
storage dependency.

The package is an alpha reference implementation. Its stable boundary is the set of
typed values and protocols; production applications should supply their own model,
storage, sandbox, and telemetry adapters.

## Install for development

```console
python -m pip install -e '.[dev]'
pytest
```

Python 3.11 or newer is required.

## Where it fits

`bbagents` is not intended to replace LangChain or LangGraph. LangChain supplies a
broad ecosystem of model, tool, retriever, and provider integrations. LangGraph
supplies durable, stateful orchestration, checkpointing, streaming, and
human-in-the-loop execution. `bbagents` supplies the application-level plans,
policies, bounded context, versioned state, memory records, learning procedures,
roles, and governance rules that can run inside those systems.

These primitives can be composed into an application-specific **agent harness**:
the layer that assembles context, constrains actions, applies policy, and captures
feedback around a model. `bbagents` does not impose a universal harness or own the
outer execution loop; applications and orchestration frameworks retain that role.

The [manuscript alignment audit](MANUSCRIPT_ALIGNMENT.md) maps the current book to
implemented contracts and records production concerns still owned by applications.

For small deterministic workflows, the package can run by itself. In a larger
application, LangGraph can own the outer execution loop while graph nodes use
`bbagents` primitives. LangChain models and tools can be connected through narrow
adapters without making them required dependencies.

## Minimal example

```python
from bbagents import AgentState, FunctionTool, Observation, Plan, PlanStep, ToolCall, ToolRegistry
from bbagents.observability import InMemoryTrace
from bbagents.reference import PlanningAgent

tools = ToolRegistry([
    FunctionTool(
        "inspect",
        "Inspect a named component",
        lambda component: Observation("inspect", f"inspected {component}"),
    )
])

state = AgentState("Inspect authentication", Plan(
    "Inspect authentication",
    [PlanStep("inspect", "Inspect authentication")],
))

agent = PlanningAgent(
    tools,
    lambda state, step: [ToolCall("inspect", {"component": "authentication"})],
    trace=InMemoryTrace(),
)
agent.run(state)
assert state.completed
```

The root namespace is intentionally limited to the common planning, action, tool,
and decision contracts. Import specialised components from their module, such as
`bbagents.intervention`, `bbagents.models`, `bbagents.safety`, or
`bbagents.observability`.

## Examples

Runnable examples include:

- [`langgraph_integration.py`](examples/langgraph_integration.py), which lets
  LangGraph own orchestration and checkpointing while graph nodes use `bbagents`;
- [`langgraph_intervention.py`](examples/langgraph_intervention.py), which pauses a
  governed action through LangGraph and resumes it with a typed human response;
- [`coding_organization.py`](examples/coding_organization.py), which coordinates
  persistent roles under ownership, dependency, and governance rules;
- [`dependency_vulnerability_audit.py`](examples/dependency_vulnerability_audit.py),
  which turns repository and advisory evidence into verified remediation steps;
- [`model_routing_and_cache.py`](examples/model_routing_and_cache.py), which routes
  workloads under quality and latency constraints and caches stable results;
- [`secure_deployment.py`](examples/secure_deployment.py), which separates action
  proposals, capabilities, approval, and side effects;
- [`repository_constraint_learning.py`](examples/repository_constraint_learning.py),
  which separates retained experience from validated behavioral change;
- [`oauth_migration_planner.py`](examples/oauth_migration_planner.py), which
  demonstrates dependent planning, evidence accumulation, and tracing;
- [`langchain_integration.py`](examples/langchain_integration.py), which adapts
  LangChain models and tools without transferring ownership of the agent loop.
- [`langfuse_tracing.py`](examples/langfuse_tracing.py), which runs locally by
  default and publishes nested traces to Langfuse when configured;
- [`langsmith_tracing.py`](examples/langsmith_tracing.py), which publishes the
  same trace hierarchy to LangSmith as an alternative integration.

The [`examples/README.md`](examples/README.md) explains the goals and architectural
boundaries of each example.

## Modules and manuscript provenance

| Module | Adapted concepts |
|---|---|
| `core` | Provider-neutral models and observations (Chapters 6–8) |
| `tools` | Tool schemas, registries, and deterministic invocation (Chapter 4) |
| `planning` | Explicit plans, dependencies, execution, and traces (Chapter 7) |
| `runtime` | First-class actions, policy checks, results, and retries (Chapter 8) |
| `decision` | Structured model proposals with bounded context, validation, and traces (Chapters 2, 5, 7) |
| `skills` | Versioned authored guidance, progressive discovery/loading, and provenance (Chapters 3, 8, 13) |
| `evaluation` | Separate response, transcript, and environment evidence; graders and repeated-trial reports (Chapter 5) |
| `models` | Role-scoped heterogeneous model selection and independent review (Chapters 6, 12) |
| `intervention` | Human-intervention requests, explicit delegation, and action gating (Chapters 8, 13) |
| `context` | Budgeted context views, state projection, and summarization contracts (Chapters 2, 3, 9) |
| `retrieval` | Provider-neutral evidence retrieval and context augmentation (Chapter 3) |
| `memory` | Episodic/semantic/procedural records and retrieval (Chapter 9) |
| `learning` | Experience, generalization, validation, and promotion (Chapter 10) |
| `organization` | Roles, messages, delegation, and governance (Chapters 6, 11) |
| `optimization` | Model cost estimates, routing constraints, and caching (Chapter 12) |
| `safety` | Trust, capabilities, approvals, and audit decisions (Chapter 13) |
| `observability` | Structured traces and evaluations (Chapter 5) |
| `persistence` | Versioned state snapshots and optimistic concurrency contracts (Chapters 8-11) |

## Adaptation policy

The manuscript contains more than 120 Python fences. Many are pseudocode fragments,
provider examples, alternative versions of the same class, or deliberately incomplete
interfaces. The library does not concatenate them. It makes these adaptations:

- Repeated names such as `Action`, `Runtime`, `MemoryManager`, and `Policy` are merged
  into one responsibility-specific implementation.
- Values shared between chapters use a single canonical definition.
- Model-generated planning is represented by injected callables. This keeps model
  providers optional and leaves the deterministic loop testable.
- Model calls can use `DecisionRuntime`: applications adapt their provider to the
  `LanguageModel` or `DecisionModel` protocol while the library applies explicit
  output schemas, bounded context, validation, retries, and traces. Provider
  adapters may return model identity, token usage, and request metadata.
- `ModelCatalog` and `ModelSelector` make heterogeneous routing explicit. The
  optimization module retains a small compatibility wrapper but does not own a
  second routing policy.
- Agent profiles name their permitted model identities. Applications retain control
  of the selection policy, and can exclude a generator's model when assigning an
  independent reviewer.
- Authored skills use `SkillDescriptor`, `Skill`, and the application-implemented
  `SkillSource` protocol. Discovery can expose descriptions before loading an exact
  version. Resource references remain opaque and required tools confer no authority.
- Evaluation uses `TrialEvidence` to keep answers, transcripts, and environment
  outcomes separate. Applications provide graders and isolated trial execution;
  `report_trials` distinguishes observed any-success and all-success counts without
  assuming independent runs. Missing evidence is distinct from failure or success.
- Context summaries retain their original `summary_sources` for provenance and
  review. Only `Context.content` is rendered to the model by the decision adapter.
- Storage examples include in-memory state and an optional JSON file-backed state
  store. Corrupt or incompatible state files are treated as absent checkpoints;
  vector databases, embeddings, and production stores remain adapters.
- Context selection, state persistence, and policy evaluation have explicit typed
  contracts. Applications retain ownership of domain state, storage encoding,
  summaries, and policy semantics.
- Human intervention is represented as typed, expiring requests, versioned
  responses, explicit delegation, and action-runtime pending states. Identity
  verification, UI, notification, and workflow suspension remain application
  adapters.
- State, intervention, and memory records use stable identity and optimistic
  concurrency. Applications retain ownership of database choice and encoding.
- Trace events carry portable run/span context and can be redacted before delivery
  to in-memory or external tracing backends. Langfuse and LangSmith remain optional
  adapters rather than framework dependencies.
- `Provenance` labels observations, memory, retrieved evidence, context, and
  intervention requests with a source identity and trust level. Applications decide
  how those labels affect their policies.
- The optional `bbagents[langgraph]` integration maps a stored intervention request
  to LangGraph's durable interrupt and resume flow without requiring applications to
  encode or validate intervention payloads themselves.
- Retrieval is a narrow evidence-to-context boundary. Applications supply document
  loading, chunking, embeddings, vector stores, rerankers, and orchestration.
- Browser, computer-use, shell, filesystem, LangGraph, and vendor SDK examples are
  omitted because they require external runtimes or create unsafe defaults.
- Prompt strings and chapter-specific OAuth/coding-agent examples remain examples,
  not library behavior.
- Optimization and organizational data models are included, while speculative
  scheduling and distributed execution are left to application runtimes.

## Design constraints

- Standard library only at runtime.
- Default-deny capability policy when safety controls are used.
- Deterministic code owns loops, retries, policy checks, and state transitions.
- Probabilistic model behavior enters through narrow injected interfaces.
- No hidden global registries or network calls.
- No orchestration, checkpointing, or hosted tracing runtime: applications may use
  LangGraph, Langfuse, LangSmith, or another platform directly. The default tracing
  example runs locally and exports to Langfuse when configured; optional Langfuse
  and LangSmith adapters translate planning events into nested hosted traces.
