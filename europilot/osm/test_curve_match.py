"""Tests for matching the next sharp bend ahead on the road."""

from europilot.osm.match import next_curve_ahead
from europilot.osm.types import Curve, Road


EAST = 90.0
POSE_START = (52.0, 5.000)
POSE_END = (52.0, 5.010)


def _road(curves):
    # a straight road heading due east from lon 5.000 to 5.010 at lat 52
    return Road(
        id=1, road_class="secondary", maxspeed=80, oneway=False, lanes=None, name="",
        points=[(52.0, 5.000), (52.0, 5.010)],
        in_residential=False, calming_per_km=0.0, crossing_per_km=0.0,
        cycleway_left="unknown", cycleway_right="unknown", cyclestreet=False,
        residential_score=0.0, comfort_speed=None, cameras=(), roundabouts=(),
        curves=tuple(curves),
    )


def test_curve_ahead_returns_distance_and_radius():
    road = _road([Curve(52.0, 5.005, 60)])
    res = next_curve_ahead(POSE_START, EAST, road)
    assert res is not None
    dist, radius = res
    assert radius == 60
    assert 300 < dist < 380          # ~343 m east


def test_none_when_curve_is_behind():
    road = _road([Curve(52.0, 5.005, 60)])
    assert next_curve_ahead(POSE_END, EAST, road) is None


def test_none_without_heading():
    road = _road([Curve(52.0, 5.005, 60)])
    assert next_curve_ahead(POSE_START, None, road) is None


def test_none_when_no_curves():
    assert next_curve_ahead(POSE_START, EAST, _road([])) is None


def test_picks_nearest_ahead():
    road = _road([Curve(52.0, 5.008, 120), Curve(52.0, 5.004, 40)])
    dist, radius = next_curve_ahead(POSE_START, EAST, road)
    assert radius == 40              # 5.004 is nearer than 5.008
    assert 200 < dist < 320


def test_offset_curve_is_ignored():
    # a marker well off the road (0.01 deg north ~ 1.1 km) must not count
    road = _road([Curve(52.01, 5.005, 60)])
    assert next_curve_ahead(POSE_START, EAST, road) is None
