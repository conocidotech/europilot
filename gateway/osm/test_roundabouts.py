"""Tests for roundabout extraction and its wire round-trip."""

from gateway.osm.osm_source import OsmData, Way
from gateway.osm.roundabouts import roundabouts_by_way
from gateway.osm import wire

RING, APPROACH, UNRELATED, MINI_ROAD = 100, 200, 300, 400


def _data() -> OsmData:
    d = OsmData()
    # roundabout ring nodes 1..4 (closed), approach at node 1
    d.nodes = {
        1: (52.000, 5.000), 2: (52.001, 5.000), 3: (52.001, 5.001), 4: (52.000, 5.001),
        10: (51.999, 5.000),                       # approach road's far node
        20: (52.010, 5.010), 21: (52.011, 5.010),  # unrelated road
        30: (52.004, 5.005), 5: (52.005, 5.005), 31: (52.006, 5.005),  # mini-roundabout road
    }
    d.node_tags = {5: {"highway": "mini_roundabout"}}
    d.ways = [
        Way(RING, {"junction": "roundabout", "highway": "primary"}, [1, 2, 3, 4, 1]),
        Way(APPROACH, {"highway": "primary"}, [10, 1]),
        Way(UNRELATED, {"highway": "residential"}, [20, 21]),
        Way(MINI_ROAD, {"highway": "residential"}, [30, 5, 31]),
    ]
    return d


class TestRoundaboutAttachment:
    def test_attaches_to_the_approach_road_at_the_entry_node(self):
        rb = roundabouts_by_way(_data())
        assert APPROACH in rb
        pts = rb[APPROACH]
        assert len(pts) == 1
        assert pts[0]["kind"] == "roundabout"
        assert pts[0]["radius_m"] > 0          # ring radius baked from the geometry
        assert abs(pts[0]["lat"] - 52.000) < 1e-9 and abs(pts[0]["lon"] - 5.000) < 1e-9

    def test_never_attaches_to_the_ring_itself(self):
        # you are already on the roundabout -- no ease-off for the carriageway.
        assert RING not in roundabouts_by_way(_data())

    def test_unrelated_road_gets_nothing(self):
        assert UNRELATED not in roundabouts_by_way(_data())

    def test_mini_roundabout_attaches_to_its_road(self):
        rb = roundabouts_by_way(_data())
        assert MINI_ROAD in rb
        pts = rb[MINI_ROAD]
        assert len(pts) == 1 and pts[0]["kind"] == "mini"
        assert pts[0]["radius_m"] == 0         # a mini has no ring geometry
        assert abs(pts[0]["lat"] - 52.005) < 1e-9

    def test_no_duplicate_entry_for_the_same_point(self):
        for pts in roundabouts_by_way(_data()).values():
            keys = [(p["lat"], p["lon"], p["kind"]) for p in pts]
            assert len(keys) == len(set(keys))


class TestRoundaboutWire:
    def _record(self, roundabouts):
        return {"id": 1, "coords": [(52.0, 5.0), (52.0, 5.01)], "roundabouts": roundabouts}

    def test_normalized_only_when_present(self):
        # a roundabout-less road must not carry the key -> hash stays as before.
        assert "roundabouts" not in wire.normalize_road(self._record([]))
        assert "roundabouts" in wire.normalize_road(
            self._record([{"lat": 52.0, "lon": 5.0, "kind": "roundabout", "radius_m": 25}]))

    def test_hash_unchanged_for_roundabout_less_tile(self):
        rec = {"id": 1, "coords": [(52.0, 5.0), (52.0, 5.01)]}
        a = wire.tile_content_hash(wire.normalize_tile(200, 20, [rec]))
        b = wire.tile_content_hash(wire.normalize_tile(200, 20, [dict(rec, roundabouts=[])]))
        assert a == b

    def test_capnp_round_trip_preserves_roundabouts(self):
        rec = self._record([
            {"lat": 52.0, "lon": 5.0, "kind": "roundabout", "radius_m": 30},
            {"lat": 52.001, "lon": 5.002, "kind": "mini", "radius_m": 0},
        ])
        data = wire.serialize_tile(200, 20, [rec])
        road = wire.from_capnp_bytes(data)["roads"][0]
        by_kind = {rb["kind"]: rb for rb in road["roundabouts"]}
        assert sorted(by_kind) == ["mini", "roundabout"]
        assert by_kind["roundabout"]["radiusM"] == 30   # radius survives the wire
        assert by_kind["mini"]["radiusM"] == 0
