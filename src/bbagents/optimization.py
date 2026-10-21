"""Caching and compatibility helpers for model selection."""

from __future__ import annotations

from typing import Generic, Hashable, Iterable, TypeVar

from .models import (
    CheapestEligibleModelSelector,
    ModelCandidate,
    ModelProfile,
    SelectionConstraints,
)

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


def route_model(
    profiles: Iterable[ModelProfile],
    *,
    minimum_quality: float,
    maximum_latency: float | None = None,
) -> ModelProfile:
    """Compatibility wrapper around the canonical model-selector contract."""

    candidates = tuple(
        ModelCandidate(profile.name, profile) for profile in profiles
    )
    selected = CheapestEligibleModelSelector().select(
        candidates,
        SelectionConstraints(
            minimum_quality=minimum_quality,
            maximum_latency_seconds=maximum_latency,
        ),
    )
    return selected.profile


class Cache(Generic[K, V]):
    def __init__(self) -> None:
        self._values: dict[K, V] = {}

    def get(self, key: K) -> V | None:
        return self._values.get(key)

    def put(self, key: K, value: V) -> None:
        self._values[key] = value
