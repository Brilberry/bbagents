from bbagents import Action, ActionRuntime, ActionStatus, AuthorizationDecision
from bbagents.safety import Capability, CapabilityPolicy


def test_runtime_retries_transient_failure() -> None:
    attempts = 0

    def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")
        return "ok"

    result = ActionRuntime({"flaky": flaky}).execute(Action("flaky"), retries=1)

    assert result.success
    assert result.output == "ok"
    assert result.attempts == 2


def test_capability_policy_denies_ungranted_and_unapproved_actions() -> None:
    policy = CapabilityPolicy([Capability("read"), Capability("write")])
    runtime = ActionRuntime({"read": lambda: "data", "write": lambda: "changed"}, policy=policy)

    assert runtime.execute(Action("read")).success
    assert not runtime.execute(Action("delete")).success
    assert not runtime.execute(Action("write", requires_confirmation=True)).success
    assert len(policy.audit_log) == 3


def test_action_result_status_is_authoritative_for_policy_decisions() -> None:
    class PendingPolicy:
        def decide(self, action: Action) -> AuthorizationDecision:
            del action
            return AuthorizationDecision.pending_intervention("approval needed", "request-1")

    result = ActionRuntime({"deploy": lambda: None}, policy=PendingPolicy()).execute(
        Action("deploy")
    )

    assert result.status == ActionStatus.PENDING_INTERVENTION
    assert not result.success
    assert result.intervention_request_id == "request-1"
