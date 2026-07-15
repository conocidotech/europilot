"""Tests for detecting that the matched road is inside an average-speed section."""

from europilot.osm.match import match, _section_limit
from europilot.osm.types import Camera, Road


def _road(cameras, maxspeed=100):
    return Road(
        id=1, road_class="trunk", maxspeed=maxspeed, oneway=False, lanes=None, name="",
        points=[(52.0, 5.000), (52.0, 5.010)],
        in_residential=False, calming_per_km=0.0, crossing_per_km=0.0,
        cycleway_left="unknown", cycleway_right="unknown", cyclestreet=False,
        residential_score=0.0, comfort_speed=None, cameras=tuple(cameras),
        roundabouts=(),
    )


def _section(maxspeed):
    return Camera(lat=52.0, lon=5.005, maxspeed=maxspeed, kind="section")


def _fixed(maxspeed):
    return Camera(lat=52.0, lon=5.005, maxspeed=maxspeed, kind="fixed")


def test_section_limit_from_the_section_camera():
    assert _section_limit(_road([_section(80)])) == 80


def test_not_in_section_without_a_section_camera():
    assert _section_limit(_road([_fixed(100)])) == 0
    assert _section_limit(_road([])) == 0


def test_falls_back_to_road_limit_when_section_has_no_maxspeed():
    assert _section_limit(_road([_section(None)], maxspeed=100)) == 100


def test_tightest_section_wins_when_overlapping():
    assert _section_limit(_road([_section(100), _section(80)])) == 80


def test_match_surfaces_section_limit_on_the_advisory():
    # driving along a road that carries a section camera -> advisory.section_limit set
    adv = match((52.0, 5.002), 90.0, [_road([_section(80)])])
    assert adv.valid
    assert adv.section_limit == 80


def test_match_reports_no_section_on_a_plain_road():
    adv = match((52.0, 5.002), 90.0, [_road([])])
    assert adv.valid
    assert adv.section_limit == 0
