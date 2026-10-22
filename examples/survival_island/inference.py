"""One inference at a time, with an idle interval after every request."""

from threading import Lock
import time


class InferenceGate:
    def __init__(self, cooldown: float = 3.0, *, clock=time.monotonic, sleeper=time.sleep):
        if cooldown < 0:
            raise ValueError("inference cooldown cannot be negative")
        self.cooldown, self.clock, self.sleeper = cooldown, clock, sleeper
        self.lock = Lock()
        self.next_ready = 0.0
        self.calls = 0

    def ready(self) -> bool:
        return not self.lock.locked() and self.clock() >= self.next_ready

    def run(self, function, *args):
        with self.lock:
            delay = self.next_ready - self.clock()
            if delay > 0:
                self.sleeper(delay)
            self.calls += 1
            try:
                return function(*args)
            finally:
                self.next_ready = self.clock() + self.cooldown
