"""Tests for curve (sharp-bend) extraction and its wire round-trip."""

from gateway.osm.osm_source import OsmData, Way
from gateway.osm.curves import curves_by_way, _CURVE_THRESHOLD_M
from gateway.osm import wire

STRAIGHT, SHARP, GENTLE, CYCLE, ARC = 1, 2, 3, 4, 5


def _data() -> OsmData:
    d = OsmData()
    d.nodes = {
        # straight road (collinear, due east)
        10: (52.000, 5.000), 11: (52.000, 5.001), 12: (52.000, 5.002),
        # sharp ~90 deg bend: east then north, corner at node 21
        20: (52.000, 5.010), 21: (52.000, 5.011), 22: (52.001, 5.011),
        # very gentle deviation
        30: (52.000, 5.020), 31: (52.000, 5.030), 32: (52.0002, 5.040),
        # a cycleway with the same sharp corner -> must be ignored (not drivable)
        40: (52.010, 5.010), 41: (52.010, 5.011), 42: (52.011, 5.011),
        # a tight arc with consecutive sharp vertices -> ONE clustered marker
        50: (52.020, 5.000), 51: (52.020, 5.001), 52: (52.0206, 5.0012), 53: (52.0212, 5.0012),
    }
    d.ways = [
        Way(STRAIGHT, {"highway": "secondary"}, [10, 11, 12]),
        Way(SHARP, {"highway": "secondary"}, [20, 21, 22]),
        Way(GENTLE, {"highway": "secondary"}, [30, 31, 32]),
        Way(CYCLE, {"highway": "cycleway"}, [40, 41, 42]),
        Way(ARC, {"highway": "tertiary"}, [50, 51, 52, 53]),
    ]
    return d


class TestCurveExtraction:
    def test_straight_road_has_no_curve(self):
        assert STRAIGHT not in curves_by_way(_data())

    def test_sharp_bend_gets_one_marker_at_the_corner(self):
        cv = curves_by_way(_data())
        assert SHARP in cv
        pts = cv[SHARP]
        assert len(pts) == 1
        assert 0 < pts[0]["radius_m"] < _CURVE_THRESHOLD_M
        # marker sits at the corner node (52.000, 5.011)
        assert abs(pts[0]["lat"] - 52.000) < 1e-9 and abs(pts[0]["lon"] - 5.011) < 1e-9

    def test_gentle_curve_is_below_threshold_and_skipped(self):
        assert GENTLE not in curves_by_way(_data())

    def test_cycleway_is_never_marked(self):
        assert CYCLE not in curves_by_way(_data())

    def test_consecutive_sharp_vertices_cluster_into_one_marker(self):
        cv = curves_by_way(_data())
        assert ARC in cv
        assert len(cv[ARC]) == 1          # one bend, not one per vertex


class TestCurveWire:
    def _record(self, curves):
        return {"id": 1, "coords": [(52.0, 5.0), (52.0, 5.01)], "curves": curves}

    def test_normalized_only_when_present(self):
        # a curve-less road must not carry the key -> hash stays as before.
        assert "curves" not in wire.normalize_road(self._record([]))
        assert "curves" in wire.normalize_road(
            self._record([{"lat": 52.0, "lon": 5.0, "radius_m": 65}]))

    def test_hash_unchanged_for_curve_less_tile(self):
        rec = {"id": 1, "coords": [(52.0, 5.0), (52.0, 5.01)]}
        a = wire.tile_content_hash(wire.normalize_tile(200, 20, [rec]))
        b = wire.tile_content_hash(wire.normalize_tile(200, 20, [dict(rec, curves=[])]))
        assert a == b

    def test_capnp_round_trip_preserves_curves(self):
        rec = self._record([
            {"lat": 52.0, "lon": 5.0, "radius_m": 65},
            {"lat": 52.001, "lon": 5.002, "radius_m": 300},
        ])
        data = wire.serialize_tile(200, 20, [rec])
        road = wire.from_capnp_bytes(data)["roads"][0]
        radii = sorted(cv["radiusM"] for cv in road["curves"])
        assert radii == [65, 300]        # radius survives the wire (UInt16)
