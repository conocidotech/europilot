"""Tests for resolving the binding advisory ease (observability)."""

from europilot.cruise import (
    resolve_easing, EASING_NONE, EASING_CAMERA, EASING_SECTION,
    EASING_ROUNDABOUT, EASING_CURVE,
)

ALL_ON = dict(camera_on=True, roundabout_on=True, curve_on=True)
NONE = dict(camera_target=None, camera_distance_m=None, section_target=None,
            roundabout_target=None, roundabout_distance_m=None,
            curve_target=None, curve_distance_m=None)


def test_nothing_easing():
    reason, target, dist = resolve_easing(**NONE, **ALL_ON)
    assert reason == EASING_NONE and target == -1 and dist == -1.0


def test_single_source_reported_with_distance():
    reason, target, dist = resolve_easing(
        **{**NONE, "curve_target": 60, "curve_distance_m": 120.0}, **ALL_ON)
    assert reason == EASING_CURVE and target == 60 and dist == 120.0


def test_lowest_target_binds():
    # curve 60 vs roundabout 40 -> the roundabout is the binding (lowest) ease
    reason, target, _ = resolve_easing(
        **{**NONE, "curve_target": 60, "curve_distance_m": 100.0,
           "roundabout_target": 40, "roundabout_distance_m": 200.0}, **ALL_ON)
    assert reason == EASING_ROUNDABOUT and target == 40


def test_section_has_no_distance():
    reason, target, dist = resolve_easing(
        **{**NONE, "section_target": 80}, **ALL_ON)
    assert reason == EASING_SECTION and target == 80 and dist == -1.0


def test_disabled_source_is_ignored():
    # curve would bind at 50, but curve easing is OFF -> fall to the camera at 70
    reason, target, _ = resolve_easing(
        **{**NONE, "curve_target": 50, "curve_distance_m": 80.0,
           "camera_target": 70, "camera_distance_m": 300.0},
        camera_on=True, roundabout_on=True, curve_on=False)
    assert reason == EASING_CAMERA and target == 70


def test_all_disabled_means_none():
    reason, _, _ = resolve_easing(
        **{**NONE, "curve_target": 50, "curve_distance_m": 80.0},
        camera_on=False, roundabout_on=False, curve_on=False)
    assert reason == EASING_NONE


def test_camera_vs_section_attribution():
    # camera approach 90 and section hold 80 both active -> section (80) binds
    reason, target, _ = resolve_easing(
        **{**NONE, "camera_target": 90, "camera_distance_m": 400.0, "section_target": 80},
        **ALL_ON)
    assert reason == EASING_SECTION and target == 80
