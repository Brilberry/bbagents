"""Canonical decision-tree JSON compiled to the native behavior-tree engine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import operator
import re
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from .behavior_trees import (
    Action,
    ActionHandler,
    BehaviorTree,
    Branch,
    Node,
    Outcome,
    Status,
    _json_copy,
)
from .core import TraceSink
from .policy_registry import (
    ActionSpec,
    Frame,
    PolicyRegistry,
    Scalar,
    UNAVAILABLE,
    Unavailable,
    parameters,
)

ContextT = TypeVar("ContextT")
Expression = Callable[[Frame[ContextT]], bool | Unavailable]
OPERATORS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


def digest(data: Any) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


class _BoundAction(Generic[ContextT]):
    def __init__(
        self, spec: ActionSpec[ContextT], values: Mapping[str, Scalar]
    ) -> None:
        self.spec = spec
        self.values = MappingProxyType(dict(values))

    def tick(self, frame: Frame[ContextT], memory: dict[str, Any]) -> Status:
        if self.spec.precondition is not None:
            allowed = self.spec.precondition(frame, self.values)
            if type(allowed) is not bool:
                raise TypeError("Action precondition must return bool")
            if not allowed:
                self.cancel(frame, memory)
                return Status.FAILURE
        work = memory.setdefault("work", {})
        memory["started"] = True
        return self.spec.handler.tick(frame, self.values, work)

    def cancel(self, frame: Frame[ContextT], memory: dict[str, Any]) -> None:
        if memory.get("started", False):
            self.spec.handler.cancel(frame, self.values, memory["work"])
            memory["started"] = False


@dataclass(frozen=True, slots=True)
class CompiledPolicy(Generic[ContextT]):
    id: str
    event: str
    event_parameters: Mapping[str, Scalar]
    priority: int
    cooldown_steps: int
    enabled: bool
    tree: BehaviorTree[Frame[ContextT]]
    _canonical: str
    fingerprint: str
    registry_fingerprint: str

    def to_data(self) -> dict[str, Any]:
        return json.loads(self._canonical)  # type: ignore[no-any-return]


class DecisionCompiler(Generic[ContextT]):
    """Validate data-only policies against a typed registration snapshot.

    Native node IDs retain decision IDs. Result handlers introduce a generated
    '<id>:operation' leaf; the original ID names its Outcome wrapper. Unknown
    observations are three-valued through AND/OR/NOT; only known true chooses YES.
    No AST includes selectors, executable strings, or callback import paths.
    """

    def __init__(
        self, registry: PolicyRegistry[ContextT], *, trace: TraceSink | None = None
    ) -> None:
        self.registry = registry
        self.trace = trace

    def compile(self, data: Mapping[str, Any]) -> CompiledPolicy[ContextT]:
        required = {"version", "id", "event", "root"}
        if (
            not isinstance(data, Mapping)
            or not required <= set(data)
            or set(data)
            - (required | {"event_parameters", "priority", "cooldown_steps", "enabled"})
        ):
            raise ValueError("Policy requires version, id, event and root")
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError("Expected decision-policy version 1")
        identity, event = self._id(data["id"]), data["event"]
        if not isinstance(event, str) or event not in self.registry.events:
            raise ValueError("Unknown policy event")
        priority, cooldown, enabled = (
            data.get("priority", 0),
            data.get("cooldown_steps", 0),
            data.get("enabled", True),
        )
        if (
            type(priority) is not int
            or type(cooldown) is not int
            or cooldown < 0
            or type(enabled) is not bool
        ):
            raise ValueError(
                "Priority/cooldown must be integers; cooldown nonnegative; enabled bool"
            )
        # Bound and detach data before constructing callbacks.
        data = self._bounded_data(data)
        event_spec = self.registry.events[event]
        event_values = data.get("event_parameters", {})
        if event_spec.kind == "occurrence":
            if event_values != {}:
                raise ValueError(
                    "Occurrence payload is supplied by events, not policies"
                )
        else:
            event_values = parameters(event_spec.fields, event_values)
        nodes: set[str] = set()
        budget = [512]
        conditions: dict[str, Callable[[Frame[ContextT]], bool]] = {}
        actions: dict[str, ActionHandler[Frame[ContextT]]] = {}

        def build(row: Any, depth: int) -> tuple[Node, dict[str, Any]]:
            if not isinstance(row, dict) or depth > 24 or len(nodes) >= 256:
                raise ValueError(
                    "Invalid decision node or policy exceeds 24 levels/256 nodes"
                )
            node_id = self._id(row.get("id"))
            if node_id in nodes:
                raise ValueError(
                    "Decision nodes require unique IDs; shared nodes/cycles are invalid"
                )
            nodes.add(node_id)
            kind = row.get("type")
            if kind == "condition":
                if set(row) != {"id", "type", "condition", "yes", "no"}:
                    raise ValueError(
                        f"Condition {node_id!r} requires condition and YES/NO branches"
                    )
                expression = self._expression(row["condition"], event, budget)
                binding = node_id

                def predicate(frame: Frame[ContextT]) -> bool:
                    return expression(frame) is True

                conditions[binding] = predicate
                yes, yes_data = build(row["yes"], depth + 1)
                no, no_data = build(row["no"], depth + 1)
                return Branch(node_id, binding, yes, no), {
                    "id": node_id,
                    "type": kind,
                    "condition": _json_copy(row["condition"]),
                    "yes": yes_data,
                    "no": no_data,
                }
            if (
                kind != "action"
                or not {"id", "type", "action"} <= set(row)
                or set(row)
                - {"id", "type", "action", "parameters", "success", "failure"}
            ):
                raise ValueError(f"Invalid action node {node_id!r}")
            action_name = row["action"]
            if (
                not isinstance(action_name, str)
                or action_name not in self.registry.actions
            ):
                raise ValueError(f"Unknown action at {node_id!r}")
            spec = self.registry.actions[action_name]
            self._scope(spec.events, event)
            values = parameters(spec.parameters, row.get("parameters", {}))
            binding = node_id
            actions[binding] = _BoundAction(spec, values)
            canonical = {
                "id": node_id,
                "type": kind,
                "action": action_name,
                "parameters": values,
            }
            if "success" not in row and "failure" not in row:
                return Action(node_id, binding), canonical
            outcomes = {}
            for outcome in ("success", "failure"):
                if outcome in row:
                    child, child_data = build(row[outcome], depth + 1)
                    outcomes[outcome] = child
                    canonical[outcome] = child_data
            return Outcome(
                node_id,
                Action(f"{node_id}:operation", binding),
                outcomes.get("success"),
                outcomes.get("failure"),
            ), canonical

        root, canonical_root = build(data["root"], 1)
        canonical = json.dumps(
            {
                "version": 1,
                "id": identity,
                "event": event,
                "event_parameters": event_values,
                "priority": priority,
                "cooldown_steps": cooldown,
                "enabled": enabled,
                "root": canonical_root,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        fingerprint = digest(
            {"registry": self.registry.catalog(), "policy": json.loads(canonical)}
        )
        salt = fingerprint[:24]

        def bind(node: Node) -> Node:
            if isinstance(node, Action):
                return Action(node.id, f"{salt}.{node.operation}")
            if isinstance(node, Branch):
                return Branch(
                    node.id, f"{salt}.{node.predicate}", bind(node.yes), bind(node.no)
                )
            assert isinstance(node, Outcome)
            return Outcome(
                node.id,
                bind(node.child),
                None if node.success is None else bind(node.success),
                None if node.failure is None else bind(node.failure),
            )

        tree = BehaviorTree(
            bind(root),
            actions={f"{salt}.{key}": value for key, value in actions.items()},
            conditions={f"{salt}.{key}": value for key, value in conditions.items()},
            trace=self.trace,
        )
        return CompiledPolicy(
            identity,
            event,
            MappingProxyType(event_values),
            priority,
            cooldown,
            enabled,
            tree,
            canonical,
            fingerprint,
            digest(self.registry.catalog()),
        )

    @staticmethod
    def _id(value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(
            r"[a-zA-Z][a-zA-Z0-9_-]{0,47}", value
        ):
            raise ValueError("Policy/node IDs need 1-48 identifier characters")
        return value

    @staticmethod
    def _scope(scopes: tuple[str, ...], event: str) -> None:
        if scopes and event not in scopes:
            raise ValueError("Attribute/action unavailable in this event context")

    @staticmethod
    def _bounded_data(data: Mapping[str, Any]) -> dict[str, Any]:
        # Bound hostile nesting before JSON copying, including expression input.
        remaining = [16384]

        def visit(value: Any, depth: int) -> None:
            remaining[0] -= 1
            if depth > 80 or remaining[0] < 0:
                raise ValueError("Policy data exceeds nesting/size limits")
            if type(value) is dict:
                for child in value.values():
                    visit(child, depth + 1)
            elif type(value) is list:
                for child in value:
                    visit(child, depth + 1)

        visit(dict(data), 0)
        return _json_copy(dict(data))  # type: ignore[no-any-return]

    def _expression(
        self, row: Any, event: str, budget: list[int], depth: int = 1
    ) -> Expression[ContextT]:
        budget[0] -= 1
        if not isinstance(row, dict) or depth > 24 or budget[0] < 0:
            raise ValueError("Invalid condition or expression exceeds limits")
        kind = row.get("type")
        if kind == "compare":
            if set(row) != {"type", "attribute", "op", "value"}:
                raise ValueError("Comparison needs attribute, op and value")
            attribute = row["attribute"]
            if (
                not isinstance(attribute, str)
                or attribute not in self.registry.attributes
            ):
                raise ValueError("Unknown condition attribute")
            spec = self.registry.attributes[attribute]
            self._scope(spec.events, event)
            op = row["op"]
            if (
                not isinstance(op, str)
                or op not in OPERATORS
                or spec.value.kind != "number"
                and op not in ("==", "!=")
            ):
                raise ValueError("Invalid operator for attribute type")
            value = spec.value.check(row["value"])
            compare = OPERATORS[op]

            def evaluate(frame: Frame[ContextT]) -> bool | Unavailable:
                actual = self.registry.read(attribute, frame)
                return (
                    UNAVAILABLE
                    if actual is UNAVAILABLE
                    else bool(compare(actual, value))
                )

            return evaluate
        if kind == "not":
            if set(row) != {"type", "child"}:
                raise ValueError("NOT requires one child")
            child = self._expression(row["child"], event, budget, depth + 1)

            def negate(frame: Frame[ContextT]) -> bool | Unavailable:
                value = child(frame)
                return UNAVAILABLE if value is UNAVAILABLE else not value

            return negate
        if (
            kind not in ("and", "or")
            or set(row) != {"type", "children"}
            or not isinstance(row["children"], list)
            or not 1 <= len(row["children"]) <= 64
        ):
            raise ValueError("AND/OR require 1-64 child expressions")
        children = tuple(
            self._expression(child, event, budget, depth + 1)
            for child in row["children"]
        )

        def combine(frame: Frame[ContextT]) -> bool | Unavailable:
            unknown = False
            for child in children:
                value = child(frame)
                if kind == "and" and value is False:
                    return False
                if kind == "or" and value is True:
                    return True
                unknown |= value is UNAVAILABLE
            return UNAVAILABLE if unknown else kind == "and"

        return combine
