"""Tests for the OSM spatial-join derivations."""

from pathlib import Path

from gateway.osm.osm_source import parse_full
from gateway.osm.spatial import (
    combine_side, comfort_speed, cyclestreet, cycleway_geometry_sides,
    cycleway_tag_sides, density_per_km, in_residential, residential_polygons,
    residential_score,
)
from gateway.osm.tiles import build_tiles_full

RESIDENTIAL = (Path(__file__).parent / "test_data" / "residential.osm").read_bytes()


class TestResidentialMembership:
    def test_closed_way_area(self):
        data = parse_full(RESIDENTIAL)
        polys = residential_polygons(data)
        assert len(polys) == 1
        assert in_residential([(52.010, 5.005), (52.010, 5.015)], polys) is True   # inside
        assert in_residential([(52.100, 5.005), (52.100, 5.015)], polys) is False  # outside

    def test_no_areas(self):
        assert residential_polygons(parse_full(b"<osm/>")) == []
        assert in_residential([(52.0, 5.0)], []) is False

    def test_through_road_crossing_is_not_residential(self):
        # A road that only crosses the area (square lon[5.00,5.02]) at its middle
        # node: the midpoint lands inside, but most of the road is outside. The
        # old single-point test flagged this; the fraction test must not.
        data = parse_full(RESIDENTIAL)
        polys = residential_polygons(data)
        through = [(52.010, 4.980), (52.010, 5.010), (52.010, 5.040)]
        assert in_residential(through, polys) is False


class TestDensity:
    def test_calming_per_km(self):
        # road ~685 m at lat 52; two bumps on it -> ~2.9 / km
        road = [(52.010, 5.005), (52.010, 5.015)]
        bumps = [(52.010, 5.008), (52.010, 5.012)]
        assert 2.5 < density_per_km(road, bumps) < 3.3

    def test_points_off_the_road_are_not_counted(self):
        road = [(52.010, 5.005), (52.010, 5.015)]
        far = [(52.050, 5.010)]   # ~4 km away
        assert density_per_km(road, far) == 0.0

    def test_zero_length(self):
        assert density_per_km([(52.0, 5.0)], [(52.0, 5.0)]) == 0.0


class TestCyclewayTags:
    def test_both_and_absent(self):
        assert cycleway_tag_sides({"cycleway": "lane"}) == (True, True)
        assert cycleway_tag_sides({"cycleway:both": "track"}) == (True, True)
        assert cycleway_tag_sides({"cycleway": "no"}) == (False, False)
        assert cycleway_tag_sides({}) == (None, None)   # unknown, not False

    def test_one_sided(self):
        assert cycleway_tag_sides({"cycleway:right": "lane"}) == (None, True)
        assert cycleway_tag_sides({"cycleway:left": "no"}) == (False, None)

    def test_separate_means_absent_on_road(self):
        assert cycleway_tag_sides({"cycleway": "separate"}) == (False, False)


class TestCyclewayGeometry:
    def test_parallel_cycleway_on_the_left(self):
        # road W->E; cycleway ~11 m north == left of travel direction
        road = [(52.010, 5.005), (52.010, 5.015)]
        cw = [(52.0101, 5.005), (52.0101, 5.015)]
        assert cycleway_geometry_sides(road, [cw]) == (True, False)

    def test_parallel_cycleway_on_the_right(self):
        road = [(52.010, 5.005), (52.010, 5.015)]
        cw = [(52.0099, 5.005), (52.0099, 5.015)]   # ~11 m south == right
        assert cycleway_geometry_sides(road, [cw]) == (False, True)

    def test_far_or_crossing_cycleway_ignored(self):
        road = [(52.010, 5.005), (52.010, 5.015)]
        far = [(52.030, 5.005), (52.030, 5.015)]          # ~2 km north
        crossing = [(52.009, 5.010), (52.011, 5.010)]      # perpendicular
        assert cycleway_geometry_sides(road, [far, crossing]) == (False, False)


class TestCombineAndScore:
    def test_combine_side_three_valued(self):
        assert combine_side(None, True) is True     # geometry found it
        assert combine_side(True, False) is True     # tag says yes
        assert combine_side(False, False) is False   # tag says no
        assert combine_side(None, False) is None     # nothing known -> unknown

    def test_cyclestreet_nl_convention(self):
        assert cyclestreet({"cyclestreet": "yes"}) is True
        assert cyclestreet({"bicycle_road": "yes"}) is True
        assert cyclestreet({}) is False

    def test_residential_score_and_comfort(self):
        strong = residential_score(road_class="residential", maxspeed=30, living_street=False,
                                   calming_per_km=3.0, crossing_per_km=1.0, in_residential=True)
        assert strong >= 0.6
        assert comfort_speed(strong, 50) == 30

        weak = residential_score(road_class="primary", maxspeed=50, living_street=False,
                                 calming_per_km=0.0, crossing_per_km=0.0, in_residential=False)
        assert weak < 0.35
        assert comfort_speed(weak, 50) is None

    def test_comfort_never_exceeds_posted(self):
        assert comfort_speed(0.9, 20) == 20   # capped at the posted 20, not 30


class TestBuildTilesFull:
    def test_spatial_attributes_stamped(self):
        data = parse_full(RESIDENTIAL)
        tiles = build_tiles_full(data)
        records = [r for recs in tiles.values() for r in recs]
        by_id = {r["id"]: r for r in records}

        # cycleway (102) is not a road record; the two residential roads are
        assert set(by_id) == {101, 103}

        road_a = by_id[101]
        assert road_a["inResidential"] is True
        assert road_a["cyclewayLeft"] is True and road_a["cyclewayRight"] is None
        assert road_a["calmingPerKm"] > 2.0
        assert road_a["residentialScore"] >= 0.6
        assert road_a["comfortSpeed"] == 30

        road_b = by_id[103]
        assert road_b["inResidential"] is False
        assert road_b["comfortSpeed"] is None
