"""Coordinate a governed software change across persistent organizational roles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bbagents.organization import GovernancePolicy, Message, OrganizationalTask, Role


@dataclass(slots=True)
class SoftwareOrganization:
    """Small deterministic coordinator built from bbagents organization values."""

    roles: dict[str, Role]
    policies: tuple[GovernancePolicy, ...]
    completed: set[str] = field(default_factory=set)
    messages: list[Message] = field(default_factory=list)

    def permits(self, task: OrganizationalTask) -> bool:
        return task.owner in self.roles and all(
            policy.permits(task) for policy in self.policies
        )

    def execute(
        self,
        task: OrganizationalTask,
        *,
        outcome: str,
        next_task: OrganizationalTask | None = None,
    ) -> None:
        if not self.permits(task):
            raise PermissionError(f"Governance denied task: {task.id}")
        missing = set(task.dependencies) - self.completed
        if missing:
            raise RuntimeError(f"Task {task.id} has incomplete dependencies: {missing}")

        self.completed.add(task.id)
        if next_task is not None:
            self.messages.append(
                Message(
                    sender=task.owner or "unassigned",
                    recipient=next_task.owner or "unassigned",
                    content={
                        "completed_task": task.id,
                        "next_task": next_task.id,
                        "evidence": outcome,
                    },
                )
            )


@dataclass(slots=True)
class OrganizationExample:
    """Inspectable result from the governed workflow."""

    organization: SoftwareOrganization
    tasks: tuple[OrganizationalTask, ...]
    unauthorized_deployment_denied: bool


def build_organization() -> SoftwareOrganization:
    """Create stable roles and governance rules for software delivery."""

    roles = {
        role.name: role
        for role in (
            Role("planning", ("decompose changes", "define acceptance criteria")),
            Role("implementation", ("modify code", "produce implementation evidence")),
            Role("validation", ("verify acceptance criteria", "reject unsafe changes")),
            Role("deployment", ("authorize releases", "record deployment outcomes")),
        )
    }
    policies = (
        GovernancePolicy(
            "independent validation",
            lambda task: task.id != "validate" or task.owner == "validation",
            "Validation must be owned by the validation role.",
        ),
        GovernancePolicy(
            "deployment authority",
            lambda task: not task.id.startswith("deploy") or task.owner == "deployment",
            "Only the deployment role may release software.",
        ),
    )
    return SoftwareOrganization(roles=roles, policies=policies)


def build_workflow() -> tuple[OrganizationalTask, ...]:
    """Define the ownership and dependency structure of one production change."""

    return (
        OrganizationalTask(
            "plan",
            "Plan the OAuth migration",
            owner="planning",
        ),
        OrganizationalTask(
            "implement",
            "Implement the OAuth migration",
            owner="implementation",
            dependencies=["plan"],
        ),
        OrganizationalTask(
            "validate",
            "Validate tests and acceptance evidence",
            owner="validation",
            dependencies=["implement"],
        ),
        OrganizationalTask(
            "deploy",
            "Deploy the validated migration",
            owner="deployment",
            dependencies=["validate"],
        ),
    )


def run_example() -> OrganizationExample:
    """Reject an unauthorized release, then execute the governed workflow."""

    organization = build_organization()
    tasks = build_workflow()
    unauthorized = OrganizationalTask(
        "deploy-unreviewed",
        "Deploy without independent validation",
        owner="implementation",
    )
    unauthorized_deployment_denied = not organization.permits(unauthorized)

    outcomes: dict[str, Any] = {
        "plan": "Migration plan and acceptance criteria approved",
        "implement": "OAuth callback implemented; unit tests pass",
        "validate": "Integration and authorization tests pass",
        "deploy": "Version 2026.7.2 released to production",
    }
    for index, task in enumerate(tasks):
        next_task = tasks[index + 1] if index + 1 < len(tasks) else None
        organization.execute(
            task,
            outcome=str(outcomes[task.id]),
            next_task=next_task,
        )

    return OrganizationExample(
        organization=organization,
        tasks=tasks,
        unauthorized_deployment_denied=unauthorized_deployment_denied,
    )


def main() -> None:
    result = run_example()

    print(
        "Unauthorized deployment denied: "
        f"{result.unauthorized_deployment_denied}"
    )
    completed = [
        task.id for task in result.tasks if task.id in result.organization.completed
    ]
    print(f"Completed tasks: {', '.join(completed)}")
    print("\nRole handoffs:")
    for message in result.organization.messages:
        print(
            f"- {message.sender} -> {message.recipient}: "
            f"{message.content['evidence']}"
        )


if __name__ == "__main__":
    main()
