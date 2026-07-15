"""Loop-timing watchdog for europilot's background daemons.

A daemon whose loop stalls can starve the msgq bus (or itself go stale as a
service a core process reads), which surfaces elsewhere as a "Communication Issue
Between Processes" take-control alert -- with no clue in the logs which daemon
caused it. This logs a rate-limited warning whenever an iteration runs well over
its budget, so a stalling europilot daemon is obvious after the fact. It is a
diagnostic only: it never blocks and never affects control.
"""

import time

from openpilot.common.swaglog import cloudlog


class LoopWatch:
    def __init__(self, name: str, budget_s: float, log_interval_s: float = 10.0):
        self.name = name
        self.budget_s = budget_s
        self._log_interval_s = log_interval_s
        self._last_tick: float | None = None
        self._last_log: float | None = None   # None -> the first slow tick always warns
        self._slow = 0
        self._worst = 0.0

    def tick(self) -> None:
        """Call once at the top of each loop iteration."""
        now = time.monotonic()
        if self._last_tick is not None:
            dt = now - self._last_tick
            if dt > self.budget_s:
                self._slow += 1
                self._worst = max(self._worst, dt)
                if self._last_log is None or now - self._last_log >= self._log_interval_s:
                    cloudlog.warning(
                        "europilot loop slow: %s up to %.2fs (>%.2fs budget), %d slow ticks in %.0fs",
                        self.name, self._worst, self.budget_s, self._slow, self._log_interval_s)
                    self._last_log = now
                    self._slow = 0
                    self._worst = 0.0
        self._last_tick = now
