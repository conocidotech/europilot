"""Tests for the MTSC curve cruise-easing decision + cornering speed."""

from europilot.cruise import (
    curve_target_kph, curve_speed_kph, MAX_TRIGGER_M, easing_distance_m,
    CURVE_MIN_KPH, CURVE_MAX_KPH,
)


# --- cornering speed sizing ---------------------------------------------------

def test_no_radius_means_no_speed():
    assert curve_speed_kph(0) == 0
    assert curve_speed_kph(-5) == 0


def test_tighter_bend_is_slower():
    tight = curve_speed_kph(30)
    open_ = curve_speed_kph(200)
    assert tight < open_


def test_cornering_speed_is_monotonic_in_radius():
    speeds = [curve_speed_kph(r) for r in (25, 50, 100, 150, 250)]
    assert speeds == sorted(speeds)


def test_cornering_speed_clamped_to_band_and_rounded_to_5():
    assert curve_speed_kph(1) == CURVE_MIN_KPH        # a hairpin -> floor
    assert curve_speed_kph(100000) == CURVE_MAX_KPH   # nearly straight -> ceiling
    for r in range(10, 300, 7):
        assert curve_speed_kph(r) % 5 == 0


# --- the easing decision ------------------------------------------------------

def test_no_easing_when_no_curve():
    assert curve_target_kph(curve_distance_m=None, curve_radius_m=50, v_ego_kph=100) is None
    assert curve_target_kph(curve_distance_m=-1.0, curve_radius_m=50, v_ego_kph=100) is None


def test_no_easing_beyond_trigger_range():
    assert curve_target_kph(curve_distance_m=MAX_TRIGGER_M + 1, curve_radius_m=50,
                            v_ego_kph=100) is None


def test_no_easing_when_already_at_cornering_speed():
    comfort = curve_speed_kph(50)
    assert curve_target_kph(curve_distance_m=50, curve_radius_m=50, v_ego_kph=comfort) is None


def test_eases_to_the_cornering_speed_when_close():
    comfort = curve_speed_kph(50)
    d = easing_distance_m(100, comfort)
    assert curve_target_kph(curve_distance_m=d - 1, curve_radius_m=50, v_ego_kph=100) == comfort


def test_holds_off_while_still_far():
    comfort = curve_speed_kph(50)
    d = easing_distance_m(100, comfort)
    assert curve_target_kph(curve_distance_m=d + 50, curve_radius_m=50, v_ego_kph=100) is None


def test_only_ever_lowers_never_raises():
    t = curve_target_kph(curve_distance_m=20, curve_radius_m=200, v_ego_kph=60)
    assert t is None or t <= 60
