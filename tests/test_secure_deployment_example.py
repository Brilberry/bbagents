from examples.secure_deployment import run_example


def test_deployment_requires_approval_before_execution() -> None:
    result = run_example()

    assert result.inspection.success
    assert not result.denied_deployment.success
    assert result.denied_deployment.error == "human approval required"
    assert result.denied_deployment.attempts == 0
    assert result.approved_deployment.success

    deployment_effects = [
        effect for effect in result.effects if effect["action"] == "deploy_release"
    ]
    assert len(deployment_effects) == 1


def test_policy_records_denied_and_allowed_decisions() -> None:
    result = run_example()

    assert list(result.policy.audit_log) == [
        {
            "action": "inspect_repository",
            "allowed": True,
            "reason": "capability granted",
        },
        {
            "action": "deploy_release",
            "allowed": False,
            "reason": "human approval required",
        },
        {
            "action": "deploy_release",
            "allowed": True,
            "reason": "capability granted",
        },
    ]
