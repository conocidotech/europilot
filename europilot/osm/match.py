"""Match a vehicle pose to the OSM road it's driving on.

Pure geometry, no network, hot path. Given the roads from the current tile and a
pose, pick the nearest road whose direction agrees with the heading (undirected:
a two-way road's mapped geometry runs either way), and return its advisory
attributes. Cross-streets are filtered by the heading test; a pose too far from
any road yields Advisory.none() and the caller falls back to its other sources.

This mirrors the NDW matcher's split: the gateway hands over a region, the device
matches locally because lateral distance and which road you're on depend on a
pose that updates far faster than the gateway refreshes.
"""

from europilot.osm.types import Advisory, Road
from gateway.osm.geo import bearing, point_to_polyline_m, point_to_segment_m

MAX_LATERAL_M = 20.0          # must be this close to count as "on" the road
MAX_BEARING_DELTA_DEG = 45.0  # heading vs road direction, undirected


def _undirected_delta(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _nearest_segment_bearing(road: Road, pose: tuple[float, float]) -> float | None:
    pts = road.points
    if len(pts) < 2:
        return None
    best_i, best_d = 0, float("inf")
    for i in range(len(pts) - 1):
        d = point_to_segment_m(pose, pts[i], pts[i + 1])
        if d < best_d:
            best_i, best_d = i, d
    return bearing(pts[best_i], pts[best_i + 1])


def _heading_ok(road: Road, pose: tuple[float, float], heading: float | None) -> bool:
    if heading is None:
        return True
    seg = _nearest_segment_bearing(road, pose)
    if seg is None:
        return True   # single-point geometry: can't judge, don't exclude
    return _undirected_delta(seg, heading) <= MAX_BEARING_DELTA_DEG


def _advisory(road: Road, distance_m: float) -> Advisory:
    return Advisory(
        valid=True,
        road_id=road.id,
        road_class=road.road_class,
        name=road.name,
        speed_limit=road.maxspeed,
        comfort_speed=road.comfort_speed,
        in_residential=road.in_residential,
        cycleway_left=road.cycleway_left,
        cycleway_right=road.cycleway_right,
        cyclestreet=road.cyclestreet,
        distance_m=round(distance_m, 1),
    )


def match(pose: tuple[float, float], heading: float | None, roads: list[Road]) -> Advisory:
    """Best road for a (lat, lon) pose and heading, or Advisory.none()."""
    best: Road | None = None
    best_d = MAX_LATERAL_M
    for road in roads:
        if not road.points:
            continue
        d = point_to_polyline_m(pose, road.points)
        if d > best_d:
            continue
        if not _heading_ok(road, pose, heading):
            continue
        best, best_d = road, d
    return _advisory(best, best_d) if best is not None else Advisory.none()
