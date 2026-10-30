"""Deterministic, synchronous behavior trees with application-owned action adapters.

Definitions are reusable; each actor needs a separate Execution. No clock, RNG,
workers, tool authorization, or model calls are supplied by this module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Generic, Protocol, TypeVar, cast

from .core import NullTrace, TraceSink
from .observability import TraceEventName

ContextT = TypeVar("ContextT")
HandlerContextT = TypeVar("HandlerContextT", contravariant=True)


class Status(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    RUNNING = "running"


class ActionHandler(Protocol[HandlerContextT]):
    """Trusted bounded adapter; all execution must use ordinary authorization.

    tick is called once per active leaf visit. memory belongs to this execution
    and must remain JSON data for checkpointing. cancel must release outstanding
    work; it does not undo completed world effects. It may be retried after errors.
    """

    def tick(self, context: HandlerContextT, memory: dict[str, Any]) -> Status: ...

    def cancel(self, context: HandlerContextT, memory: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    operation: str


@dataclass(frozen=True, slots=True)
class Condition:
    id: str
    predicate: str


@dataclass(frozen=True, slots=True)
class Selector:
    id: str
    children: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Sequence:
    id: str
    children: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Inverter:
    id: str
    child: Node


@dataclass(frozen=True, slots=True)
class Branch:
    """Choose exactly one arm; pin the choice until completion or cancellation.

    Failure in the chosen arm is returned unchanged, never interpreted as a
    reason to try the opposite arm. This is the safe decision-compiler primitive.
    """

    id: str
    predicate: str
    yes: Node
    no: Node


@dataclass(frozen=True, slots=True)
class Outcome:
    """Run an explicit result handler, without retrying the completed child."""

    id: str
    child: Node
    success: Node | None = None
    failure: Node | None = None


Node = Action | Condition | Selector | Sequence | Inverter | Branch | Outcome


class BehaviorTreeError(RuntimeError):
    """Callback execution failed; cancel before attempting another tick."""


def _name(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("Node and registry names must be nonblank strings <=128 chars")


def _children(node: Node) -> tuple[Node, ...]:
    if isinstance(node, (Selector, Sequence)):
        return node.children
    if isinstance(node, Inverter):
        return (node.child,)
    if isinstance(node, Branch):
        return (node.yes, node.no)
    if isinstance(node, Outcome):
        return (node.child,) + tuple(
            child for child in (node.success, node.failure) if child is not None
        )
    return ()


def _encode(node: Node) -> dict[str, Any]:
    data: dict[str, Any] = {"id": node.id, "type": type(node).__name__.lower()}
    if isinstance(node, Action):
        data["operation"] = node.operation
    elif isinstance(node, Condition):
        data["predicate"] = node.predicate
    elif isinstance(node, (Selector, Sequence)):
        data["children"] = [_encode(child) for child in node.children]
    elif isinstance(node, Inverter):
        data["child"] = _encode(node.child)
    elif isinstance(node, Branch):
        data.update(
            predicate=node.predicate, yes=_encode(node.yes), no=_encode(node.no)
        )
    else:
        data.update(
            child=_encode(node.child),
            success=None if node.success is None else _encode(node.success),
            failure=None if node.failure is None else _encode(node.failure),
        )
    return data


def _decode(data: Any, depth: int = 1, budget: list[int] | None = None) -> Node:
    budget = [1024] if budget is None else budget
    budget[0] -= 1
    if depth > 64 or budget[0] < 0 or not isinstance(data, dict):
        raise ValueError("Invalid tree node or tree exceeds 64 levels/1024 nodes")
    kind = data.get("type")
    fields = {
        "action": {"operation"},
        "condition": {"predicate"},
        "selector": {"children"},
        "sequence": {"children"},
        "inverter": {"child"},
        "branch": {"predicate", "yes", "no"},
        "outcome": {"child", "success", "failure"},
    }
    if (
        not isinstance(kind, str)
        or kind not in fields
        or set(data) != {"id", "type"} | fields[kind]
    ):
        raise ValueError("Unknown node type or invalid fields")
    identity = data["id"]
    _name(identity)
    if kind == "action":
        return Action(identity, data["operation"])
    if kind == "condition":
        return Condition(identity, data["predicate"])
    if kind in {"selector", "sequence"}:
        rows = data["children"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1024:
            raise ValueError("Composite requires 1-1024 children")
        children = tuple(_decode(row, depth + 1, budget) for row in rows)
        return (
            Selector(identity, children)
            if kind == "selector"
            else Sequence(identity, children)
        )
    if kind == "inverter":
        return Inverter(identity, _decode(data["child"], depth + 1, budget))
    if kind == "outcome":
        return Outcome(
            identity,
            _decode(data["child"], depth + 1, budget),
            None
            if data["success"] is None
            else _decode(data["success"], depth + 1, budget),
            None
            if data["failure"] is None
            else _decode(data["failure"], depth + 1, budget),
        )
    return Branch(
        identity,
        data["predicate"],
        _decode(data["yes"], depth + 1, budget),
        _decode(data["no"], depth + 1, budget),
    )


def _json_copy(value: Any) -> Any:
    """Reject non-JSON data rather than silently changing checkpoint semantics."""

    def validate(item: Any) -> None:
        if item is None or type(item) in (str, bool, int, float):
            return
        if type(item) is list:
            for entry in item:
                validate(entry)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for entry in item.values():
                validate(entry)
            return
        raise ValueError("Action memory must contain only JSON data")

    validate(value)
    return json.loads(json.dumps(value, allow_nan=False))


class BehaviorTree(Generic[ContextT]):
    """Validated reusable definition, with a private snapshot of callback bindings.

    Sequence and Selector remember their running child. They are not reactive:
    the caller's scheduler must cancel on higher-priority events or invalidation.
    Handler objects may be shared; actor-specific mutable state belongs in memory.
    """

    def __init__(
        self,
        root: Node,
        *,
        actions: Mapping[str, ActionHandler[ContextT]],
        conditions: Mapping[str, Callable[[ContextT], bool]],
        trace: TraceSink | None = None,
    ) -> None:
        self._nodes: dict[str, Node] = {}

        def visit(node: Node, depth: int) -> None:
            if not isinstance(
                node, (Action, Condition, Selector, Sequence, Inverter, Branch, Outcome)
            ):
                raise TypeError("Unsupported behavior-tree node")
            _name(node.id)
            if depth > 64 or len(self._nodes) >= 1024:
                raise ValueError("Tree exceeds 64 levels or 1024 nodes")
            if node.id in self._nodes:
                raise ValueError(
                    "Node IDs must be unique; shared nodes/cycles are invalid"
                )
            self._nodes[node.id] = node
            if isinstance(node, (Selector, Sequence)) and (
                type(node.children) is not tuple or not node.children
            ):
                raise ValueError("Composite children must be a nonempty tuple")
            if isinstance(node, Action):
                _name(node.operation)
                handler = actions.get(node.operation)
                if not callable(getattr(handler, "tick", None)) or not callable(
                    getattr(handler, "cancel", None)
                ):
                    raise ValueError(
                        f"Action {node.operation!r} needs tick and cancel handlers"
                    )
            if isinstance(node, (Condition, Branch)):
                _name(node.predicate)
                if not callable(conditions.get(node.predicate)):
                    raise ValueError(f"Unknown condition {node.predicate!r}")
            for child in _children(node):
                visit(child, depth + 1)

        visit(root, 1)
        self._root = root
        self._actions = dict(actions)
        self._conditions = dict(conditions)
        self._trace = NullTrace() if trace is None else trace
        if not callable(getattr(self._trace, "record", None)):
            raise TypeError("trace must provide record()")
        self.fingerprint = hashlib.sha256(
            json.dumps(self.to_data(), sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    def to_data(self) -> dict[str, Any]:
        return {"version": 1, "root": _encode(self._root)}

    @classmethod
    def from_data(
        cls,
        data: Mapping[str, Any],
        *,
        actions: Mapping[str, ActionHandler[ContextT]],
        conditions: Mapping[str, Callable[[ContextT], bool]],
        trace: TraceSink | None = None,
    ) -> BehaviorTree[ContextT]:
        if (
            set(data) != {"version", "root"}
            or type(data["version"]) is not int
            or data["version"] != 1
        ):
            raise ValueError("Expected behavior-tree version 1 and root")
        return cls(
            _decode(data["root"]), actions=actions, conditions=conditions, trace=trace
        )

    def start(self) -> Execution[ContextT]:
        return Execution(self)


class Execution(Generic[ContextT]):
    """One actor's state. Never concurrently tick/cancel the same execution.

    Terminal ticks clear progress; the next tick starts a new traversal. Callbacks
    and trace errors fail closed. Cancellation must succeed before execution can
    resume. Snapshots contain data only, never callbacks or application context.
    """

    def __init__(self, tree: BehaviorTree[ContextT]) -> None:
        self.tree = tree
        self._cursors: dict[str, int] = {}
        self._branches: dict[str, bool] = {}
        self._outcomes: dict[str, str] = {}
        self._memory: dict[str, dict[str, Any]] = {}
        self._active: str | None = None
        self._faulted = False
        self._busy = False
        self._step = 0

    def _record(self, event: TraceEventName, **fields: Any) -> None:
        self.tree._trace.record(
            event, {"tree": self.tree.fingerprint, "step": self._step, **fields}
        )

    def tick(self, context: ContextT) -> Status:
        if self._busy or self._faulted:
            raise RuntimeError("Execution is busy or faulted; cancel before retrying")
        self._busy = True
        self._step += 1
        try:
            result = self._visit(self.tree._root, context)
            self._record(TraceEventName.TREE_TICK_FINISHED, status=result.value)
            return result
        except BaseException:
            self._faulted = True
            raise
        finally:
            self._busy = False

    def _predicate(self, node: Condition | Branch, context: ContextT) -> bool:
        try:
            result = self.tree._conditions[node.predicate](context)
            if type(result) is not bool:
                raise TypeError("Condition must return bool")
        except Exception as exc:
            self._record(
                TraceEventName.TREE_FAILED,
                node=node.id,
                stage="condition",
                error_type=type(exc).__name__,
            )
            raise BehaviorTreeError(
                f"Condition {node.id!r} raised {type(exc).__name__}"
            ) from exc
        self._record(
            TraceEventName.TREE_CONDITION,
            node=node.id,
            predicate=node.predicate,
            value=result,
        )
        return result

    def _visit(self, node: Node, context: ContextT) -> Status:
        self._record(
            TraceEventName.TREE_NODE_ENTERED,
            node=node.id,
            kind=type(node).__name__.lower(),
        )
        if isinstance(node, Action):
            memory = self._memory.setdefault(node.id, {})
            self._active = node.id
            try:
                result = self.tree._actions[node.operation].tick(context, memory)
                if not isinstance(result, Status):
                    raise TypeError("Action must return Status")
            except Exception as exc:
                self._record(
                    TraceEventName.TREE_FAILED,
                    node=node.id,
                    stage="action",
                    error_type=type(exc).__name__,
                )
                raise BehaviorTreeError(
                    f"Action {node.id!r} raised {type(exc).__name__}"
                ) from exc
            if result != Status.RUNNING:
                self._active = None
                self._memory.pop(node.id, None)
        elif isinstance(node, Condition):
            result = (
                Status.SUCCESS if self._predicate(node, context) else Status.FAILURE
            )
        elif isinstance(node, Inverter):
            child_result = self._visit(node.child, context)
            result = {
                Status.SUCCESS: Status.FAILURE,
                Status.FAILURE: Status.SUCCESS,
                Status.RUNNING: Status.RUNNING,
            }[child_result]
        elif isinstance(node, Branch):
            if node.id not in self._branches:
                self._branches[node.id] = self._predicate(node, context)
            yes = self._branches[node.id]
            self._record(
                TraceEventName.TREE_BRANCH, node=node.id, branch="yes" if yes else "no"
            )
            result = self._visit(node.yes if yes else node.no, context)
            if result != Status.RUNNING:
                self._branches.pop(node.id, None)
        elif isinstance(node, Outcome):
            previous = self._outcomes.get(node.id)
            result = (
                Status(previous)
                if previous is not None
                else self._visit(node.child, context)
            )
            if result != Status.RUNNING:
                selected = node.success if result == Status.SUCCESS else node.failure
                if selected is not None:
                    self._outcomes[node.id] = result.value
                    self._record(
                        TraceEventName.TREE_OUTCOME, node=node.id, outcome=result.value
                    )
                    result = self._visit(selected, context)
                if result != Status.RUNNING:
                    self._outcomes.pop(node.id, None)
        else:
            index = self._cursors.get(node.id, 0)
            advance_on = (
                Status.FAILURE if isinstance(node, Selector) else Status.SUCCESS
            )
            result = advance_on
            while index < len(node.children):
                result = self._visit(node.children[index], context)
                if result == Status.RUNNING:
                    self._cursors[node.id] = index
                    break
                if result != advance_on:
                    break
                index += 1
            if result != Status.RUNNING:
                self._cursors.pop(node.id, None)
        self._record(
            TraceEventName.TREE_NODE_FINISHED, node=node.id, status=result.value
        )
        return result

    def cancel(self, context: ContextT) -> None:
        """Cancel active work then reset. On error retain state for cleanup retry."""
        if self._busy:
            raise RuntimeError("Execution is busy")
        self._busy = True
        try:
            if self._active is not None:
                node = self.tree._nodes[self._active]
                assert isinstance(node, Action)
                try:
                    self.tree._actions[node.operation].cancel(
                        context, self._memory[node.id]
                    )
                except Exception as exc:
                    self._record(
                        TraceEventName.TREE_FAILED,
                        node=node.id,
                        stage="cancel",
                        error_type=type(exc).__name__,
                    )
                    raise BehaviorTreeError(
                        f"Cancellation {node.id!r} raised {type(exc).__name__}"
                    ) from exc
                self._active = None
            self._cursors.clear()
            self._branches.clear()
            self._outcomes.clear()
            self._memory.clear()
            self._record(TraceEventName.TREE_CANCELLED)
            self._faulted = False
        except BaseException:
            self._faulted = True
            raise
        finally:
            self._busy = False

    def snapshot(self) -> dict[str, Any]:
        if self._busy or self._faulted:
            raise RuntimeError("Cannot checkpoint busy or faulted execution")
        return cast(
            dict[str, Any],
            _json_copy(
                {
                    "version": 1,
                    "tree": self.tree.fingerprint,
                    "step": self._step,
                    "cursors": self._cursors,
                    "branches": self._branches,
                    "memory": self._memory,
                    "active": self._active,
                    **({"outcomes": self._outcomes} if self._outcomes else {}),
                }
            ),
        )

    @classmethod
    def restore(
        cls, tree: BehaviorTree[ContextT], data: Mapping[str, Any]
    ) -> Execution[ContextT]:
        """Validate a checkpoint against its definition, including its active path.

        Fingerprints cover the definition, not callback code. Applications must
        version/pin handler semantics and authenticate checkpoints themselves.
        """
        state = _json_copy(dict(data))
        if (
            set(state) - {"outcomes"}
            != {"version", "tree", "step", "cursors", "branches", "memory", "active"}
            or type(state["version"]) is not int
            or state["version"] != 1
            or state["tree"] != tree.fingerprint
        ):
            raise ValueError("Incompatible behavior-tree checkpoint")
        state.setdefault("outcomes", {})
        if (
            type(state["step"]) is not int
            or state["step"] < 0
            or any(
                type(state[key]) is not dict
                for key in ("cursors", "branches", "memory", "outcomes")
            )
        ):
            raise ValueError("Invalid checkpoint state")
        expected_cursors: set[str] = set()
        expected_branches: set[str] = set()
        expected_outcomes: set[str] = set()
        active = state["active"]
        if active is not None and (
            not isinstance(active, str)
            or not isinstance(tree._nodes.get(active), Action)
        ):
            raise ValueError("Checkpoint active node must be an action")
        node = tree._root
        if active is not None:
            while node.id != active:
                if isinstance(node, (Selector, Sequence)):
                    index = state["cursors"].get(node.id)
                    if type(index) is not int or not 0 <= index < len(node.children):
                        raise ValueError("Invalid active composite cursor")
                    expected_cursors.add(node.id)
                    node = node.children[index]
                elif isinstance(node, Branch):
                    yes = state["branches"].get(node.id)
                    if type(yes) is not bool:
                        raise ValueError("Invalid active decision branch")
                    expected_branches.add(node.id)
                    node = node.yes if yes else node.no
                elif isinstance(node, Inverter):
                    node = node.child
                elif isinstance(node, Outcome):
                    outcome = state["outcomes"].get(node.id)
                    if outcome is None:
                        node = node.child
                    else:
                        if outcome not in ("success", "failure"):
                            raise ValueError("Invalid active action outcome")
                        expected_outcomes.add(node.id)
                        selected = (
                            node.success if outcome == "success" else node.failure
                        )
                        if selected is None:
                            raise ValueError("Missing active outcome handler")
                        node = selected
                else:
                    raise ValueError("Active action is not on checkpoint path")
        if (
            set(state["cursors"]) != expected_cursors
            or set(state["branches"]) != expected_branches
            or set(state["outcomes"]) != expected_outcomes
            or set(state["memory"]) != ({active} if active else set())
            or any(type(value) is not dict for value in state["memory"].values())
        ):
            raise ValueError("Checkpoint contains inconsistent execution progress")
        execution = cls(tree)
        execution._step = state["step"]
        execution._cursors = state["cursors"]
        execution._branches = state["branches"]
        execution._outcomes = state["outcomes"]
        execution._memory = state["memory"]
        execution._active = active
        return execution
