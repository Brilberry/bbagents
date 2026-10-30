"""Typed, domain-neutral policy catalogs and trusted application adapters."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Generic, Literal, Protocol, TypeVar, cast

from .behavior_trees import Status

Scalar = str | bool | int | float
ValueKind = Literal["number", "boolean", "string"]
ContextT = TypeVar("ContextT")
ContextCo = TypeVar("ContextCo", covariant=True)
HandlerContextT = TypeVar("HandlerContextT", contravariant=True)


class Unavailable(Enum):
    VALUE = "unavailable"


UNAVAILABLE = Unavailable.VALUE


def name(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("Registry names must be nonblank strings <=128 characters")


@dataclass(frozen=True, slots=True)
class ValueSpec:
    kind: ValueKind
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Scalar, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in ("number", "boolean", "string"):
            raise ValueError("Unknown value kind")
        for bound in (self.minimum, self.maximum):
            if bound is not None and (
                self.kind != "number"
                or type(bound) not in (int, float)
                or not math.isfinite(bound)
            ):
                raise ValueError("Bounds require finite numeric values")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Minimum exceeds maximum")
        if type(self.choices) is not tuple:
            raise TypeError("choices must be a tuple")
        for value in self.choices:
            self.check(value, choices=False)
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("Duplicate value choices")

    def check(self, value: object, *, choices: bool = True) -> Scalar:
        valid = (
            (
                self.kind == "number"
                and type(value) in (int, float)
                and math.isfinite(cast(float, value))
            )
            or (self.kind == "boolean" and type(value) is bool)
            or (self.kind == "string" and type(value) is str)
        )
        if not valid:
            raise ValueError(f"Expected {self.kind} value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if (
                self.minimum is not None
                and value < self.minimum
                or self.maximum is not None
                and value > self.maximum
            ):
                raise ValueError("Value outside supported range")
        if choices and self.choices and value not in self.choices:
            raise ValueError("Unsupported value choice")
        assert isinstance(value, (str, bool, int, float))
        return value

    def to_data(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": list(self.choices),
        }


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    name: str
    value: ValueSpec
    required: bool = True
    default: Scalar | None = None

    def __post_init__(self) -> None:
        name(self.name)
        if not isinstance(self.value, ValueSpec) or type(self.required) is not bool:
            raise TypeError("Invalid parameter specification")
        if self.required and self.default is not None:
            raise ValueError("Required parameters cannot have defaults")
        if not self.required:
            self.value.check(self.default)

    def to_data(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value.to_data(),
            "required": self.required,
            **({"default": self.default} if not self.required else {}),
        }


def parameters(
    specs: tuple[ParameterSpec, ...], values: Mapping[str, Any]
) -> dict[str, Scalar]:
    if not isinstance(values, Mapping) or set(values) - {spec.name for spec in specs}:
        raise ValueError("Unknown parameter or invalid parameter object")
    result = {}
    for spec in specs:
        if spec.name not in values and spec.required:
            raise ValueError(f"Missing parameter {spec.name!r}")
        result[spec.name] = spec.value.check(values.get(spec.name, spec.default))
    return result


@dataclass(frozen=True, slots=True)
class Event:
    """Occurrence sequence is supplied by the application's ordered event stream."""

    name: str
    sequence: int | None = None
    payload: Mapping[str, Scalar] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Frame(Generic[ContextCo]):
    context: ContextCo
    event: Event


@dataclass(frozen=True, slots=True)
class AttributeSpec(Generic[ContextT]):
    name: str
    value: ValueSpec
    read: Callable[[Frame[ContextT]], Scalar | Unavailable]
    label: str = ""
    description: str = ""
    events: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EventSpec(Generic[ContextT]):
    name: str
    label: str
    kind: Literal["level", "occurrence"]
    fields: tuple[ParameterSpec, ...] = ()
    active: Callable[[Frame[ContextT]], bool] | None = None
    release: Callable[[Frame[ContextT]], bool] | None = None
    description: str = ""


