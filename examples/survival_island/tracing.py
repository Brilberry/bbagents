"""Framework trace sink with disk journals and a bounded recent-event window."""

from collections import deque, OrderedDict
from dataclasses import replace
import json
from pathlib import Path
import re
from threading import RLock

from bbagents.observability import InMemoryTrace, TraceEvent


def row_for(event):
    return {"event": getattr(event.name, "value", event.name),
            "timestamp": event.timestamp.isoformat(), "run_id": event.context.run_id,
            "span_id": event.context.span_id, "parent_span_id": event.context.parent_span_id,
            **dict(event.attributes)}


class IslandTrace(InMemoryTrace):
    def __init__(self, simulation):
        super().__init__()
        self.simulation = simulation
        self.lock = RLock()
        self.directory = None
        self.handles = OrderedDict()
        self.closed = False

    def record(self, event, payload=None):
        with self.lock:
            metadata = {"tick": self.simulation.tick, "recorded_tick": self.simulation.tick}
            if isinstance(event, TraceEvent):
                event = replace(event, attributes={**metadata, **event.attributes})
            else:
                payload = {**metadata, **(payload or {})}
            super().record(event, payload)
            if self.directory:
                self._write(self.events[-1])
                cutoff = self.simulation.tick - 199
                while self.events and self.events[0].attributes.get("recorded_tick", 0) < cutoff:
                    self.events.popleft()

    def _write(self, event):
        if self.closed:
            return
        row = row_for(event)
        names = {"trace"}
        names.update(value for value in (row.get("agent"), row.get("recipient"))
                     if isinstance(value, str) and re.fullmatch(r"agent-\d+", value))
        line = json.dumps(row, default=str) + "\n"
        for name in names:
            if name not in self.handles:
                if len(self.handles) >= 64:
                    _, old = self.handles.popitem(last=False)
                    old.close()
                self.handles[name] = (self.directory / f"{name}.jsonl").open("a", encoding="utf-8")
            self.handles[name].write(line)
            self.handles.move_to_end(name)

    def enable_journal(self, directory):
        with self.lock:
            if self.directory:
                raise ValueError("trace journal already enabled")
            self.directory = Path(directory)
            self.directory.mkdir(parents=True, exist_ok=True)
            for event in self.events:
                self._write(event)
            cutoff = self.simulation.tick - 199
            self.events = deque((event for event in self.events
                                 if event.attributes.get("recorded_tick", 0) >= cutoff), maxlen=30000)
            self.flush()

    def flush(self):
        with self.lock:
            for handle in self.handles.values():
                if not handle.closed:
                    handle.flush()

    def iter_rows(self, agent_id=None):
        if self.directory:
            with self.lock:
                self.flush()
                path = self.directory / f"{agent_id or 'trace'}.jsonl"
                limit = path.stat().st_size if path.exists() else 0
            if path.exists():
                with path.open("rb") as handle:
                    while handle.tell() < limit:
                        line = handle.readline()
                        if handle.tell() > limit:
                            break
                        yield json.loads(line)
        else:
            with self.lock:
                events = tuple(self.events)
            for event in events:
                row = row_for(event)
                if agent_id is None or row.get("agent") == agent_id or row.get("recipient") == agent_id:
                    yield row

    def close(self):
        with self.lock:
            self.flush()
            for handle in self.handles.values():
                handle.close()
            self.closed = True
