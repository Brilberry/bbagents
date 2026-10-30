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

## Behavior selection

`bbagents.behavior` selects which controller proposes the next `ToolCall`.
It does not authorize or execute that call, complete a `PlanStep`, or replace an
orchestration runtime. Policies conform structurally to `BehaviorPolicy[ContextT]`:
implement `propose(context)` returning `Proposal(action, reason)` or
`Abstain(reason)`. Context is application-defined; no inheritance is required.

```python
from bbagents import ToolCall
from bbagents.behavior import Abstain, PolicyRule, PolicySelector, Proposal

class ReviewController:
    def propose(self, context: bool) -> Proposal | Abstain:
        if not context:
            return Abstain("No review requested")
        return Proposal(ToolCall("inspect"), "Review requested")

selector = PolicySelector([
    PolicyRule("review", ReviewController(), priority=20),
])
selection = selector.select(True)
assert selection.policy == "review"
```

Rules are snapshotted at construction, sorted by descending integer priority,
and evaluated in registration order for ties. Negative priorities are valid.
Names must be unique, nonblank strings of at most 128 characters. Construction
never calls a policy. First proposal wins; lower controllers are not evaluated.
`Selection.considered` contains only evaluated attempts in execution order.
Empty or all-abstaining selectors return `policy=None`, `action=None`, and reason
`"No policy proposed an action"`. The application handles this explicitly; there
is no implicit fallback or action cache. Construct a new selector to change rules.

By default, an evaluation exception raises `PolicySelectionError` with the policy
name, error class, and evaluated attempts; its `__cause__` retains the original
exception for authorized callers. `error_mode="skip"` explicitly permits later
controllers to run after ordinary evaluation errors, retaining failed attempts.
Choose it deliberately: skipping a broken emergency controller may be unsafe.
Invalid returned values (including `None`, awaitables, non-`ToolCall` proposals,
and non-string reasons) always raise `TypeError`, even in skip mode. Returned
values are validated at the selection boundary. Signals such as `KeyboardInterrupt`
and trace-sink failures propagate. No evaluation is automatically retried.

Tracing uses the existing `TraceSink`: `behavior.policy_evaluated` precedes a
terminal `behavior.selected` or `behavior.no_match`. Raised evaluation/contract
errors emit only the evaluated error event. Registration indices are zero-based
original input positions, not sorted positions. Default payloads contain policy
names, priorities, outcomes, error classes, and selected tool names, never context,
arguments, exception text, or reason strings. `trace_reasons=True` opts into reason
tracing; reasons may contain secrets. Select trusted policy/tool names and use a
trace redactor (for example `KeyRedactor(frozenset({"reason"}))`) when needed.
Returned selections still carry policy reasons; handle them as application data.

Policies should be fast, synchronous, nonblocking inspections of available state.
An available-model-plan controller can read a previously validated plan; model
calls, inference admission, cooldowns, and lifecycle updates stay outside
`select()`. This is trusted in-process Python, not enforced purity or a sandbox.
The same context reference reaches each evaluated policy. Values are frozen but
only shallowly immutable: applications own snapshot stability and mutable objects
inside `ToolCall.arguments`. No context or arguments are copied or mutated by the
selector; thread safety of stateful policies remains application-owned.

Applications execute selected calls through their existing tool or actor-bound
action path and update plan evidence only after handling the result. Tool
preconditions and authorization still apply. A denial is not an instruction to
select a lower-priority fallback automatically. Selection also does not cancel,
suspend, or erase another controller's work. See the complete offline
[`behavior_policy_selection.py`](examples/behavior_policy_selection.py) example.

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

The [implementation notes](IMPLEMENTATION.md) describe production contracts and
migration from the original synchronous reference runtimes. New modules cover
transactional persistence, bounded execution, effect recovery, task lifecycle,
governed memory/skills/procedure promotion, and isolated evaluation experiments.
The root API stays small; import these features from their responsibility modules.

