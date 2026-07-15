"""Tests for the residential comfort-speed hold + its easing resolution."""

from europilot.cruise import comfort_hold_kph, resolve_easing, EASING_COMFORT, EASING_CURVE


def test_no_hold_without_a_comfort_speed():
    assert comfort_hold_kph(0) is None
    assert comfort_hold_kph(None) is None
    assert comfort_hold_kph(-1) is None


def test_holds_at_the_comfort_speed():
    assert comfort_hold_kph(30) == 30
    assert comfort_hold_kph(40) == 40


def test_resolve_includes_comfort_when_enabled():
    reason, target, dist = resolve_easing(
        camera_target=None, camera_distance_m=None, section_target=None,
        roundabout_target=None, roundabout_distance_m=None,
        curve_target=None, curve_distance_m=None, comfort_target=30,
        camera_on=True, roundabout_on=True, curve_on=True, comfort_on=True)
    assert reason == EASING_COMFORT and target == 30 and dist == -1.0


def test_resolve_ignores_comfort_when_disabled():
    reason, _, _ = resolve_easing(
        camera_target=None, camera_distance_m=None, section_target=None,
        roundabout_target=None, roundabout_distance_m=None,
        curve_target=None, curve_distance_m=None, comfort_target=30,
        camera_on=True, roundabout_on=True, curve_on=True, comfort_on=False)
    assert reason == "none"


def test_lower_event_ease_binds_over_comfort():
    # comfort caps at 30 but a curve wants 25 -> the curve (lower) binds
    reason, target, _ = resolve_easing(
        camera_target=None, camera_distance_m=None, section_target=None,
        roundabout_target=None, roundabout_distance_m=None,
        curve_target=25, curve_distance_m=80.0, comfort_target=30,
        camera_on=True, roundabout_on=True, curve_on=True, comfort_on=True)
    assert reason == EASING_CURVE and target == 25
