"""Planar geometry primitives for the tile derivations.

All the spatial joins (is this road inside a residential area, is there a
cycleway parallel to it, how many bumps per km) reduce to a handful of
operations. At tile scale a local equirectangular projection to metres is
accurate enough and far simpler than a full geodesic library, so that's what we
use for distances and sidedness; point-in-polygon runs directly on lat/lon
since it's purely topological.

Coordinates are (lat, lon) degrees throughout.
"""

import math

_M_PER_DEG = 111_320.0  # metres per degree of latitude


def meters_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine distance between two (lat, lon) points, in metres."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(min(1.0, math.sqrt(h)))


def way_length_m(coords: list[tuple[float, float]]) -> float:
    return sum(meters_between(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _to_local(p: tuple[float, float], ref: tuple[float, float]) -> tuple[float, float]:
    """(lat, lon) -> (x east, y north) metres, in a local frame around ref."""
    y = (p[0] - ref[0]) * _M_PER_DEG
    x = (p[1] - ref[1]) * _M_PER_DEG * math.cos(math.radians(ref[0]))
    return (x, y)


def point_to_segment_m(p: tuple[float, float], a: tuple[float, float],
                       b: tuple[float, float]) -> float:
    """Shortest distance from point p to segment a-b, in metres."""
    px, py = _to_local(p, p)   # (0, 0)
    ax, ay = _to_local(a, p)
    bx, by = _to_local(b, p)
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0.0:
        t = 0.0
    else:
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def point_to_polyline_m(p: tuple[float, float], line: list[tuple[float, float]]) -> float:
    """Shortest distance from p to a polyline (metres). inf for an empty line."""
    if len(line) == 1:
        return meters_between(p, line[0])
    return min((point_to_segment_m(p, line[i], line[i + 1]) for i in range(len(line) - 1)),
               default=float("inf"))


def side_of_segment(a: tuple[float, float], b: tuple[float, float],
                    p: tuple[float, float]) -> int:
    """Which side of directed segment a->b the point p is on.

    +1 = left, -1 = right, 0 = collinear -- relative to the a->b direction.
    """
    ax, ay = _to_local(a, a)
    bx, by = _to_local(b, a)
    px, py = _to_local(p, a)
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    return (cross > 0) - (cross < 0)


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Compass-ish bearing of a->b in degrees [0, 360)."""
    x, y = _to_local(b, a)
    return math.degrees(math.atan2(x, y)) % 360.0


def point_in_ring(p: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon for a single ring of (lat, lon) points."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        yi, xi = ring[i]      # lat, lon
        yj, xj = ring[j]
        if (yi > p[0]) != (yj > p[0]):
            x_cross = (xj - xi) * (p[0] - yi) / (yj - yi) + xi
            if p[1] < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_polygon(p: tuple[float, float], outer: list[tuple[float, float]],
                     holes: list[list[tuple[float, float]]] | None = None) -> bool:
    """Inside the outer ring and not inside any hole (multipolygon member)."""
    if not point_in_ring(p, outer):
        return False
    for hole in holes or []:
        if point_in_ring(p, hole):
            return False
    return True
