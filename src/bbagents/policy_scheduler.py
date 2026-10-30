"""Actor-local, deterministic event scheduling for compiled decision policies."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from .behavior_trees import Execution, Status, _json_copy
from .core import NullTrace, TraceSink
from .decision_policies import CompiledPolicy, digest
from .observability import TraceEventName
from .policy_registry import Event, Frame, PolicyRegistry

ContextT = TypeVar("ContextT")


@dataclass(frozen=True, slots=True)
class PolicyTick:
    policy: str | None
    status: Status | None
    event_sequence: int | None = None


@dataclass(slots=True)
class _Run(Generic[ContextT]):
    policy: CompiledPolicy[ContextT]
    execution: Execution[Frame[ContextT]]
    event: Event


class PolicySchedulingError(RuntimeError):
    """Event evaluation failed. Explicit cancellation is required before reuse."""


class PolicyScheduler(Generic[ContextT]):
    """One scheduler per actor; one selected policy advances per supplied step.

    Priorities descend, with registration-order ties. Running work wins equal
    priority ties; only strictly higher priority preempts. Occurrences queue FIFO
    per policy, while level triggers latch until release (hysteresis). Cancellation
    precedes any replacement action. This does not supply threads or authorization.
    """

    def __init__(
        self,
        registry: PolicyRegistry[ContextT],
        policies: Iterable[CompiledPolicy[ContextT]],
        *,
        max_pending: int = 64,
        trace: TraceSink | None = None,
    ) -> None:
        if type(max_pending) is not int or not 1 <= max_pending <= 4096:
            raise ValueError("max_pending must be an integer from 1 to 4096")
        rows = tuple(policies)
        if len(rows) > 256:
            raise ValueError("At most 256 policies per scheduler")
        registry_fingerprint = digest(registry.catalog())
        known: set[str] = set()
        for row in rows:
            if (
                not isinstance(row, CompiledPolicy)
                or row.registry_fingerprint != registry_fingerprint
            ):
                raise ValueError(
                    "Policies must be compiled against this registry version/catalog"
                )
            if row.id in known:
                raise ValueError("Duplicate policy ID")
            known.add(row.id)
        self.registry = registry
        self._registered = rows
        self._policies = {row.id: row for row in rows}
        self._ordered = tuple(sorted(rows, key=lambda row: -row.priority))
        self.max_pending = max_pending
        self._trace = NullTrace() if trace is None else trace
        if not callable(getattr(self._trace, "record", None)):
            raise TypeError("trace must provide record()")
        self.fingerprint = digest(
            {
                "policies": [row.fingerprint for row in rows],
                "max_pending": max_pending,
                "registry": registry_fingerprint,
            }
        )
        self._pending: dict[str, list[Event]] = {row.id: [] for row in rows}
        self._levels: dict[str, bool] = {
            row.id: False for row in rows if registry.events[row.event].kind == "level"
        }
        self._ready: dict[str, int] = {row.id: 0 for row in rows}
        self._active: _Run[ContextT] | None = None
        self._last_step = -1
        self._sequence = -1
        self._busy = False
        self._faulted = False

    def _record(self, event_name: TraceEventName, **fields: Any) -> None:
        self._trace.record(
            event_name,
            {"scheduler": self.fingerprint, "step": self._last_step, **fields},
        )

    def _level_event(self, policy: CompiledPolicy[ContextT]) -> Event:
        return self.registry.event(
            Event(policy.event, payload=policy.event_parameters), occurrence=False
        )

    def _intake(self, events: Iterable[Event]) -> tuple[dict[str, list[Event]], int]:
        rows = tuple(self.registry.event(event, occurrence=True) for event in events)
        previous = -1
        for event in rows:
            assert event.sequence is not None
            if event.sequence <= previous:
                raise ValueError(
                    "Occurrences must be ordered with unique increasing sequences"
                )
            previous = event.sequence
        queues = {key: list(value) for key, value in self._pending.items()}
        sequence = self._sequence
        for event in rows:
            assert event.sequence is not None
            if event.sequence <= self._sequence:
                continue  # application replay of already accepted input
            for policy in self._registered:
                if policy.enabled and policy.event == event.name:
                    reserved = int(
                        self._active is not None
                        and self._active.policy.id == policy.id
                        and self._active.event.sequence is not None
                    )
                    if len(queues[policy.id]) + reserved >= self.max_pending:
                        raise ValueError(
                            "Occurrence queue capacity exceeded; no input accepted"
                        )
                    queues[policy.id].append(event)
            sequence = event.sequence
        return queues, sequence

    def _poll_levels(self, context: ContextT) -> None:
        for policy in self._registered:
            if not policy.enabled or policy.id not in self._levels:
                continue
            spec = self.registry.events[policy.event]
            frame = Frame(context, self._level_event(policy))
            was_active = self._levels[policy.id]
            callback = (
                spec.release if was_active and spec.release is not None else spec.active
            )
            assert callback is not None
            try:
                value = callback(frame)
                if type(value) is not bool:
                    raise TypeError("Event predicate must return bool")
            except Exception as exc:
                raise PolicySchedulingError(
                    f"Event {policy.event!r} raised {type(exc).__name__}"
                ) from exc
            active = not value if was_active and spec.release is not None else value
            self._levels[policy.id] = active
            if active != was_active:
                self._record(
                    TraceEventName.POLICY_EVENT,
                    policy=policy.id,
                    event=policy.event,
                    active=active,
                )

    def _eligible(self, policy: CompiledPolicy[ContextT], step: int) -> bool:
        return (
            policy.enabled
            and step >= self._ready[policy.id]
            and (self._levels.get(policy.id, False) or bool(self._pending[policy.id]))
        )

    def _stop(self, context: ContextT, *, reason: str, requeue: bool) -> None:
        run = self._active
        if run is None:
            return
        run.execution.cancel(Frame(context, run.event))
        if requeue and run.event.sequence is not None:
            self._pending[run.policy.id].insert(0, run.event)
        self._active = None
        self._record(
            TraceEventName.POLICY_INTERRUPTED,
            policy=run.policy.id,
            reason=reason,
            event_sequence=run.event.sequence,
        )

    def tick(
        self, context: ContextT, *, step: int, events: Iterable[Event] = ()
    ) -> PolicyTick:
        if self._busy or self._faulted:
            raise RuntimeError("Scheduler is busy or faulted; cancel before reuse")
        if type(step) is not int or step < 0 or step <= self._last_step:
            raise ValueError("step must be a strictly increasing nonnegative integer")
        self._busy = True
        try:
            queues, sequence = self._intake(events)
        except BaseException:
            self._busy = False
            raise
        self._last_step = step
        self._pending, self._sequence = queues, sequence
        try:
            self._poll_levels(context)
            if (
                self._active is not None
                and self._active.policy.id in self._levels
                and not self._levels[self._active.policy.id]
            ):
                self._stop(context, reason="event_released", requeue=False)
            candidate = next(
                (policy for policy in self._ordered if self._eligible(policy, step)),
                None,
            )
            if (
                self._active is not None
                and candidate is not None
                and candidate.priority > self._active.policy.priority
            ):
                self._stop(context, reason="higher_priority", requeue=True)
            if self._active is None and candidate is not None:
                event = (
                    self._level_event(candidate)
                    if candidate.id in self._levels
                    else self._pending[candidate.id].pop(0)
                )
                self._active = _Run(candidate, candidate.tree.start(), event)
                self._record(
                    TraceEventName.POLICY_SELECTED,
                    policy=candidate.id,
                    priority=candidate.priority,
                    event=candidate.event,
                    event_sequence=event.sequence,
                )
            run = self._active
            if run is None:
                self._record(TraceEventName.POLICY_IDLE)
                return PolicyTick(None, None)
            result = run.execution.tick(Frame(context, run.event))
            if result != Status.RUNNING:
                self._ready[run.policy.id] = step + run.policy.cooldown_steps + 1
                self._active = None
                self._record(
                    TraceEventName.POLICY_FINISHED,
                    policy=run.policy.id,
                    status=result.value,
                    ready_at=self._ready[run.policy.id],
                )
            return PolicyTick(run.policy.id, result, run.event.sequence)
        except BaseException:
            self._faulted = True
            raise
        finally:
            self._busy = False

    def cancel(self, context: ContextT) -> None:
        """Stop active work and clear fault; pending inputs and level latches remain.

        A cancelled occurrence is consumed. Interrupted occurrences are requeued
        only by higher-priority preemption. Disabling a policy requires rebuilding
        the immutable collection, not cancellation.
        """
        if self._busy:
            raise RuntimeError("Scheduler is busy")
        self._busy = True
        try:
            self._stop(context, reason="explicit_cancel", requeue=False)
            self._record(TraceEventName.POLICY_CANCELLED)
            self._faulted = False
        except BaseException:
            self._faulted = True
            raise
        finally:
            self._busy = False

    @staticmethod
    def _event_data(event: Event) -> dict[str, Any]:
        return {
            "name": event.name,
            "sequence": event.sequence,
            "payload": dict(event.payload),
        }

    def snapshot(self) -> dict[str, Any]:
        if self._busy or self._faulted:
            raise RuntimeError("Cannot checkpoint busy/faulted scheduler")
        run = self._active
        return cast(
            dict[str, Any],
            _json_copy(
                {
                    "version": 1,
                    "scheduler": self.fingerprint,
                    "last_step": self._last_step,
                    "sequence": self._sequence,
                    "pending": {
                        key: [self._event_data(event) for event in queue]
                        for key, queue in self._pending.items()
                    },
                    "levels": self._levels,
                    "ready": self._ready,
                    "active": None
                    if run is None
                    else {
                        "policy": run.policy.id,
                        "event": self._event_data(run.event),
                        "execution": run.execution.snapshot(),
                    },
                }
            ),
        )

    @classmethod
    def restore(
        cls,
        registry: PolicyRegistry[ContextT],
        policies: Iterable[CompiledPolicy[ContextT]],
        data: Mapping[str, Any],
        *,
        max_pending: int = 64,
        trace: TraceSink | None = None,
    ) -> PolicyScheduler[ContextT]:
        scheduler = cls(registry, policies, max_pending=max_pending, trace=trace)
        state = _json_copy(dict(data))
        if (
            set(state)
            != {
                "version",
                "scheduler",
                "last_step",
                "sequence",
                "pending",
                "levels",
                "ready",
                "active",
            }
            or type(state["version"]) is not int
            or state["version"] != 1
            or state["scheduler"] != scheduler.fingerprint
        ):
            raise ValueError("Incompatible scheduler checkpoint")
        if any(
            type(state[key]) is not int or state[key] < -1
            for key in ("last_step", "sequence")
        ):
            raise ValueError("Invalid scheduler step/sequence")
        for key, expected in (
            ("pending", scheduler._pending),
            ("levels", scheduler._levels),
            ("ready", scheduler._ready),
        ):
            if type(state[key]) is not dict or set(state[key]) != set(expected):
                raise ValueError("Invalid scheduler policy state")
        if any(type(value) is not bool for value in state["levels"].values()) or any(
            type(value) is not int or value < 0 for value in state["ready"].values()
        ):
            raise ValueError("Invalid level/cooldown state")

        def decode(row: Any, *, occurrence: bool) -> Event:
            if type(row) is not dict or set(row) != {"name", "sequence", "payload"}:
                raise ValueError("Invalid saved event")
            event = registry.event(
                Event(row["name"], row["sequence"], row["payload"]),
                occurrence=occurrence,
            )
            if event.sequence is not None and event.sequence > state["sequence"]:
                raise ValueError("Saved event exceeds accepted sequence")
            return event

        seen: dict[int, dict[str, Any]] = {}

        def remember(event: Event) -> None:
            assert event.sequence is not None
            encoded = cls._event_data(event)
            if event.sequence in seen and seen[event.sequence] != encoded:
                raise ValueError("Conflicting occurrence payloads")
            seen[event.sequence] = encoded

        queues: dict[str, list[Event]] = {}
        for identity, queue in state["pending"].items():
            if type(queue) is not list or len(queue) > max_pending:
                raise ValueError("Invalid pending occurrence queue")
            policy = scheduler._policies[identity]
            decoded = [decode(row, occurrence=True) for row in queue]
            if decoded and (not policy.enabled or identity in scheduler._levels):
                raise ValueError("Disabled/level policy cannot queue occurrences")
            previous = -1
            for event in decoded:
                assert event.sequence is not None
                if event.name != policy.event or event.sequence <= previous:
                    raise ValueError("Invalid occurrence order/context")
                previous = event.sequence
                remember(event)
            queues[identity] = decoded
        active = state["active"]
        if active is not None:
            if (
                type(active) is not dict
                or set(active) != {"policy", "event", "execution"}
                or not isinstance(active["policy"], str)
                or active["policy"] not in scheduler._policies
            ):
                raise ValueError("Invalid active policy")
            policy = scheduler._policies[active["policy"]]
            if not policy.enabled or state["ready"][policy.id] > state["last_step"]:
                raise ValueError("Active policy is disabled or cooling down")
            event = decode(
                active["event"], occurrence=policy.id not in scheduler._levels
            )
            if event.name != policy.event:
                raise ValueError("Wrong active event context")
            if policy.id in scheduler._levels:
                if not state["levels"][policy.id] or dict(event.payload) != dict(
                    policy.event_parameters
                ):
                    raise ValueError(
                        "Active level event is not latched or has wrong parameters"
                    )
            else:
                assert event.sequence is not None
                if len(queues[policy.id]) >= max_pending or any(
                    item.sequence is None or item.sequence <= event.sequence
                    for item in queues[policy.id]
                ):
                    raise ValueError("Active occurrence order/capacity is inconsistent")
                remember(event)
            execution = Execution.restore(policy.tree, active["execution"])
            if execution.snapshot()["active"] is None:
                raise ValueError("Active policy must contain running work")
            scheduler._active = _Run(policy, execution, event)
        if any(
            not scheduler._policies[identity].enabled and value
            for identity, value in state["levels"].items()
        ):
            raise ValueError("Disabled level policy cannot be latched")
        if state["last_step"] == -1 and (
            state["sequence"] != -1
            or active is not None
            or any(queues.values())
            or any(state["levels"].values())
            or any(state["ready"].values())
        ):
            raise ValueError("Unstarted scheduler contains progress")
        scheduler._pending = queues
        scheduler._levels = state["levels"]
        scheduler._ready = state["ready"]
        scheduler._last_step = state["last_step"]
        scheduler._sequence = state["sequence"]
        return scheduler
