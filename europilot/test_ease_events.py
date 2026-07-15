"""Tests for reducing the easing signal to discrete ease events."""

from europilot.ease_events import EaseEventTracker

POS = dict(lat=52.0, lon=5.0, v_kph=50.0)


def test_no_event_while_not_easing():
    tr = EaseEventTracker()
    assert tr.update(t=1.0, reason="none", target_kph=-1, **POS) is None


def test_emits_on_ease_start():
    tr = EaseEventTracker()
    ev = tr.update(t=1.0, reason="curve", target_kph=60, **POS)
    assert ev is not None
    assert ev["reason"] == "curve" and ev["target_kph"] == 60
    assert ev["lat"] == 52.0 and ev["lon"] == 5.0 and ev["v_kph"] == 50.0


def test_no_repeat_while_same_reason_continues():
    tr = EaseEventTracker()
    assert tr.update(t=1.0, reason="curve", target_kph=60, **POS) is not None
    assert tr.update(t=1.2, reason="curve", target_kph=55, **POS) is None
    assert tr.update(t=1.4, reason="curve", target_kph=50, **POS) is None


def test_emits_again_after_ending_and_restarting():
    tr = EaseEventTracker(min_gap_s=0.0)
    assert tr.update(t=1.0, reason="curve", target_kph=60, **POS) is not None
    assert tr.update(t=2.0, reason="none", target_kph=-1, **POS) is None      # ended
    assert tr.update(t=3.0, reason="curve", target_kph=60, **POS) is not None  # restarted


def test_emits_on_reason_change():
    tr = EaseEventTracker(min_gap_s=0.0)
    assert tr.update(t=1.0, reason="comfort", target_kph=30, **POS)["reason"] == "comfort"
    ev = tr.update(t=2.0, reason="roundabout", target_kph=40, **POS)
    assert ev is not None and ev["reason"] == "roundabout"


def test_debounces_rapid_reflapping():
    tr = EaseEventTracker(min_gap_s=5.0)
    assert tr.update(t=1.0, reason="camera", target_kph=50, **POS) is not None
    tr.update(t=1.5, reason="none", target_kph=-1, **POS)
    # camera comes back 1s after the first event -> within the gap -> suppressed
    assert tr.update(t=2.0, reason="camera", target_kph=50, **POS) is None
    # ... but well after the gap it emits again
    tr.update(t=6.5, reason="none", target_kph=-1, **POS)
    assert tr.update(t=7.0, reason="camera", target_kph=50, **POS) is not None


def test_handles_missing_pose():
    tr = EaseEventTracker()
    ev = tr.update(t=1.0, reason="section", target_kph=80, lat=None, lon=None, v_kph=82.0)
    assert ev["lat"] is None and ev["lon"] is None and ev["reason"] == "section"
