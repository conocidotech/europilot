"""Tests for the roundabout cruise-easing decision + size-aware approach speed."""

from europilot.cruise import (
    roundabout_target_kph, roundabout_approach_kph, MAX_TRIGGER_M, easing_distance_m,
    ROUNDABOUT_MINI_KPH, ROUNDABOUT_SMALL_KPH, ROUNDABOUT_LARGE_KPH,
)


# --- size-aware approach speed ------------------------------------------------

def test_mini_gets_the_fixed_low_speed():
    assert roundabout_approach_kph("mini", 0) == ROUNDABOUT_MINI_KPH
    assert roundabout_approach_kph("mini", 50) == ROUNDABOUT_MINI_KPH   # kind wins


def test_unknown_radius_falls_back_to_mini():
    assert roundabout_approach_kph("roundabout", 0) == ROUNDABOUT_MINI_KPH


def test_small_ring_is_slow_large_ring_is_faster():
    small = roundabout_approach_kph("roundabout", 8)
    large = roundabout_approach_kph("roundabout", 40)
    assert small <= ROUNDABOUT_SMALL_KPH + 5
    assert large == ROUNDABOUT_LARGE_KPH
    assert small < large


def test_approach_speed_is_monotonic_in_radius():
    speeds = [roundabout_approach_kph("roundabout", r) for r in (10, 18, 26, 34)]
    assert speeds == sorted(speeds)          # never decreases with size
    assert all(ROUNDABOUT_SMALL_KPH <= s <= ROUNDABOUT_LARGE_KPH for s in speeds)


def test_approach_speed_rounded_to_5():
    for r in range(5, 45, 3):
        assert roundabout_approach_kph("roundabout", r) % 5 == 0


# --- the easing decision (target uses the sized comfort speed) ---------------

def test_no_easing_when_no_roundabout():
    assert roundabout_target_kph(roundabout_distance_m=None, v_ego_kph=100) is None
    assert roundabout_target_kph(roundabout_distance_m=-1.0, v_ego_kph=100) is None


def test_no_easing_beyond_trigger_range():
    assert roundabout_target_kph(roundabout_distance_m=MAX_TRIGGER_M + 1, v_ego_kph=100) is None


def test_no_easing_when_already_slow():
    assert roundabout_target_kph(roundabout_distance_m=50, v_ego_kph=ROUNDABOUT_MINI_KPH,
                                 comfort_kph=ROUNDABOUT_MINI_KPH) is None


def test_eases_to_the_sized_comfort_speed_when_close():
    comfort = roundabout_approach_kph("roundabout", 40)   # large -> LARGE_KPH
    d = easing_distance_m(100, comfort)
    assert roundabout_target_kph(roundabout_distance_m=d - 1, v_ego_kph=100,
                                 comfort_kph=comfort) == comfort


def test_only_ever_lowers_never_raises():
    t = roundabout_target_kph(roundabout_distance_m=20, v_ego_kph=90, comfort_kph=48)
    assert t is None or t <= 90