- Standard library only at runtime.
- Default-deny capability policy when safety controls are used.
- Deterministic code owns loops, retries, policy checks, and state transitions.
- Probabilistic model behavior enters through narrow injected interfaces.
- No hidden global registries or network calls.
- No orchestration, checkpointing, or hosted tracing runtime: applications may use
  LangGraph, Langfuse, LangSmith, or another platform directly. The default tracing
  example runs locally and exports to Langfuse when configured; optional Langfuse
  and LangSmith adapters translate planning events into nested hosted traces.

## Native behavior trees

`bbagents.behavior_trees` provides execution separately from the proposal-only
`PolicySelector`. See [the offline example](examples/native_behavior_tree.py).
No existing policy API or root imports change.

Definitions use `Action`, `Condition`, `Selector`, `Sequence`, `Inverter`, and
`Branch`. Every node has a unique stable ID. Leaves reference application-bound
operation or predicate names. `BehaviorTree.to_data()` and `from_data()` serialize
only the definition; they never load Python code. Definitions are bounded to
1,024 nodes and 64 levels. Keep bindings stable for the lifetime of a tree.

- Actions return exactly `Status.SUCCESS`, `FAILURE`, or `RUNNING`. Conditions
  return exactly bool. Invalid returns are errors, not failed actions.
- Sequence proceeds on success; Selector proceeds on failure. Both remember their
  running child. Inverter swaps success/failure and preserves running.
- Branch evaluates its predicate once per traversal, pins the YES/NO arm while
  running, and returns that arm's outcome unchanged. A failed YES action never
  executes NO. Use this primitive when compiling player-facing decision trees;
  an ordinary Selector of guarded Sequences does not provide that guarantee.
- Terminal traversals reset progress. A subsequent tick starts another traversal.
  Multiple instantaneous leaves can execute in one tick; an adapter must return
  RUNNING if its work spans intervals. This is not an implicit one-tool-per-tick
  budget or an asynchronous worker scheduler.

Call `tree.start()` once per actor. `Execution.tick(context)` advances only that
actor's state. Action handlers receive a private JSON-data memory dictionary;
shared handler instances must keep actor progress there, rather than on `self`.
Separate executions are isolated only if application adapters also respect that
contract. Concurrent/reentrant operations on the same execution are unsupported;
reentrant calls fail, but this is not thread synchronization or a sandbox.

The caller owns event detection and scheduling. These composites are memoryful,
not reactive: when a higher-priority event arrives or a branch becomes invalid,
call `execution.cancel(fresh_context)` before selecting new work. Cancellation
invokes the running action's cleanup and clears tree progress. It does not undo
world effects or promise resumable external jobs. Domain projects can retain their
own completed work and validate it when a later action restarts. Cleanup callbacks
must tolerate retries after failure. Failed cleanup blocks subsequent ticks.

`Execution.snapshot()` returns detached JSON data, including cursors, pinned
branches, action memory and a definition fingerprint. Restore with
`Execution.restore(tree, data)`; inconsistent active paths or different tree
schemas are rejected. Callbacks/context are never serialized. The fingerprint
identifies definition data, not callback code or external world state: applications
must pin adapter versions, persist world state consistently, authenticate incoming
checkpoints and reconcile outstanding effects. This is not exactly-once execution.

Callback errors fail closed and chain the original exception. Cancel successfully
before ticking again. Trace sink errors also propagate and fault execution rather
than silently retrying side effects. Terminal actions are not cancelled again.
`BaseException` signals propagate. Snapshots of busy/faulted executions are refused.

Structured `behavior_tree.*` events identify tree fingerprint, explicit tick step,
node entry/results, condition bool, selected branch, cancellation and error class.
They omit context, memory, exception text and tool arguments. Applications should
use stable node IDs to highlight their authoring view and record authorized action
results, energy expenditure and observed entities separately. Trace sinks are
shared bindings; supply actor/run correlation with the existing trace adapters.
No clocks or randomness are consumed by the tree engine itself.

Operation callbacks are trusted code and must use the application's existing
`ToolRegistry` / `ActionRuntime` path, including authorization and fresh domain
preconditions. Returning SUCCESS is the adapter's report, not proof of execution
or permission. Energy, action duration, event registries, scheduler priorities,
hysteresis and player-facing decision-tree compilation remain application or
subsequent integration concerns; this module supplies their execution foundation.
