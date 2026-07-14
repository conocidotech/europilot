"""Tests for matching the next roundabout ahead on the road."""

from europilot.osm.match import next_roundabout_ahead
from europilot.osm.types import Road, Roundabout


def _road(roundabouts):
    # a straight road heading due east from lon 5.000 to 5.010 at lat 52
    return Road(
        id=1, road_class="primary", maxspeed=80, oneway=False, lanes=None, name="",
        points=[(52.0, 5.000), (52.0, 5.010)],
        in_residential=False, calming_per_km=0.0, crossing_per_km=0.0,
        cycleway_left="unknown", cycleway_right="unknown", cyclestreet=False,
        residential_score=0.0, comfort_speed=None, cameras=(),
        roundabouts=tuple(roundabouts),
    )


EAST = 90.0
POSE_START = (52.0, 5.000)
POSE_END = (52.0, 5.010)


def test_roundabout_ahead_returns_distance_and_kind():
    road = _road([Roundabout(52.0, 5.005, "roundabout")])
    res = next_roundabout_ahead(POSE_START, EAST, road)
    assert res is not None
    dist, kind = res
    assert kind == "roundabout"
    assert 300 < dist < 380   # ~343 m east


def test_none_when_roundabout_is_behind():
    road = _road([Roundabout(52.0, 5.005, "roundabout")])
    # driving east but already past the roundabout (pose at the east end)
    assert next_roundabout_ahead(POSE_END, EAST, road) is None


def test_none_without_heading():
    road = _road([Roundabout(52.0, 5.005, "roundabout")])
    assert next_roundabout_ahead(POSE_START, None, road) is None


def test_none_when_no_roundabouts():
    assert next_roundabout_ahead(POSE_START, EAST, _road([])) is None


def test_picks_nearest_ahead():
    road = _road([Roundabout(52.0, 5.008, "roundabout"), Roundabout(52.0, 5.004, "mini")])
    dist, kind = next_roundabout_ahead(POSE_START, EAST, road)
    assert kind == "mini"        # 5.004 is nearer than 5.008
    assert 200 < dist < 320


def test_offset_roundabout_is_ignored():
    # a point well off the road (0.01 deg north ~ 1.1 km) must not count
    road = _road([Roundabout(52.01, 5.005, "roundabout")])
    assert next_roundabout_ahead(POSE_START, EAST, road) is None
