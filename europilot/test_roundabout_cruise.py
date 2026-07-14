"""Tests for the roundabout cruise-easing decision (europilot/cruise.py)."""

from europilot.cruise import (
    roundabout_target_kph, ROUNDABOUT_COMFORT_KPH, MAX_TRIGGER_M, easing_distance_m,
)


def test_no_easing_when_no_roundabout():
    assert roundabout_target_kph(roundabout_distance_m=None, v_ego_kph=100) is None
    assert roundabout_target_kph(roundabout_distance_m=-1.0, v_ego_kph=100) is None


def test_no_easing_beyond_trigger_range():
    assert roundabout_target_kph(roundabout_distance_m=MAX_TRIGGER_M + 1, v_ego_kph=100) is None


def test_no_easing_when_already_slow():
    # at/below the comfort speed (+ hysteresis) there is nothing to shed
    assert roundabout_target_kph(roundabout_distance_m=50, v_ego_kph=ROUNDABOUT_COMFORT_KPH) is None


def test_eases_to_comfort_speed_when_close_and_fast():
    # far enough into the easing window that it should fire
    d = easing_distance_m(100, ROUNDABOUT_COMFORT_KPH)
    assert roundabout_target_kph(roundabout_distance_m=d - 1, v_ego_kph=100) == ROUNDABOUT_COMFORT_KPH


def test_no_easing_yet_when_still_far_within_range():
    # inside MAX_TRIGGER_M but well before the comfortable easing distance
    d = easing_distance_m(100, ROUNDABOUT_COMFORT_KPH)
    assert roundabout_target_kph(roundabout_distance_m=d + 200, v_ego_kph=100) is None


def test_only_ever_lowers_never_raises():
    t = roundabout_target_kph(roundabout_distance_m=20, v_ego_kph=90)
    assert t is None or t <= 90
