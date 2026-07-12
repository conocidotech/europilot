"""The lat/lon grid the OSM tiles are baked into.

Same 0.25 degree data tiles as the NDW region snapshots and mapd's grid: the
device works out which cell its GPS position is in and reads that one tile, no
database and no position query to the server. Tiles are grouped into 2 degree
folders on the server side so a directory never holds too many files.

This module is pure geometry -- no I/O, no OSM parsing.
"""

import math

# mapd's AREA_BOX_DEGREES / GROUP_AREA_BOX_DEGREES; see the OSM distribution
# decision doc.
TILE_DEG = 0.25
GROUP_DEG = 2.0

EARTH_RADIUS_M = 6_371_000.0


def tile_of(lat: float, lon: float) -> tuple[int, int]:
    """The (tile_lat, tile_lon) integer cell index containing a position."""
    return (int(math.floor(lat / TILE_DEG)), int(math.floor(lon / TILE_DEG)))


def group_of(tile_lat: int, tile_lon: int) -> tuple[int, int]:
    """The 2 degree group folder a tile belongs to."""
    step = int(round(GROUP_DEG / TILE_DEG))
    return (math.floor(tile_lat / step) * step, math.floor(tile_lon / step) * step)


def tile_bounds(tile_lat: int, tile_lon: int) -> tuple[float, float, float, float]:
    """(min_lat, min_lon, max_lat, max_lon) of a tile cell."""
    min_lat, min_lon = tile_lat * TILE_DEG, tile_lon * TILE_DEG
    return (min_lat, min_lon, min_lat + TILE_DEG, min_lon + TILE_DEG)


def centroid(coords: list[tuple[float, float]]) -> tuple[float, float]:
    """Mean (lat, lon) of a way's node coordinates.

    A way is assigned to the tile its centroid falls in. Long ways that straddle
    a tile edge are handled by the device reading a padded neighbourhood, the
    same way the NDW client does; that padding is a serving-layer concern, not a
    tiling one.
    """
    n = len(coords)
    if n == 0:
        raise ValueError("empty way")
    return (sum(c[0] for c in coords) / n, sum(c[1] for c in coords) / n)
