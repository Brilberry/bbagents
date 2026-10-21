# Examples

## Production contracts

All of these examples run offline by default:

- `durable_checkpoints.py`: reopen SQLite state and reject a stale writer.
- `bounded_execution.py`: cancel cooperative work and report an uncertain outcome.
- `recovering_effects.py`: reconcile a completed effect without repeating it.
- `task_completion.py`: persist acceptance evidence and enforce completion.
- `governed_memory.py`: scope isolation, revision history, and rollback.
- `isolated_evaluation.py`: fresh environments for repeated dataset trials.
- `local_model.py`: model adapter and token usage via a fixture; pass `--model NAME`
  to call an installed Ollama model using its [chat API](https://docs.ollama.com/api/chat).

The release and pandemic integration fixtures are documented in [demos](../demos/).
The test suite executes every offline example. Hosted tracing exporters are tested
with fake clients rather than uploading traces during tests.

## LangGraph integration

`langgraph_integration.py` demonstrates the inverse of the LangChain example's
ownership model. LangGraph owns the outer orchestration loop, graph transitions, and
checkpoint lifecycle. Individual graph nodes use `bbagents` plans, dependencies,
tools, observations, step statuses, and traces.

The example stores a checkpoint-safe serialized view of `AgentState` in LangGraph
state and reconstructs the typed `bbagents` values inside each node. It executes
three graph nodes: inspect authentication, inspect sessions, and produce an OAuth
migration strategy. An `InMemorySaver` checkpoints the workflow after graph
transitions. The example uses an in-memory saver for demonstration only; production
applications should select a durable LangGraph checkpointer.

The example demonstrates:

- a LangGraph `StateGraph` as the orchestration owner;
- `bbagents.Plan` and `PlanStep` as the typed domain model inside graph nodes;
- explicit serialization at the framework checkpoint boundary;
- explicit dependency checks within graph nodes;
- `ToolRegistry` execution and `Observation` accumulation;
- independent `bbagents` trace events; and
- thread-scoped LangGraph checkpoints.

### Run it

From the `bbagents` directory:

```console
python -m pip install -e '.[langgraph]'
python examples/langgraph_integration.py
```

## LangGraph human intervention

`langgraph_intervention.py` demonstrates the optional `bbagents[langgraph]`
integration. The action runtime creates a typed intervention request, and its ID is
persisted in LangGraph state before a following node pauses through LangGraph's
`interrupt()` mechanism. When the graph resumes, the adapter validates the response
through `ActionInterventionService` before the original action can execute.

The application only handles its authenticated response payload. The adapter owns
the interrupt payload, response conversion, and intervention resolution. A durable
LangGraph checkpointer and a durable intervention store are required in production.

### Run it

From the `bbagents` directory:

```console
python -m pip install -e '.[langgraph]'
python examples/langgraph_intervention.py
```

## Coding organization

`coding_organization.py` demonstrates why a software-engineering organization is
more than a graph of model calls. Planning, implementation, validation, and
deployment remain persistent roles with different responsibilities and authority.
Tasks move between those roles through typed messages carrying acceptance evidence.

Governance requires independent validation and reserves deployment authority for the
deployment role. The example first proposes an unreviewed release owned by the
implementation role and confirms that policy rejects it. It then executes the valid
workflow in dependency order.

The example demonstrates:

- persistent roles with explicit responsibilities;
- task ownership and dependency boundaries;
- deterministic governance policies;
- rejection of unauthorized deployment work;
- structured evidence passed through `Message` values; and
- accountability that remains stable when individual agents change.

The small `SoftwareOrganization` coordinator belongs to the example rather than the
library. It illustrates how applications can compose the provider-neutral values in
`bbagents.organization` without imposing one universal organizational runtime.

### Run it

From the `bbagents` directory:

```console
python -m pip install -e .
python examples/coding_organization.py
```

## Heterogeneous model organization

`heterogeneous_model_organization.py` demonstrates Chapter 6's heterogeneous
specialization. A release writer selects the cheapest permitted model that satisfies
its requirements. The reviewer explicitly excludes that model identity, forcing an
independent review model while retaining typed context and decision traces.

### Run it

From the `bbagents` directory:

```console
python -m pip install -e .
python examples/heterogeneous_model_organization.py
```

## Dependency vulnerability audit

`dependency_vulnerability_audit.py` implements the vulnerability-remediation
scenario from Chapter 4 as a complete evidence pipeline. The agent inventories the
repository, retrieves advisories for installed packages, compares installed and
fixed versions, and drafts upgrades only for confirmed exposure.

All package names, versions, and advisory identifiers are synthetic. The example is
designed to demonstrate architecture rather than provide real security data.

The example demonstrates:

- a plan whose stages depend on evidence from previous stages;
- deterministic repository and advisory adapters;
- structured metadata passed between tool calls;
- separation between advisory retrieval and exposure assessment;
- remediation derived only from confirmed vulnerable versions; and
- a trace that preserves the complete audit sequence.

### Run it

From the `bbagents` directory:

```console
python -m pip install -e .
python examples/dependency_vulnerability_audit.py
```

## Model routing and cache

`model_routing_and_cache.py` demonstrates optimization as a constrained runtime
decision rather than a preference for one model. Three provider-neutral model
profiles describe cost, latency, and expected quality. A planning request requires
high quality and therefore routes to the deep model. A routine formatting request
has a strict latency budget and routes to the fast model.

The planning request is then repeated with the same stable cache key. Its result is
returned from `Cache` without another model execution or additional estimated cost.

The example demonstrates:

- provider-neutral `ModelProfile` definitions;
- minimum-quality and maximum-latency constraints;
- selection of the cheapest eligible model;
- input/output token cost estimation;
- explicit cache keys and cache-hit reporting; and
- measuring actual executions rather than logical requests.

### Run it

From the `bbagents` directory:

```console
python -m pip install -e .
python examples/model_routing_and_cache.py
```

## Secure deployment

`secure_deployment.py` demonstrates the separation between proposing an action and
possessing authority to execute it. A runtime first performs a read-only repository
inspection. It then receives a production deployment action that has the required
capability but still requires explicit human confirmation.

The first deployment attempt is denied before the deployment handler runs. After an
approval is added, the same action is authorized and executed. Both decisions remain
visible in the capability policy's audit log.

The example demonstrates:

- actions with explicit expected effects and resource metadata;
- default-deny capability checks;
- an approval requirement independent of model reasoning;
- policy enforcement before side effects occur;
- structured success and denial results; and
- an auditable sequence of authorization decisions.

### Run it

From the `bbagents` directory:

```console
python -m pip install -e .
python examples/secure_deployment.py
```

## Repository constraint learning

`repository_constraint_learning.py` demonstrates the distinction between retaining
experience and changing future behavior. The scenario comes from Chapter 10: changes
to authentication handlers repeatedly fail unless the repository's separate
authorization registry is updated as well.

The example records every execution as episodic memory, including a failed change.
The learning runtime generalizes only after two successful executions corroborate
the constraint. A deterministic validator checks that the proposed rule is properly
scoped and names both sides of the dependency. Only then is the rule promoted into
semantic memory for future retrieval.

The example demonstrates:

- `Experience` records with outcomes and supporting evidence;
- separation between episodic memory and learned procedures;
- a typed `Procedure` containing a repository constraint;
- minimum evidence requirements before generalization;
- deterministic validation before promotion; and
- retrieval of the promoted rule from `MemoryStore`.

### Run it

From the `bbagents` directory:

```console
python -m pip install -e .
python examples/repository_constraint_learning.py
```

## OAuth migration planner

`oauth_migration_planner.py` demonstrates the planning boundary developed in
Chapter 7 of the book. An agent must inspect an existing authentication architecture
before proposing an OAuth migration. Later steps depend on evidence gathered by
earlier steps, so the work cannot be represented as an unordered collection of tool
calls.

The example demonstrates:

- explicit `PlanStep` dependencies;
- deterministic selection and execution of registered tools;
- observations accumulating in `AgentState`;
- later tool arguments derived from earlier evidence;
- structured execution traces; and
- a final strategy produced only after its prerequisite inspections complete.

The repository is an in-memory fixture and the selector is an ordinary Python
function. This is deliberate: the example isolates planning and runtime behavior
without requiring a model provider. A production application can replace the
selector with a model-backed implementation while preserving the same plan and
execution boundaries.

### Run it

From the `bbagents` directory:

```console
python -m pip install -e .
python examples/oauth_migration_planner.py
```

## LangChain integration

`langchain_integration.py` demonstrates how LangChain and `bbagents` can occupy
different layers of the same application.

LangChain supplies two integration abstractions:

- a `BaseTool` implementation for repository inspection;
- a `BaseChatModel` implementation for producing a final recommendation.

Small adapters expose those objects through the corresponding `bbagents` protocols.
`bbagents` then owns the explicit plan, step dependencies, deterministic tool
execution, observations, and execution trace.

The example deliberately does not create a LangChain agent. Running two independent
agent loops would make ownership ambiguous. In this design, `bbagents` owns the loop
and LangChain supplies replaceable model and tool integrations. A LangGraph-based
application could invert that relationship: LangGraph would own the outer loop while
its nodes used the same `bbagents` plans and policies.

The example uses LangChain's `FakeListChatModel` and an in-memory repository so it
runs without credentials or network access.

### Run it

From the `bbagents` directory:

```console
python -m pip install -e '.[langchain]'
python examples/langchain_integration.py
```

Expected output includes the completed plan, observations from both repository
components, the model's recommendation, and the number of recorded trace events.

### Use a real provider

Replace `FakeListChatModel` with any LangChain `BaseChatModel`, such as a provider
adapter from `langchain-openai` or `langchain-anthropic`. Provider credentials and
packages remain application concerns and are intentionally not dependencies of
`bbagents`.

## Langfuse tracing

`langfuse_tracing.py` is the default observability example. It runs with no
account, network access, or optional package by retaining the complete trace in
memory and printing its event count. When `LANGFUSE_PUBLIC_KEY` and
`LANGFUSE_SECRET_KEY` are configured, the same example exports the agent run, plan
steps, and tool calls as nested Langfuse observations. Langfuse can be self-hosted.

### Run it locally

From the `bbagents` directory:

```console
python -m pip install -e .
python examples/langfuse_tracing.py
```

### Export to Langfuse

```console
python -m pip install -e '.[langfuse]'
export LANGFUSE_PUBLIC_KEY=<your-public-key>
export LANGFUSE_SECRET_KEY=<your-secret-key>
python examples/langfuse_tracing.py
```

## LangSmith tracing

`langsmith_tracing.py` sends the OAuth migration planner's complete execution
hierarchy to LangSmith. The trace contains the agent run, each plan step, and every
tool invocation. Step and tool failures are recorded as failed spans rather than
being represented only as terminal log text.

The adapter is an alternative integration and uses LangSmith's standard environment
configuration for credentials, endpoints, projects, and sampling. It does not make
LangSmith a runtime dependency of the library.

### Run it

From the `bbagents` directory:

```console
python -m pip install -e '.[langsmith]'
export LANGSMITH_API_KEY=<your-api-key>
python examples/langsmith_tracing.py
```
