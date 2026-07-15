"""Tests for the daemon loop-timing watchdog."""

import types

from europilot import loopwatch
from europilot.loopwatch import LoopWatch


def _run(monkeypatch, ticks_s, budget_s=0.5, log_interval_s=10.0):
    """Feed a sequence of monotonic timestamps; return the warning call args."""
    warnings = []
    clock = {"t": 0.0}
    monkeypatch.setattr(loopwatch.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(loopwatch, "cloudlog",
                        types.SimpleNamespace(warning=lambda *a, **k: warnings.append(a)))
    w = LoopWatch("test", budget_s=budget_s, log_interval_s=log_interval_s)
    for t in ticks_s:
        clock["t"] = t
        w.tick()
    return warnings


def test_no_warning_on_fast_ticks(monkeypatch):
    # 0.1s apart, well under the 0.5s budget
    assert _run(monkeypatch, [0.0, 0.1, 0.2, 0.3, 0.4]) == []


def test_warns_on_a_slow_tick(monkeypatch):
    # a 2s gap exceeds the 0.5s budget -> one warning
    assert len(_run(monkeypatch, [0.0, 2.0])) == 1


def test_warning_is_rate_limited(monkeypatch):
    # repeated slow ticks within one log interval -> only the first warns
    ticks = [i * 1.0 for i in range(6)]   # 1s gaps, 0.5s budget, 10s interval
    assert len(_run(monkeypatch, ticks, budget_s=0.5, log_interval_s=10.0)) == 1


def test_first_tick_never_warns(monkeypatch):
    assert _run(monkeypatch, [5.0]) == []
