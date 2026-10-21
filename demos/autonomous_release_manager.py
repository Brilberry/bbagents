"""A governed release manager that prepares a release of bbagents itself.

Run from the repository root after installing bbagents in editable mode:

    python demos/autonomous_release_manager.py --approve

The demo runs real project quality checks. Publishing is a simulated action handler:
replace it with an application-owned package-index adapter in a real deployment.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from bbagents import (
    Action,
    ActionResult,
    ActionRuntime,
    ActionStatus,
    AgentState,
    Observation,
    Plan,
    PlanStep,
    ToolCall,
    ToolRegistry,
)
from bbagents.context import ContextBudget, ContextBuilder
from bbagents.intervention import (
    ActorKind,
    ActionInterventionPolicy,
    ActionInterventionService,
    HumanOrDelegatedAuthority,
    InMemoryInterventionChannel,
    InMemoryInterventionStore,
    InterventionDecision,
    InterventionResponse,
)
from bbagents.memory import MemoryKind, MemoryRecord, MemoryStore
from bbagents.models import ModelProfile
from bbagents.observability import InMemoryTrace
from bbagents.optimization import Cache, route_model
from bbagents.organization import Message, Role
from bbagents.persistence import InMemoryStateStore
from bbagents.reference import PlanningAgent
from bbagents.retrieval import MemoryRetriever, RetrievalAugmenter
from bbagents.runtime import AuthorizationDecision, CompositeActionPolicy
from bbagents.safety import Capability, CapabilityPolicy
from bbagents.tools import FunctionTool

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BBAGENTS_ROOT = (
    REPOSITORY_ROOT
    if (REPOSITORY_ROOT / "pyproject.toml").is_file()
    else REPOSITORY_ROOT / "bbagents"
)


@dataclass(frozen=True, slots=True)
class QualityCheck:
    name: str
    passed: bool
    output: str


CheckRunner = Callable[[str, tuple[str, ...], Path], QualityCheck]


@dataclass(frozen=True, slots=True)
class ReleaseRecord:
    version: str
    status: str
    quality_checks: tuple[QualityCheck, ...] = ()
    release_notes: str = ""
    model: str = ""
    intervention_request_id: str | None = None
    publication: str | None = None


class ReleaseReadinessPolicy:
    """Domain policy: only a validated release can request publication."""

    def decide(
        self,
        record: ReleaseRecord,
        _: object,
    ) -> AuthorizationDecision:
        if not record.quality_checks or not all(
            check.passed for check in record.quality_checks
        ):
            return AuthorizationDecision.deny("quality checks have not passed")
        return AuthorizationDecision.allow("validated release can request publication")


def run_check(name: str, command: tuple[str, ...], cwd: Path) -> QualityCheck:
    """Execute one read-only release quality check against the package."""

    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    return QualityCheck(name, completed.returncode == 0, output)


class AutonomousReleaseManager:
    """Application-owned coordinator composed from bbagents primitives."""

    def __init__(
        self,
        project_root: Path = BBAGENTS_ROOT,
        *,
        check_runner: CheckRunner = run_check,
    ) -> None:
        self.project_root = project_root
        self.check_runner = check_runner
        self.trace = InMemoryTrace()
        self.store: InMemoryStateStore[ReleaseRecord] = InMemoryStateStore()
        self.release_id = "bbagents-release"
        self.capabilities = CapabilityPolicy(
            [Capability("publish_release", "package-index")],
            enforce_confirmation=False,
        )
        self.intervention_channel = InMemoryInterventionChannel()
        self.interventions = ActionInterventionService(
            InMemoryInterventionStore(),
            HumanOrDelegatedAuthority(),
            channel=self.intervention_channel,
        )
        self.runtime = ActionRuntime(
            {"publish_release": self._publish_to_index},
            policy=CompositeActionPolicy(
                [self.capabilities, ActionInterventionPolicy(self.interventions)]
            ),
        )
        self.readiness = ReleaseReadinessPolicy()
        self.roles = (
            Role("release-manager", ("coordinate evidence", "record release state")),
            Role("quality-engineer", ("validate package quality",)),
            Role("release-approver", ("approve publication",)),
        )
        self.messages: list[Message] = []
        self.published_versions: list[str] = []
        self._note_cache: Cache[str, str] = Cache()

    def preflight(self) -> ReleaseRecord:
        """Inspect, retrieve prior knowledge, validate, and draft release notes."""

        agent, state = self._build_preflight_agent()
        agent.run(state)
        if not state.completed:
            raise RuntimeError("release preflight plan did not complete")

        metadata = self._metadata_from(state, "release.metadata", "project")
        checks = tuple(self._metadata_from(state, "release.validation", "checks"))
        notes = self._metadata_from(state, "release.notes", "notes")
        model = self._metadata_from(state, "release.notes", "model")
        status = (
            "ready_for_intervention"
            if all(check.passed for check in checks)
            else "blocked"
        )
        record = ReleaseRecord(
            version=str(metadata["version"]),
            status=status,
            quality_checks=checks,
            release_notes=notes,
            model=model,
        )
        self.store.save(self.release_id, record)
        self.messages.append(
            Message(
                "release-manager",
                "release-approver",
                {
                    "version": record.version,
                    "status": record.status,
                    "quality_checks": [check.name for check in checks],
                },
            )
        )
        self.trace.record(
            "release.preflight.completed",
            {"version": record.version, "status": record.status},
        )
        return record

    def approve(self, actor: str) -> ReleaseRecord:
        """Resolve the pending request through an authenticated-human adapter."""

        snapshot = self._require_current()
        request_id = snapshot.value.intervention_request_id
        if request_id is None:
            raise RuntimeError("request publication before recording an approval")
        self.intervention_channel.submit_response(
            request_id,
            InterventionResponse(
                actor,
                ActorKind.HUMAN,
                InterventionDecision.APPROVE,
                request_version=self.interventions.get(request_id).version,
            ),
        )
        record = replace(snapshot.value, status="ready_for_publication")
        self.store.save(
            self.release_id,
            record,
            expected_version=snapshot.version,
            metadata={"approved_by": actor},
        )
        self.trace.record(
            "release.intervention.approved",
            {"version": record.version, "actor": actor, "request_id": request_id},
        )
        return record

    def publish(self) -> ActionResult:
        """Enforce readiness and capability policy before simulated publication."""

        snapshot = self._require_current()
        record = snapshot.value
        readiness = self.readiness.decide(record, None)
        if not readiness.allowed:
            self.trace.record(
                "release.readiness.denied",
                {"version": record.version, "reason": readiness.reason},
            )
            return ActionResult(
                ActionStatus.DENIED,
                error=readiness.reason,
                attempts=0,
            )

        action = Action(
            "publish_release",
            {"version": record.version},
            expected_effect="publish an immutable package release",
            requires_confirmation=True,
            intervention_id=record.intervention_request_id,
            metadata={"resource": "package-index"},
        )
        result = self.runtime.execute(action)
        status = "released" if result.success else "awaiting_human_intervention"
        if result.intervention_request_id is None and not result.success:
            status = "publication_denied"
        updated = replace(
            record,
            status=status,
            intervention_request_id=result.intervention_request_id
            or record.intervention_request_id,
            publication=str(result.output) if result.success else None,
        )
        self.store.save(
            self.release_id,
            updated,
            expected_version=snapshot.version,
            metadata={"publication_attempt": True},
        )
        self.trace.record(
            "release.publication.finished",
            {"version": record.version, "success": result.success, "status": status},
        )
        return result

    def current(self) -> ReleaseRecord:
        return self._require_current().value

    def _build_preflight_agent(self) -> tuple[PlanningAgent, AgentState]:
        memory = MemoryStore()
        memory.remember(
            MemoryRecord(
                "Release only after tests, linting, and type checks pass.",
                kind=MemoryKind.PROCEDURAL,
                source="release-policy",
                metadata={"rule": "quality-gate"},
            )
        )
        memory.remember(
            MemoryRecord(
                "Release notes must name the validated package version.",
                kind=MemoryKind.PROCEDURAL,
                source="release-policy",
                metadata={"rule": "versioned-notes"},
            )
        )
        retrieval = RetrievalAugmenter(
            MemoryRetriever(memory, kind=MemoryKind.PROCEDURAL),
            ContextBuilder(ContextBudget(max_items=3, max_characters=600)),
            trace=self.trace,
        )

        def inspect_metadata() -> Observation:
            with (self.project_root / "pyproject.toml").open("rb") as file:
                project = tomllib.load(file)["project"]
            return Observation(
                "release.metadata",
                f"Prepared {project['name']} {project['version']} for release.",
                metadata={"project": project},
            )

        def retrieve_policy() -> Observation:
            context = retrieval.augment("release validation and release notes")
            return Observation(
                "release.policy-context",
                context.content,
                metadata={"context": context},
            )

        def validate() -> Observation:
            checks = tuple(
                self.check_runner(name, command, self.project_root)
                for name, command in (
                    ("tests", (sys.executable, "-m", "pytest")),
                    ("lint", (sys.executable, "-m", "ruff", "check", "src", "tests")),
                    ("types", (sys.executable, "-m", "mypy", "src")),
                )
            )
            summary = ", ".join(
                f"{check.name}={'passed' if check.passed else 'failed'}"
                for check in checks
            )
            return Observation(
                "release.validation", summary, metadata={"checks": checks}
            )

        def draft_notes() -> Observation:
            project = self._metadata_from(state, "release.metadata", "project")
            policy_context = self._metadata_from(
                state,
                "release.policy-context",
                "context",
            )
            profile = route_model(
                (
                    ModelProfile("local-fast", 0, 0, 0.2, 0.6),
                    ModelProfile("local-release-writer", 0, 0, 1.2, 0.85),
                ),
                minimum_quality=0.8,
                maximum_latency=2.0,
            )
            key = f"release-notes:{project['version']}"
            notes = self._note_cache.get(key)
            if notes is None:
                notes = (
                    f"{project['name']} {project['version']} is ready for publication. "
                    f"Validated release policy: {policy_context.items[0].content}"
                )
                self._note_cache.put(key, notes)
            return Observation(
                "release.notes",
                notes,
                metadata={"notes": notes, "model": profile.name},
            )

        tools = ToolRegistry(
            [
                FunctionTool(
                    "inspect_metadata",
                    "Read package release metadata.",
                    inspect_metadata,
                ),
                FunctionTool(
                    "retrieve_policy",
                    "Retrieve prior release procedures.",
                    retrieve_policy,
                ),
                FunctionTool("validate", "Run release quality checks.", validate),
                FunctionTool(
                    "draft_notes",
                    "Draft release notes from validated evidence.",
                    draft_notes,
                ),
            ]
        )
        plan = Plan(
            "Prepare a governed bbagents release",
            [
                PlanStep("metadata", "Inspect package metadata"),
                PlanStep(
                    "policy", "Retrieve release procedure", depends_on=["metadata"]
                ),
                PlanStep("validation", "Run quality gates", depends_on=["metadata"]),
                PlanStep(
                    "notes",
                    "Draft release notes",
                    depends_on=["policy", "validation"],
                ),
            ],
        )
        state = AgentState(plan.goal, plan)
        calls = {
            "metadata": ToolCall("inspect_metadata"),
            "policy": ToolCall("retrieve_policy"),
            "validation": ToolCall("validate"),
            "notes": ToolCall("draft_notes"),
        }
        agent = PlanningAgent(
            tools,
            lambda _state, step: [calls[step.id]],
            trace=self.trace,
        )
        return agent, state

    def _publish_to_index(self, version: str) -> str:
        """Simulate the adapter that would call a package index in production."""

        self.published_versions.append(version)
        return f"published bbagents {version} to package-index"

    def _require_current(self):
        snapshot = self.store.load(self.release_id)
        if snapshot is None:
            raise RuntimeError("run preflight before approving or publishing")
        return snapshot

    @staticmethod
    def _metadata_from(state: AgentState, source: str, field: str) -> Any:
        return next(
            observation.metadata[field]
            for observation in state.observations
            if observation.source == source
        )


def run_demo(*, approve: bool) -> tuple[AutonomousReleaseManager, ActionResult | None]:
    manager = AutonomousReleaseManager()
    manager.preflight()
    pending = manager.publish()
    if not approve:
        return manager, pending
    manager.approve("release-approver")
    return manager, manager.publish()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--approve",
        action="store_true",
        help="apply the demo release-approver's authorization and publish",
    )
    arguments = parser.parse_args()
    manager, publication = run_demo(approve=arguments.approve)
    record = manager.current()

    print(f"Release: bbagents {record.version}")
    print(f"Status: {record.status}")
    print("Quality checks:")
    for check in record.quality_checks:
        print(f"- {check.name}: {'passed' if check.passed else 'failed'}")
    print(f"Release-note model: {record.model}")
    print(f"Trace events: {len(manager.trace.events)}")
    if publication is None or publication.intervention_request_id is not None:
        print("Publication is awaiting human intervention.")
    else:
        print(
            f"Publication: {publication.output if publication.success else publication.error}"
        )


if __name__ == "__main__":
    main()
