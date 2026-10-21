"""Versioned application-state persistence contracts and in-memory reference store."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Generic, Mapping, Protocol, TypeVar

T = TypeVar("T")
FILE_STATE_FORMAT_VERSION = 1


class VersionedRecord(Protocol):
    """A record with a stable identity and optimistic-concurrency version."""

    version: int


class ConcurrentWriteError(RuntimeError):
    """Raised when an optimistic-concurrency version no longer matches."""


@dataclass(frozen=True, slots=True)
class StateSnapshot(Generic[T]):
    """A versioned application-owned state value."""

    key: str
    value: T
    version: int
    saved_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, Any] = field(default_factory=dict)


class StateStore(Protocol[T]):
    """Persists application state; implementations choose encoding and storage."""

    def load(self, key: str) -> StateSnapshot[T] | None: ...

    def save(
        self,
        key: str,
        value: T,
        *,
        expected_version: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> StateSnapshot[T]: ...


class InMemoryStateStore(Generic[T]):
    """Reference store with optimistic concurrency, suitable for tests and demos."""

    def __init__(self) -> None:
        self._snapshots: dict[str, StateSnapshot[T]] = {}

    def load(self, key: str) -> StateSnapshot[T] | None:
        return self._snapshots.get(key)

    def save(
        self,
        key: str,
        value: T,
        *,
        expected_version: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> StateSnapshot[T]:
        existing = self._snapshots.get(key)
        actual_version = existing.version if existing is not None else 0
        if expected_version is not None and expected_version != actual_version:
            raise ConcurrentWriteError(
                f"State {key!r} is at version {actual_version}, not {expected_version}"
            )
        snapshot = StateSnapshot(
            key,
            value,
            version=actual_version + 1,
            metadata=dict(metadata or {}),
        )
        self._snapshots[key] = snapshot
        return snapshot


class FileStateStore(Generic[T]):
    """JSON-backed reference store with atomic writes and tolerant reads.

    Corrupt, incomplete, or incompatible files are treated as absent state. This
    lets an application recover from a lost checkpoint rather than fail at startup.
    Applications that store non-JSON values can supply matching encode/decode
    functions; a decode failure receives the same absent-state treatment.
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        encode: Callable[[T], Any] | None = None,
        decode: Callable[[Any], T] | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.encode = encode or _identity
        self.decode = decode or _identity

    def load(self, key: str) -> StateSnapshot[T] | None:
        path = self._path_for(key)
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            return self._snapshot_from_payload(key, payload)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError, TypeError, ValueError, KeyError):
            return None

    def save(
        self,
        key: str,
        value: T,
        *,
        expected_version: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> StateSnapshot[T]:
        existing = self.load(key)
        actual_version = existing.version if existing is not None else 0
        if expected_version is not None and expected_version != actual_version:
            raise ConcurrentWriteError(
                f"State {key!r} is at version {actual_version}, not {expected_version}"
            )
        snapshot = StateSnapshot(
            key,
            value,
            version=actual_version + 1,
            metadata=dict(metadata or {}),
        )
        self._write(self._path_for(key), self._payload_for(snapshot))
        return snapshot

    def _snapshot_from_payload(
        self,
        key: str,
        payload: Mapping[str, Any],
    ) -> StateSnapshot[T]:
        if payload["format_version"] != FILE_STATE_FORMAT_VERSION:
            raise ValueError("unsupported state file format")
        if payload["key"] != key:
            raise ValueError("state file key does not match requested key")
        version = payload["version"]
        if not isinstance(version, int) or version < 1:
            raise ValueError("state file version must be a positive integer")
        saved_at = datetime.fromisoformat(payload["saved_at"])
        if saved_at.tzinfo is None:
            raise ValueError("state file timestamp must include a timezone")
        metadata = payload["metadata"]
        if not isinstance(metadata, Mapping):
            raise TypeError("state file metadata must be a mapping")
        return StateSnapshot(
            key,
            self.decode(payload["value"]),
            version,
            saved_at=saved_at,
            metadata=metadata,
        )

    def _payload_for(self, snapshot: StateSnapshot[T]) -> dict[str, Any]:
        return {
            "format_version": FILE_STATE_FORMAT_VERSION,
            "key": snapshot.key,
            "value": self.encode(snapshot.value),
            "version": snapshot.version,
            "saved_at": snapshot.saved_at.isoformat(),
            "metadata": dict(snapshot.metadata),
        }

    def _path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    def _write(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)


def _identity(value: T) -> T:
    return value
