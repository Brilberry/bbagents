"""Select a controller offline; execute and update plan evidence in the application."""

from dataclasses import dataclass

from bbagents import FunctionTool, Observation, Plan, PlanStep, ToolCall, ToolRegistry
from bbagents.behavior import Abstain, PolicyRule, PolicySelector, Proposal, Selection
from bbagents.observability import InMemoryTrace
from bbagents.planning import StepStatus


@dataclass(frozen=True, slots=True)
class InspectionContext:
    incident: bool
    available_plan: Plan | None


class IncidentController:
    def propose(self, context: InspectionContext) -> Proposal | Abstain:
        if not context.incident:
            return Abstain("No incident requires immediate inspection")
        return Proposal(
            ToolCall("inspect", {"component": "service"}), "Incident active"
        )


class AvailablePlanController:
    def propose(self, context: InspectionContext) -> Proposal | Abstain:
        # Model inference and plan validation would happen before selection.
        plan = context.available_plan
        step = None if plan is None else plan.next_ready_step()
        if step is None:
            return Abstain("No executable milestone is available")
        return Proposal(
            ToolCall("inspect", {"component": step.id}), "Continue the available plan"
        )


class InventoryController:
    def propose(self, context: InspectionContext) -> Proposal:
        return Proposal(ToolCall("inventory"), "No higher-priority work")


def run_example() -> tuple[Selection, Plan, Observation, InMemoryTrace]:
    plan = Plan("Inspect authentication", [PlanStep("authentication", "Inspect auth")])
    snapshot = InspectionContext(incident=False, available_plan=plan)
    trace = InMemoryTrace()
    selector = PolicySelector(
        [
            PolicyRule("incident", IncidentController(), priority=100),
            PolicyRule("available_plan", AvailablePlanController(), priority=40),
            PolicyRule("inventory", InventoryController(), priority=-10),
        ],
        trace=trace,
    )
    selection = selector.select(snapshot)
    assert [attempt.policy for attempt in selection.considered] == [
        "incident",
        "available_plan",
    ]  # The inventory controller was never evaluated.
    step = plan.next_ready_step()
    assert step is not None and step.status == StepStatus.PENDING
    if selection.action is None:
        raise RuntimeError("Application must handle no-match explicitly")

    tools = ToolRegistry(
        [
            FunctionTool(
                "inspect",
                "Inspect a repository component",
                lambda component: Observation("repository", f"Inspected {component}"),
            ),
            FunctionTool(
                "inventory", "List repository components", lambda: "authentication"
            ),
        ]
    )
    # Selection is not authorization. These are read-only example tools; guarded
    # effects belong in the application's existing actor-bound ActionRuntime path.
    observation = tools.execute(selection.action)
    # Only application-owned feedback completes the milestone, after execution.
    step.evidence.append(observation.content)
    step.status = StepStatus.DONE
    return selection, plan, observation, trace


def main() -> None:
    selection, plan, observation, trace = run_example()
    for attempt in selection.considered:
        print(f"{attempt.policy}: {attempt.outcome} ({attempt.reason})")
    print(f"Observation: {observation.content}")
    print(f"Plan complete after application feedback: {plan.is_complete()}")
    print(f"Trace events: {len(trace.events)}")


if __name__ == "__main__":
    main()