class ParameterizedAction(Protocol[HandlerContextT]):
    def tick(
        self,
        frame: Frame[HandlerContextT],
        parameters: Mapping[str, Scalar],
        memory: dict[str, Any],
    ) -> Status: ...
    def cancel(
        self,
        frame: Frame[HandlerContextT],
        parameters: Mapping[str, Scalar],
        memory: dict[str, Any],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class EnergyCost:
    amount: float = 0
    mode: Literal["per_action", "per_interval", "recovery"] = "per_action"

    def __post_init__(self) -> None:
        if (
            type(self.amount) not in (int, float)
            or not math.isfinite(self.amount)
            or self.amount < 0
            or self.mode not in ("per_action", "per_interval", "recovery")
        ):
            raise ValueError(
                "Energy metadata needs nonnegative finite amount and supported mode"
            )


@dataclass(frozen=True, slots=True)
class ActionSpec(Generic[ContextT]):
    name: str
    label: str
    handler: ParameterizedAction[ContextT]
    parameters: tuple[ParameterSpec, ...] = ()
    events: tuple[str, ...] = ()
    precondition: Callable[[Frame[ContextT], Mapping[str, Scalar]], bool] | None = None
    energy: EnergyCost = EnergyCost()
    duration: str = "application-defined"
    description: str = ""
    success: str = "Adapter reports success"
    failure: str = "Precondition fails or adapter reports failure"
    effects: str = "Application-owned"


class PolicyRegistry(Generic[ContextT]):
    """Immutable registration snapshot; callbacks are trusted, not sandboxed.

    Attribute readers must use the actor's observations. UNAVAILABLE propagates
    through boolean expressions, including NOT; missing knowledge is not false.
    The registry cannot stop a malicious reader from inspecting hidden world data.
    Version callback semantics explicitly when changing application adapters.
    """

    def __init__(
        self,
        *,
        version: str,
        events: Iterable[EventSpec[ContextT]],
        attributes: Iterable[AttributeSpec[ContextT]],
        actions: Iterable[ActionSpec[ContextT]],
    ) -> None:
        name(version)
        self.version = version
        self.events: Mapping[str, EventSpec[ContextT]] = MappingProxyType(
            self._register(events, EventSpec)
        )
        self.attributes: Mapping[str, AttributeSpec[ContextT]] = MappingProxyType(
            self._register(attributes, AttributeSpec)
        )
        self.actions: Mapping[str, ActionSpec[ContextT]] = MappingProxyType(
            self._register(actions, ActionSpec)
        )
        for event in self.events.values():
            self._parameters(event.fields)
            if event.kind not in ("level", "occurrence"):
                raise ValueError("Unknown event kind")
            if event.kind == "level":
                if not callable(event.active):
                    raise ValueError("Level events need an active predicate")
            elif event.active is not None or event.release is not None:
                raise ValueError("Occurrence events cannot have level predicates")
            if event.release is not None and not callable(event.release):
                raise TypeError("Event release must be callable")
        for attribute in self.attributes.values():
            if not isinstance(attribute.value, ValueSpec) or not callable(
                attribute.read
            ):
                raise TypeError("Attribute needs typed value and reader")
            self._scopes(attribute.events)
        for action in self.actions.values():
            self._parameters(action.parameters)
            self._scopes(action.events)
            if not callable(getattr(action.handler, "tick", None)) or not callable(
                getattr(action.handler, "cancel", None)
            ):
                raise TypeError("Action handler needs tick and cancel methods")
            if action.precondition is not None and not callable(action.precondition):
                raise TypeError("Action precondition must be callable")
            if not isinstance(action.energy, EnergyCost):
                raise TypeError("Action energy must be EnergyCost")

    @staticmethod
    def _register(rows: Iterable[Any], expected: type[Any]) -> dict[str, Any]:
        result = {}
        for row in rows:
            if not isinstance(row, expected):
                raise TypeError("Unexpected registry entry")
            name(row.name)
            if row.name in result:
                raise ValueError(f"Duplicate registration {row.name!r}")
            result[row.name] = row
        return result

    @staticmethod
    def _parameters(rows: tuple[ParameterSpec, ...]) -> None:
        if (
            type(rows) is not tuple
            or any(not isinstance(row, ParameterSpec) for row in rows)
            or len({row.name for row in rows}) != len(rows)
        ):
            raise ValueError("Parameters need a tuple of unique ParameterSpec entries")

    def _scopes(self, events: tuple[str, ...]) -> None:
        if (
            type(events) is not tuple
            or any(
                not isinstance(event, str) or event not in self.events
                for event in events
            )
            or len(set(events)) != len(events)
        ):
            raise ValueError("Invalid event context restrictions")

    def event(self, value: Event, *, occurrence: bool) -> Event:
        if (
            not isinstance(value, Event)
            or not isinstance(value.name, str)
            or value.name not in self.events
        ):
            raise ValueError("Unknown event")
        spec = self.events[value.name]
        if occurrence != (spec.kind == "occurrence"):
            raise ValueError("Wrong event kind")
        if occurrence and (type(value.sequence) is not int or value.sequence < 0):
            raise ValueError("Occurrences require a nonnegative integer sequence")
        if not occurrence and value.sequence is not None:
            raise ValueError("Level events have no occurrence sequence")
        return Event(
            value.name,
            value.sequence,
            MappingProxyType(parameters(spec.fields, value.payload)),
        )

    def read(self, identity: str, frame: Frame[ContextT]) -> Scalar | Unavailable:
        spec = self.attributes[identity]
        result = spec.read(frame)
        return result if result is UNAVAILABLE else spec.value.check(result)

    def catalog(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "events": [
                {
                    "name": spec.name,
                    "label": spec.label,
                    "kind": spec.kind,
                    "description": spec.description,
                    "fields": [field.to_data() for field in spec.fields],
                }
                for spec in self.events.values()
            ],
            "attributes": [
                {
                    "name": spec.name,
                    "label": spec.label,
                    "description": spec.description,
                    "value": spec.value.to_data(),
                    "events": list(spec.events),
                }
                for spec in self.attributes.values()
            ],
            "actions": [
                {
                    "name": spec.name,
                    "label": spec.label,
                    "description": spec.description,
                    "parameters": [param.to_data() for param in spec.parameters],
                    "events": list(spec.events),
                    "energy": {"amount": spec.energy.amount, "mode": spec.energy.mode},
                    "duration": spec.duration,
                    "success": spec.success,
                    "failure": spec.failure,
                    "effects": spec.effects,
                }
                for spec in self.actions.values()
            ],
            "operators": {
                "number": [">", "<", ">=", "<=", "==", "!="],
                "boolean": ["==", "!="],
                "string": ["==", "!="],
            },
            "boolean_operators": ["and", "or", "not"],
        }
