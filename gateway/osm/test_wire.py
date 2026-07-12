"""Tests for the Cap'n Proto tile wire format.

The mapping and hashing are pure and run everywhere. The capnp encode/decode
round-trip needs pycapnp; it's importorskip'd so the lightweight CI stays green
if the wheel isn't installed, while the risky mapping logic is always covered.
"""

from pathlib import Path

import pytest

from gateway.osm import wire
from gateway.osm.osm_source import parse_full
from gateway.osm.tiles import build_tiles_full

RESIDENTIAL = (Path(__file__).parent / "test_data" / "residential.osm").read_bytes()
CAMERAS = (Path(__file__).parent / "test_data" / "cameras.osm").read_bytes()

# A road A record like build_tiles_full stamps: residential, 30 km/h, cycleway
# on the left only, calming present.
ROAD_A = {
    "id": 101, "roadClass": "residential", "maxspeed": 30, "oneway": False,
    "lanes": None, "name": "Speeltuinlaan",
    "coords": [(52.010, 5.005), (52.010, 5.015)],
    "inResidential": True, "calmingPerKm": 2.9, "crossingPerKm": 0.0,
    "cyclewayLeft": True, "cyclewayRight": None, "cyclestreet": False,
    "residentialScore": 0.85, "comfortSpeed": 30,
}


class TestNormalizeRoad:
    def test_enum_and_sentinels(self):
        n = wire.normalize_road(ROAD_A)
        assert n["roadClass"] == "residential"
        assert n["lanes"] == 0            # None -> 0 sentinel
        assert n["maxspeed"] == 30
        assert n["comfortSpeed"] == 30

    def test_three_valued_presence(self):
        n = wire.normalize_road(ROAD_A)
        assert n["cyclewayLeft"] == "present"    # True
        assert n["cyclewayRight"] == "unknown"   # None, NOT absent
        assert wire.normalize_road({**ROAD_A, "cyclewayLeft": False})["cyclewayLeft"] == "absent"

    def test_coords_scaled_to_fixed_point(self):
        n = wire.normalize_road(ROAD_A)
        assert n["points"][0] == [520100000, 50050000]
        assert n["points"][1] == [520100000, 50150000]

    def test_unknown_road_class_maps_to_unknown(self):
        assert wire.normalize_road({**ROAD_A, "roadClass": "cycleway"})["roadClass"] == "unknown"

    def test_maxspeed_clamped_to_byte(self):
        assert wire.normalize_road({**ROAD_A, "maxspeed": 999})["maxspeed"] == 255

    def test_slice1_record_without_spatial_attrs(self):
        rec = {"id": 5, "roadClass": "primary", "maxspeed": 50, "oneway": True,
               "lanes": 2, "name": None, "coords": [(52.0, 5.0)]}
        n = wire.normalize_road(rec)
        assert n["cyclewayLeft"] == "unknown" and n["inResidential"] is False
        assert n["name"] == ""

    def test_camera_less_road_omits_the_key(self):
        # keeps the content hash identical to before cameras existed -> no needless
        # re-sync of every camera-free tile.
        assert "cameras" not in wire.normalize_road(ROAD_A)

    def test_cameras_normalized_and_sorted(self):
        rec = {**ROAD_A, "cameras": [
            {"lat": 52.0001, "lon": 5.010, "maxspeed": 100, "kind": "fixed"},
            {"lat": 52.000, "lon": 5.002, "maxspeed": 100, "kind": "section"},
        ]}
        cams = wire.normalize_road(rec)["cameras"]
        assert [c["point"] for c in cams] == [[520000000, 50020000], [520001000, 50100000]]
        assert cams[0]["kind"] == "section" and cams[1]["kind"] == "fixed"
        assert cams[0]["maxspeed"] == 100


