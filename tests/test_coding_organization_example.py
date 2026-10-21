from examples.coding_organization import build_organization, run_example

from bbagents.organization import OrganizationalTask


def test_workflow_preserves_ownership_and_dependency_order() -> None:
    result = run_example()

    assert result.organization.completed == {"plan", "implement", "validate", "deploy"}
    assert [
        (message.sender, message.recipient)
        for message in result.organization.messages
    ] == [
        ("planning", "implementation"),
        ("implementation", "validation"),
        ("validation", "deployment"),
    ]


def test_governance_denies_deployment_by_implementation_role() -> None:
    organization = build_organization()
    unauthorized = OrganizationalTask(
        "deploy-unreviewed",
        "Deploy without validation",
        owner="implementation",
    )

    assert not organization.permits(unauthorized)


def test_execution_rejects_incomplete_dependencies() -> None:
    organization = build_organization()
    task = OrganizationalTask(
        "implement",
        "Implement before planning",
        owner="implementation",
        dependencies=["plan"],
    )

    try:
        organization.execute(task, outcome="not allowed")
    except RuntimeError as exc:
        assert "incomplete dependencies" in str(exc)
    else:
        raise AssertionError("task executed before its dependency")
