"""Tests for matching the next stop/give-way sign ahead on the road."""

from europilot.osm.match import next_sign_ahead
from europilot.osm.types import Road, Sign

EAST = 90.0
POSE_START = (52.0, 5.000)
POSE_END = (52.0, 5.010)


def _road(signs):
    return Road(
        id=1, road_class="residential", maxspeed=50, oneway=False, lanes=None, name="",
        points=[(52.0, 5.000), (52.0, 5.010)],
        in_residential=True, calming_per_km=0.0, crossing_per_km=0.0,
        cycleway_left="unknown", cycleway_right="unknown", cyclestreet=False,
        residential_score=0.0, comfort_speed=None, cameras=(), roundabouts=(),
        curves=(), signs=tuple(signs),
    )


def test_sign_ahead_returns_distance_and_kind():
    res = next_sign_ahead(POSE_START, EAST, _road([Sign(52.0, 5.005, "stop")]))
    assert res is not None
    dist, kind = res
    assert kind == "stop"
    assert 300 < dist < 380


def test_none_when_sign_is_behind():
    assert next_sign_ahead(POSE_END, EAST, _road([Sign(52.0, 5.005, "stop")])) is None


def test_none_without_heading():
    assert next_sign_ahead(POSE_START, None, _road([Sign(52.0, 5.005, "stop")])) is None


def test_none_when_no_signs():
    assert next_sign_ahead(POSE_START, EAST, _road([])) is None


def test_picks_nearest_ahead_and_keeps_its_kind():
    road = _road([Sign(52.0, 5.008, "stop"), Sign(52.0, 5.004, "giveWay")])
    dist, kind = next_sign_ahead(POSE_START, EAST, road)
    assert kind == "giveWay"       # 5.004 is nearer than 5.008
    assert 200 < dist < 320


def test_offset_sign_is_ignored():
    assert next_sign_ahead(POSE_START, EAST, _road([Sign(52.01, 5.005, "stop")])) is None
