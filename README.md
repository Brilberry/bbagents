# bbagents

Python building blocks for agent systems: tools, planning, model selection, memory,
human approval, tracing, and evaluation. Works with LangGraph or your own runtime.

Companion code for *Agent Engineering*. [Buy the book](https://press.brilberry.com).

Requires Python 3.11+.

```sh
pip install -e '.[dev]'
pytest
```

## Example

An agent proposes a release. Your application must obtain human approval, survive
restarts, and avoid blindly repeating publication. This runnable example uses a
local file as a simulated package index; it never publishes to PyPI.

Run it once to create the approval request, then again to approve or defer.
Subsequent runs reuse the recorded result. No model or credentials are required:
the proposed action is fixed here to focus on execution safeguards.

```python
import asyncio
from dataclasses import replace
from pathlib import Path

from bbagents import Action, ActionResult, ActionStatus
from bbagents.execution import AsyncActionRuntime, AsyncFunctionAdapter
from bbagents.intervention import (
    ActionInterventionPolicy, ActionInterventionService, ActorKind,
    HumanOrDelegatedAuthority, InterventionDecision, InterventionResponse,
    InterventionStatus, StateInterventionStore,
)
from bbagents.persistence import SQLiteStateStore
from bbagents.recovery import (
    ExecutionJournal, RecoverableActionRuntime, Reconciliation, ReconciliationStatus,
)

directory = Path(".release-example")
directory.mkdir(exist_ok=True)
store = SQLiteStateStore(directory / "state.db")
approvals = ActionInterventionService(
    StateInterventionStore(store), HumanOrDelegatedAuthority(),
)
artifact = directory / "bbagents-0.1.0.published"
receipt = "bbagents 0.1.0 published to the simulated index"


async def publish(action, context):
    # Exclusive creation makes this simulated publication immutable.
    with artifact.open("x") as file:
        file.write(receipt)
    return receipt


class PackageIndexLookup:
    async def reconcile(self, action, record):
        if artifact.exists() and artifact.read_text() == receipt:
            return Reconciliation(
                ReconciliationStatus.COMPLETED,
                ActionResult(ActionStatus.SUCCEEDED, receipt),
            )
        # Absence alone does not prove a previous attempt cannot still finish.
        return Reconciliation(ReconciliationStatus.UNKNOWN)


async def main():
    action = Action(
        "publish", {"package": "bbagents", "version": "0.1.0"},
        expected_effect="Publish bbagents 0.1.0 to the simulated index",
        requires_confirmation=True, idempotency_key="bbagents-0.1.0",
        metadata={"resource": "simulated-index"},
    )
    saved = store.load("release-approval")
    if saved is None:
        request = approvals.request(action)
        store.save("release-approval", {"id": request.id}, expected_version=0)
        print("Awaiting human approval. Run again to review.")
        return

    record = approvals.get(saved.value["id"])
    if record.status == InterventionStatus.PENDING:
        if input(f"{record.request.summary}? [yes/no] ").strip().lower() != "yes":
            print("Not approved; nothing published.")
            return
        # This CLI trusts its local operator, not model-supplied identity claims.
        approvals.respond(record.request.id, InterventionResponse(
            "local-operator", ActorKind.HUMAN, InterventionDecision.APPROVE,
            record.version,
        ))

    runtime = RecoverableActionRuntime(
        AsyncActionRuntime(
            AsyncFunctionAdapter({"publish": publish}),
            policy=ActionInterventionPolicy(approvals),
        ),
        ExecutionJournal(store), PackageIndexLookup(),
    )
    result = await runtime.execute(
        replace(action, intervention_id=record.request.id),
        execution_id="publish-bbagents-0.1.0",
    )
    print(f"Release result: {result.output if result.success else result.error}")


asyncio.run(main())
```

Approval and execution state survive process restarts. Confirmed results are reused;
uncertain outcomes require application-owned reconciliation, not automatic replay.
This is not an exactly-once guarantee. A hosted application must authenticate
approvers and implement its actual package-index adapter.

The [release-manager demo](demos/autonomous_release_manager.py) adds repository
checks, completion gates and optional local-model release notes. For orchestration,
use these contracts inside LangGraph nodes, including its interrupt/resume flow.

## Features

- Typed tools, dependency-checked plans, structured model decisions and validation.
- Per-agent model selection, routing constraints, cost estimates and caching.
- Capability policies, human intervention, explicit delegation and LangGraph interrupts.
- Bounded async execution, retry policies, execution journals and effect reconciliation.
- Versioned checkpoints, SQLite persistence, task budgets and completion gates.
- Budgeted context, summarization contracts and provider-neutral retrieval (RAG).
- Scoped, revisioned memory and skills; evaluated and approved procedure promotion.
- Roles, messages, delegation and organizational governance.
- Structured traces, isolated evaluation trials and compaction-continuation checks.
- Optional Ollama, LangChain, LangGraph, LangSmith and Langfuse integrations.

No mandatory runtime dependencies. Applications own domain behavior and adapters.

See [examples](examples/), [demos](demos/) and the [guide](GUIDE.md).