class TestNormalizeTileAndHash:
    def test_roads_sorted_by_id(self):
        a = {**ROAD_A, "id": 101}
        b = {**ROAD_A, "id": 7}
        norm = wire.normalize_tile(208, 20, [a, b])
        assert [r["id"] for r in norm["roads"]] == [7, 101]

    def test_hash_is_order_independent(self):
        a = {**ROAD_A, "id": 101}
        b = {**ROAD_A, "id": 7}
        h1 = wire.tile_content_hash(wire.normalize_tile(208, 20, [a, b]))
        h2 = wire.tile_content_hash(wire.normalize_tile(208, 20, [b, a]))
        assert h1 == h2

    def test_hash_changes_with_data(self):
        base = wire.tile_content_hash(wire.normalize_tile(208, 20, [ROAD_A]))
        moved = wire.tile_content_hash(
            wire.normalize_tile(208, 20, [{**ROAD_A, "maxspeed": 50}]))
        assert base != moved

    def test_hash_excludes_tile_position_is_included(self):
        h1 = wire.tile_content_hash(wire.normalize_tile(208, 20, [ROAD_A]))
        h2 = wire.tile_content_hash(wire.normalize_tile(999, 20, [ROAD_A]))
        assert h1 != h2   # tile position is part of the identity


class TestCapnpRoundTrip:
    def setup_method(self):
        pytest.importorskip("capnp")

    def test_round_trip_preserves_fields(self):
        norm = wire.normalize_tile(208, 20, [ROAD_A])
        out = wire.from_capnp_bytes(wire.serialize_tile(208, 20, [ROAD_A]))
        assert out["tileLat"] == 208 and out["tileLon"] == 20
        assert out["formatVersion"] == wire.FORMAT_VERSION
        assert out["contentHash"] == wire.tile_content_hash(norm)
        assert out["attribution"] == wire.ATTRIBUTION

        r = out["roads"][0]
        assert r["id"] == 101
        assert r["roadClass"] == "residential"
        assert r["maxspeed"] == 30 and r["comfortSpeed"] == 30
        assert r["cyclewayLeft"] == "present" and r["cyclewayRight"] == "unknown"
        assert r["inResidential"] is True
        assert r["calmingPerKm"] == pytest.approx(2.9, abs=1e-4)   # Float32
        assert r["points"][0] == [520100000, 50050000]

    def test_bytes_are_deterministic(self):
        b1 = wire.serialize_tile(208, 20, [ROAD_A], generated_at_unix_s=1234)
        b2 = wire.serialize_tile(208, 20, [ROAD_A], generated_at_unix_s=1234)
        assert b1 == b2

    def test_timestamp_changes_bytes_not_content_hash(self):
        norm_hash = wire.tile_content_hash(wire.normalize_tile(208, 20, [ROAD_A]))
        b_early = wire.serialize_tile(208, 20, [ROAD_A], generated_at_unix_s=1)
        b_late = wire.serialize_tile(208, 20, [ROAD_A], generated_at_unix_s=999)
        assert b_early != b_late
        assert wire.from_capnp_bytes(b_early)["contentHash"] == norm_hash
        assert wire.from_capnp_bytes(b_late)["contentHash"] == norm_hash

    def test_full_pipeline_fixture(self):
        data = parse_full(RESIDENTIAL)
        tiles = build_tiles_full(data)
        (tile_lat, tile_lon), records = next(
            (k, v) for k, v in tiles.items() if any(r["id"] == 101 for r in v))
        out = wire.from_capnp_bytes(wire.serialize_tile(tile_lat, tile_lon, records))
        road_a = next(r for r in out["roads"] if r["id"] == 101)
        assert road_a["inResidential"] is True
        assert road_a["comfortSpeed"] == 30
        assert road_a["cyclewayLeft"] == "present"
        assert len(road_a["points"]) >= 2

    def test_cameras_survive_the_round_trip(self):
        rec = {**ROAD_A, "cameras": [
            {"lat": 52.0001, "lon": 5.010, "maxspeed": 100, "kind": "fixed"}]}
        out = wire.from_capnp_bytes(wire.serialize_tile(208, 20, [rec]))
        cams = out["roads"][0]["cameras"]
        assert len(cams) == 1
        assert cams[0]["point"] == [520001000, 50100000]
        assert cams[0]["maxspeed"] == 100 and cams[0]["kind"] == "fixed"

    def test_cameras_stamped_by_build_tiles_full(self):
        tiles = build_tiles_full(parse_full(CAMERAS))
        records = [r for recs in tiles.values() for r in recs]
        mainline = next(r for r in records if r["id"] == 200)
        kinds = {c["kind"] for c in mainline["cameras"]}
        assert kinds == {"fixed", "section"}   # the standalone camera + the section
        ramp = next(r for r in records if r["id"] == 201)
        assert ramp["cameras"] and all(c["kind"] == "fixed" for c in ramp["cameras"])
