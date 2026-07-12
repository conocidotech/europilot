"""Tests for the OSM tile-derivation core."""

from pathlib import Path

from gateway.osm import tags
from gateway.osm.grid import tile_of, group_of, centroid
from gateway.osm.osm_source import parse
from gateway.osm.tiles import build_tiles, tile_hash

SAMPLE = (Path(__file__).parent / "test_data" / "sample.osm").read_bytes()


class TestTags:
    def test_road_class_whitelist_and_links(self):
        assert tags.road_class({"highway": "motorway"}) == "motorway"
        assert tags.road_class({"highway": "primary_link"}) == "primary"   # link -> parent
        assert tags.road_class({"highway": "cycleway"}) is None            # dropped
        assert tags.road_class({"highway": "footway"}) is None
        assert tags.road_class({}) is None

    def test_maxspeed_numeric(self):
        assert tags.maxspeed({"maxspeed": "100"}) == 100
        assert tags.maxspeed({"maxspeed": "50"}) == 50

    def test_maxspeed_nl_zone(self):
        assert tags.maxspeed({"zone:maxspeed": "NL:zone30"}) == 30
        assert tags.maxspeed({"maxspeed:type": "NL:zone60"}) == 60
        assert tags.maxspeed({"maxspeed": "NL:zone30"}) == 30

    def test_maxspeed_zone_key_plain_forms(self):
        # zone:maxspeed=NL:30 and =30 are the common NL 30-zone tags and must
        # resolve, not fall through to None (they were silently dropped before).
        assert tags.maxspeed({"zone:maxspeed": "NL:30"}) == 30
        assert tags.maxspeed({"zone:maxspeed": "30"}) == 30
        assert tags.maxspeed({"zone:maxspeed": "NL:60"}) == 60

    def test_maxspeed_nl_implicit(self):
        assert tags.maxspeed({"maxspeed": "NL:urban"}) == 50
        assert tags.maxspeed({"maxspeed": "NL:rural"}) == 80

    def test_maxspeed_unknown_is_none(self):
        assert tags.maxspeed({}) is None
        assert tags.maxspeed({"maxspeed": "signals"}) is None
        assert tags.maxspeed({"maxspeed": "NL:motorway"}) is None   # deliberately unknown

    def test_oneway(self):
        assert tags.oneway({"oneway": "yes"}) is True
        assert tags.oneway({"oneway": "-1"}) is True
        assert tags.oneway({"oneway": "no"}) is False
        assert tags.oneway({"highway": "motorway"}) is True         # implicit
        assert tags.oneway({"highway": "residential"}) is False

    def test_lanes(self):
        assert tags.lanes({"lanes": "2"}) == 2
        assert tags.lanes({"lanes": "0"}) is None
        assert tags.lanes({}) is None

    def test_derive_way_drops_non_roads(self):
        assert tags.derive_way({"highway": "cycleway"}) is None
        assert tags.derive_way({"highway": "residential", "maxspeed": "30"}) == {
            "roadClass": "residential", "maxspeed": 30, "oneway": False,
            "lanes": None, "name": None,
        }


class TestGrid:
    def test_tile_of(self):
        assert tile_of(52.09, 5.11) == (208, 20)
        assert tile_of(51.90, 4.90) == (207, 19)

    def test_group_of(self):
        # 2 degree groups snap down to a multiple of 8 tiles
        assert group_of(208, 20) == (208, 16)
        assert group_of(207, 19) == (200, 16)

    def test_centroid(self):
        assert centroid([(52.0, 5.0), (52.2, 5.2)]) == (52.1, 5.1)


class TestTiles:
    def test_build_buckets_and_filters(self):
        nodes, ways = parse(SAMPLE)
        tiles = build_tiles(nodes, ways)

        # ways 100/101/103 fall in (208,20); 104 in (207,19); 102 (cycleway) dropped
        assert set(tiles) == {(208, 20), (207, 19)}
        ids_208 = {r["id"] for r in tiles[(208, 20)]}
        assert ids_208 == {100, 101, 103}
        assert {r["id"] for r in tiles[(207, 19)]} == {104}

    def test_derived_attributes_land_in_records(self):
        nodes, ways = parse(SAMPLE)
        tiles = build_tiles(nodes, ways)
        by_id = {r["id"]: r for r in tiles[(208, 20)]}
        assert by_id[100]["maxspeed"] == 100 and by_id[100]["oneway"] is True
        assert by_id[101]["name"] == "Dorpsstraat" and by_id[101]["maxspeed"] == 30
        assert by_id[103]["lanes"] == 2
        # the NL zone tag was resolved
        assert build_tiles(nodes, ways)[(207, 19)][0]["maxspeed"] == 30

    def test_hash_is_reproducible_and_order_independent(self):
        nodes, ways = parse(SAMPLE)
        recs = build_tiles(nodes, ways)[(208, 20)]
        assert tile_hash(recs) == tile_hash(list(reversed(recs)))
        assert tile_hash(recs) == tile_hash(build_tiles(*parse(SAMPLE))[(208, 20)])

    def test_hash_changes_when_data_changes(self):
        nodes, ways = parse(SAMPLE)
        recs = build_tiles(nodes, ways)[(208, 20)]
        mutated = [dict(r, maxspeed=999) if r["id"] == 100 else r for r in recs]
        assert tile_hash(recs) != tile_hash(mutated)
