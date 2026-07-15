"""Tests for stop/give-way sign extraction and its wire round-trip."""

from gateway.osm.osm_source import OsmData, Way
from gateway.osm.signs import signs_by_way
from gateway.osm import wire

STOP_ROAD, GIVE_ROAD, CYCLE = 100, 200, 300


def _data() -> OsmData:
    d = OsmData()
    d.nodes = {
        1: (52.000, 5.000), 2: (52.000, 5.001), 3: (52.000, 5.002),   # stop road, sign at 2
        4: (52.010, 5.000), 5: (52.010, 5.001), 6: (52.010, 5.002),   # give-way road, sign at 5
        7: (52.020, 5.000), 8: (52.020, 5.001),                       # cycleway, stop at 8
    }
    d.node_tags = {
        2: {"highway": "stop"},
        5: {"highway": "give_way"},
        8: {"highway": "stop"},        # on a cycleway only -> must be ignored
    }
    d.ways = [
        Way(STOP_ROAD, {"highway": "residential"}, [1, 2, 3]),
        Way(GIVE_ROAD, {"highway": "secondary"}, [4, 5, 6]),
        Way(CYCLE, {"highway": "cycleway"}, [7, 8]),
    ]
    return d


class TestSignExtraction:
    def test_stop_attaches_to_its_road(self):
        sg = signs_by_way(_data())
        assert STOP_ROAD in sg
        assert len(sg[STOP_ROAD]) == 1
        assert sg[STOP_ROAD][0]["kind"] == "stop"
        assert abs(sg[STOP_ROAD][0]["lat"] - 52.000) < 1e-9

    def test_give_way_kind_is_mapped(self):
        sg = signs_by_way(_data())
        assert sg[GIVE_ROAD][0]["kind"] == "giveWay"

    def test_sign_on_a_cycleway_is_ignored(self):
        assert CYCLE not in signs_by_way(_data())


class TestSignWire:
    def _record(self, signs):
        return {"id": 1, "coords": [(52.0, 5.0), (52.0, 5.01)], "signs": signs}

    def test_normalized_only_when_present(self):
        assert "signs" not in wire.normalize_road(self._record([]))
        assert "signs" in wire.normalize_road(
            self._record([{"lat": 52.0, "lon": 5.0, "kind": "stop"}]))

    def test_hash_unchanged_for_sign_less_tile(self):
        rec = {"id": 1, "coords": [(52.0, 5.0), (52.0, 5.01)]}
        a = wire.tile_content_hash(wire.normalize_tile(200, 20, [rec]))
        b = wire.tile_content_hash(wire.normalize_tile(200, 20, [dict(rec, signs=[])]))
        assert a == b

    def test_capnp_round_trip_preserves_signs(self):
        rec = self._record([
            {"lat": 52.0, "lon": 5.0, "kind": "stop"},
            {"lat": 52.001, "lon": 5.002, "kind": "giveWay"},
        ])
        data = wire.serialize_tile(200, 20, [rec])
        road = wire.from_capnp_bytes(data)["roads"][0]
        kinds = sorted(sg["kind"] for sg in road["signs"])
        assert kinds == ["giveWay", "stop"]
