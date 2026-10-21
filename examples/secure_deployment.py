"""Authorize a production deployment through explicit capabilities and approval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bbagents import Action, ActionResult, ActionRuntime
from bbagents.safety import Capability, CapabilityPolicy


@dataclass(slots=True)
class DeploymentExample:
    """Inspectable results from the secure deployment workflow."""

    inspection: ActionResult
    denied_deployment: ActionResult
    approved_deployment: ActionResult
    policy: CapabilityPolicy
    effects: list[dict[str, Any]]


def run_example() -> DeploymentExample:
    """Run inspection, denial, approval, and deployment in sequence."""

    effects: list[dict[str, Any]] = []

    def inspect_repository(repository: str) -> str:
        effects.append({"action": "inspect_repository", "repository": repository})
        return f"{repository} passed repository checks"

    def deploy_release(environment: str, version: str) -> str:
        effects.append(
            {
                "action": "deploy_release",
                "environment": environment,
                "version": version,
            }
        )
        return f"deployed {version} to {environment}"

    approvals: set[str] = set()
    policy = CapabilityPolicy(
        capabilities={
            Capability("inspect_repository", "payments"),
            Capability("deploy_release", "production"),
        },
        approvals=approvals,
    )
    runtime = ActionRuntime(
        {
            "inspect_repository": inspect_repository,
            "deploy_release": deploy_release,
        },
        policy=policy,
    )

    inspection = runtime.execute(
        Action(
            "inspect_repository",
            {"repository": "payments"},
            expected_effect="Read repository metadata",
            metadata={"resource": "payments"},
        )
    )

    deployment = Action(
        "deploy_release",
        {"environment": "production", "version": "2026.7.2"},
        expected_effect="Release the OAuth migration to production",
        requires_confirmation=True,
        metadata={"resource": "production"},
    )
    denied_deployment = runtime.execute(deployment)

    approvals.add("deploy_release")
    approved_deployment = runtime.execute(deployment)

    return DeploymentExample(
        inspection=inspection,
        denied_deployment=denied_deployment,
        approved_deployment=approved_deployment,
        policy=policy,
        effects=effects,
    )


def main() -> None:
    result = run_example()

    print(f"Inspection allowed: {result.inspection.success}")
    print(
        "Deployment before approval: "
        f"{result.denied_deployment.success} ({result.denied_deployment.error})"
    )
    print(f"Deployment after approval: {result.approved_deployment.success}")
    print(f"Executed effects: {len(result.effects)}")
    print("\nAudit log:")
    for decision in result.policy.audit_log:
        print(
            f"- {decision['action']}: "
            f"{'allowed' if decision['allowed'] else 'denied'} "
            f"({decision['reason']})"
        )


if __name__ == "__main__":
    main()
