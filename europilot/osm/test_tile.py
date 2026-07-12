"""Tile decode tests: the device reads exactly what the gateway serialized."""

from pathlib import Path

import pytest

pytest.importorskip("capnp")

from europilot.osm.tile import decode_tile
from gateway.osm.osm_source import parse_full
from gateway.osm.tiles import build_tiles_full
from gateway.osm.wire import serialize_tile

RESIDENTIAL = (Path(__file__).resolve().parents[2]
               / "gateway" / "osm" / "test_data" / "residential.osm").read_bytes()


def _road_a_tile():
    tiles = build_tiles_full(parse_full(RESIDENTIAL))
    (lat, lon), records = next((k, v) for k, v in tiles.items()
                               if any(r["id"] == 101 for r in v))
    return lat, lon, records


class TestDecode:
    def test_round_trip_from_gateway_bytes(self):
        lat, lon, records = _road_a_tile()
        roads = decode_tile(serialize_tile(lat, lon, records))
        a = next(r for r in roads if r.id == 101)
        assert a.road_class == "residential"
        assert a.maxspeed == 30 and a.comfort_speed == 30
        assert a.in_residential is True
        assert a.cycleway_left == "present" and a.cycleway_right == "unknown"
        assert a.name == "Speeltuinlaan"
        assert len(a.points) >= 2
        assert a.points[0] == pytest.approx((52.010, 5.005), abs=1e-6)

    def test_zero_sentinels_become_none(self):
        # A minimal record with unknown maxspeed/lanes/comfort.
        rec = {"id": 9, "roadClass": "primary", "maxspeed": None, "oneway": True,
               "lanes": None, "name": None, "coords": [(52.0, 5.0), (52.0, 5.001)]}
        road = decode_tile(serialize_tile(208, 20, [rec]))[0]
        assert road.maxspeed is None and road.lanes is None and road.comfort_speed is None
        assert road.name == ""
