"""Cooperative execution controls, separate from numerical acceptance criteria."""
from __future__ import annotations

import math
import threading
import time


class SolveInterrupted(Exception):
    """A requested stop, never a numerical convergence fallback."""


class SolveCancelled(SolveInterrupted):
    pass


class SolveTimedOut(SolveInterrupted):
    pass


class SolveControl:
    """Thread-safe first-stop-wins cancellation with a finite wall-time budget."""

    def __init__(self, budget_s: float):
        if not math.isfinite(budget_s) or budget_s <= 0:
            raise ValueError("solver execution budget must be finite and positive")
        self.budget_s = float(budget_s)
        self.deadline = time.monotonic() + self.budget_s
        self._event = threading.Event()
        self._lock = threading.Lock()
        self.outcome: str | None = None
        self.reason = ""

    def _stop(self, outcome: str, reason: str) -> None:
        with self._lock:
            if not self._event.is_set():
                self.outcome, self.reason = outcome, reason
                self._event.set()

    def cancel(self, reason: str = "user requested cancellation") -> None:
        self._stop("cancelled", reason)

    def checkpoint(self) -> None:
        if time.monotonic() >= self.deadline:
            self._stop("timed_out", f"execution budget of {self.budget_s:g} seconds exceeded")
        if self._event.is_set():
            exception = SolveTimedOut if self.outcome == "timed_out" else SolveCancelled
            raise exception(self.reason)
