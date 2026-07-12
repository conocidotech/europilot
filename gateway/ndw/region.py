"""Build the region snapshots the device consumes.

The gateway holds the NDW knowledge: where every sign is, and what it currently
shows. It hands the device one tile at a time, already normalised, so the device
can match locally without ever contacting a third party.

Tiles are 0.25 degrees, matching the OSM distribution decision. A tile is padded
by the matcher's search radius, otherwise a car near a tile edge would not see
the gantry it is about to pass.
"""

import math

from europilot.ndw.types import Display, Sign

TILE_DEG = 0.25
PAD_M = 2500.0
EARTH_RADIUS_M = 6_371_000.0


def tile_bounds(tile_lat: int, tile_lon: int) -> tuple[float, float, float, float]:
    """(min_lat, min_lon, max_lat, max_lon), padded so edge poses still match."""
    min_lat, min_lon = tile_lat * TILE_DEG, tile_lon * TILE_DEG
    max_lat, max_lon = min_lat + TILE_DEG, min_lon + TILE_DEG

    pad_lat = math.degrees(PAD_M / EARTH_RADIUS_M)
    mid_lat = math.radians((min_lat + max_lat) / 2.0)
    pad_lon = pad_lat / max(math.cos(mid_lat), 1e-6)

    return (min_lat - pad_lat, min_lon - pad_lon, max_lat + pad_lat, max_lon + pad_lon)


def signs_in_tile(signs: list[Sign], tile_lat: int, tile_lon: int) -> list[Sign]:
    min_lat, min_lon, max_lat, max_lon = tile_bounds(tile_lat, tile_lon)
    return [s for s in signs if min_lat <= s.lat <= max_lat and min_lon <= s.lon <= max_lon]


def build(
    signs: list[Sign],
    states: dict[str, Display],
    tile_lat: int,
    tile_lon: int,
    age_s: float,
) -> dict:
    """The payload the device receives. Signs without a live state are dropped.

    `bounds` is the padded box the signs were taken from. The device needs it to
    know how far outside the raw tile this snapshot still answers correctly.
    """
    tile_signs = [s for s in signs_in_tile(signs, tile_lat, tile_lon) if s.uuid in states]
    min_lat, min_lon, max_lat, max_lon = tile_bounds(tile_lat, tile_lon)
    return {
        "tile_lat": tile_lat,
        "tile_lon": tile_lon,
        "tile_deg": TILE_DEG,
        "bounds": {"min_lat": min_lat, "min_lon": min_lon, "max_lat": max_lat, "max_lon": max_lon},
        "age_s": round(age_s, 1),
        "signs": [s.to_json() for s in tile_signs],
        "states": {s.uuid: states[s.uuid].to_json() for s in tile_signs},
    }
