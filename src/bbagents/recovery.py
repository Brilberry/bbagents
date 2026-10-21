"""Durable execution intent and application-owned side-effect reconciliation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import time
from typing import Any, Callable, Protocol

from .execution import AsyncActionRuntime
from .persistence import StateStore
from .runtime import Action, ActionResult, ActionStatus, FailureKind, _evaluate_policy


class ReconciliationStatus(str, Enum):
    COMPLETED = "completed"
    NOT_EXECUTED = "not_executed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Reconciliation:
    status: ReconciliationStatus
    result: ActionResult | None = None


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    id: str
    fingerprint: str
    state: str
    lease_until: float
    result: ActionResult | None = None


class Reconciler(Protocol):
    """NOT_EXECUTED must establish that a prior attempt cannot later take effect."""

    async def reconcile(
        self, action: Action, record: ExecutionRecord
    ) -> Reconciliation: ...


def action_fingerprint(action: Action) -> str:
    """Strict JSON binding includes resource, approval-relevant state and intent."""

    payload = {
        "name": action.name,
        "parameters": dict(action.parameters),
        "expected_effect": action.expected_effect,
        "metadata": dict(action.metadata),
        "requires_confirmation": action.requires_confirmation,
        "idempotency_key": action.idempotency_key,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


class ExecutionJournal:
    """Use a transactional StateStore for competing workers. Results must be JSON values."""

    def __init__(self, store: StateStore[dict[str, Any]]) -> None:
        self.store = store

    def load(self, execution_id: str) -> tuple[ExecutionRecord, int] | None:
        snapshot = self.store.load(execution_id)
        if snapshot is None:
            return None
        data = dict(snapshot.value)
        result = data.get("result")
        if result is not None:
            result = dict(result)
            result["status"] = ActionStatus(result["status"])
            if result.get("failure_kind") is not None:
                result["failure_kind"] = FailureKind(result["failure_kind"])
            data["result"] = ActionResult(**result)
        return ExecutionRecord(**data), snapshot.version

    def save(self, record: ExecutionRecord, *, expected_version: int) -> int:
        return self.store.save(
            record.id, asdict(record), expected_version=expected_version
        ).version


class RecoverableActionRuntime:
    """Journal and reconcile execution without claiming exactly-once effects.

    Live claims are protected by a lease. After expiry a reconciler must establish
    the external outcome; lease expiry alone never permits replay. Cancelled calls
    leave their durable claim for recovery. Authorization is rechecked on every call.
    """

    def __init__(
        self,
        runtime: AsyncActionRuntime,
        journal: ExecutionJournal,
        reconciler: Reconciler,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.runtime = runtime
        self.journal = journal
        self.reconciler = reconciler
        self.clock = clock

    async def execute(self, action: Action, *, execution_id: str) -> ActionResult:
        fingerprint = action_fingerprint(action)
        previous = self.journal.load(execution_id)
        if previous is not None and previous[0].fingerprint != fingerprint:
            raise ValueError("Execution ID is bound to a different action or state")
        decision = _evaluate_policy(self.runtime.policy, action)
        if not decision.allowed:
            return ActionResult(
                ActionStatus.PENDING_INTERVENTION
                if decision.pending
                else ActionStatus.DENIED,
                attempts=0,
                error=decision.reason,
                intervention_request_id=decision.intervention_request_id,
            )
        version = 0
        if previous is not None:
            record, version = previous
            if record.state == "finished" and record.result is not None:
                return record.result
            if record.state == "running" and record.lease_until > self.clock():
                return _unknown("Execution is already claimed")
            reconciled = await self.reconciler.reconcile(action, record)
            if reconciled.status == ReconciliationStatus.COMPLETED:
                if reconciled.result is None or reconciled.result.outcome_unknown:
                    raise ValueError(
                        "Completed reconciliation requires a confirmed result"
                    )
                self.journal.save(
                    ExecutionRecord(
                        execution_id, fingerprint, "finished", 0, reconciled.result
                    ),
                    expected_version=version,
                )
                return reconciled.result
            if reconciled.status != ReconciliationStatus.NOT_EXECUTED:
                return _unknown("External outcome remains unknown")
        version = self.journal.save(
            ExecutionRecord(
                execution_id,
                fingerprint,
                "running",
                self.clock() + action.timeout_seconds + 5,
            ),
            expected_version=version,
        )
        result = await self.runtime.execute(action)
        self.journal.save(
            ExecutionRecord(
                execution_id,
                fingerprint,
                "uncertain" if result.outcome_unknown else "finished",
                0,
                result,
            ),
            expected_version=version,
        )
        return result


def _unknown(reason: str) -> ActionResult:
    return ActionResult(
        ActionStatus.FAILED,
        error=reason,
        attempts=0,
        failure_kind=FailureKind.UNKNOWN,
        outcome_unknown=True,
    )
