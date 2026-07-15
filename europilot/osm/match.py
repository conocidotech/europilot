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

import math

from europilot.osm.types import Advisory, Road
from gateway.osm.geo import bearing, point_to_polyline_m, point_to_segment_m

MAX_LATERAL_M = 20.0          # must be this close to count as "on" the road
MAX_BEARING_DELTA_DEG = 45.0  # heading vs road direction, undirected
CAMERA_MAX_OFFSET_M = 30.0    # a camera must project this close to the road to count
ROUNDABOUT_MAX_OFFSET_M = 20.0  # an entry node sits on the road, so keep this tight
CURVE_MAX_OFFSET_M = 10.0       # a curve marker is a vertex OF the road -> very tight
EARTH_R = 6_371_000.0


def _undirected_delta(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _to_xy(ref: tuple[float, float], p: tuple[float, float]) -> tuple[float, float]:
    """Local east/north meters of p relative to ref (fine over a tile)."""
    lat0 = math.radians(ref[0])
    return (math.radians(p[1] - ref[1]) * math.cos(lat0) * EARTH_R,
            math.radians(p[0] - ref[0]) * EARTH_R)


def _project(pts_xy: list, cum: list, q: tuple[float, float]) -> tuple[float, float]:
    """(arc-length position of q's foot along the polyline, perpendicular distance)."""
    best_s, best_d = 0.0, float("inf")
    for i in range(len(pts_xy) - 1):
        ax, ay = pts_xy[i]
        bx, by = pts_xy[i + 1]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        if seg2 == 0:
            continue
        t = max(0.0, min(1.0, ((q[0] - ax) * dx + (q[1] - ay) * dy) / seg2))
        fx, fy = ax + t * dx, ay + t * dy
        d = math.hypot(q[0] - fx, q[1] - fy)
        if d < best_d:
            best_d, best_s = d, cum[i] + t * math.sqrt(seg2)
    return best_s, best_d


def _travels_forward(road: Road, pose: tuple[float, float], heading: float) -> bool | None:
    """Whether the ego travels in the polyline's drawn direction (increasing arc length)."""
    seg = _nearest_segment_bearing(road, pose)   # forward bearing of the nearest segment
    if seg is None:
        return None
    delta = abs((heading - seg + 180.0) % 360.0 - 180.0)   # 0..180, directed
    return delta <= 90.0


def next_camera_ahead(pose: tuple[float, float], heading: float | None,
                      road: Road) -> tuple[float, int | None, str] | None:
    """(distance_m, enforced_limit, kind) of the nearest camera ahead on this road.

    Ahead means in the ego's travel direction along the matched road. Needs a
    heading -- without one we can't tell ahead from behind, so we return None
    (fail-safe: better a missed camera than easing off for one behind us).
    """
    if heading is None or not road.cameras or len(road.points) < 2:
        return None
    ref = road.points[0]
    pts = [_to_xy(ref, p) for p in road.points]
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]))

    forward = _travels_forward(road, pose, heading)
    if forward is None:
        return None
    s_pose, _ = _project(pts, cum, _to_xy(ref, pose))

    best: tuple[float, int | None, str] | None = None
    for cam in road.cameras:
        s_cam, off = _project(pts, cum, _to_xy(ref, (cam.lat, cam.lon)))
        if off > CAMERA_MAX_OFFSET_M:
            continue
        ahead = (s_cam - s_pose) if forward else (s_pose - s_cam)
        if ahead <= 0:
            continue
        if best is None or ahead < best[0]:
            best = (round(ahead, 1), cam.maxspeed, cam.kind)
    return best


def next_roundabout_ahead(pose: tuple[float, float], heading: float | None,
                          road: Road) -> tuple[float, str, int] | None:
    """(distance_m, kind, radius_m) of the nearest roundabout ahead on this road.

    Same shape and fail-safe as next_camera_ahead: needs a heading to tell ahead
    from behind, and the entry node must project onto the road. Returns None when
    there is nothing ahead -- better a missed ease-off than one for a roundabout
    already behind us.
    """
    if heading is None or not road.roundabouts or len(road.points) < 2:
        return None
    ref = road.points[0]
    pts = [_to_xy(ref, p) for p in road.points]
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]))

    forward = _travels_forward(road, pose, heading)
    if forward is None:
        return None
    s_pose, _ = _project(pts, cum, _to_xy(ref, pose))

    best: tuple[float, str, int] | None = None
    for rb in road.roundabouts:
        s_rb, off = _project(pts, cum, _to_xy(ref, (rb.lat, rb.lon)))
        if off > ROUNDABOUT_MAX_OFFSET_M:
            continue
        ahead = (s_rb - s_pose) if forward else (s_pose - s_rb)
        if ahead <= 0:
            continue
        if best is None or ahead < best[0]:
            best = (round(ahead, 1), rb.kind, rb.radius_m)
    return best


def next_curve_ahead(pose: tuple[float, float], heading: float | None,
                     road: Road) -> tuple[float, int] | None:
    """(distance_m, radius_m) of the nearest sharp bend ahead on this road.

    Same shape and fail-safe as next_roundabout_ahead: needs a heading to tell
    ahead from behind, and the marker (a vertex of the road) must project onto it.
    Returns None when nothing is ahead -- better a missed ease-off than one for a
    bend already behind us.
    """
    if heading is None or not road.curves or len(road.points) < 2:
        return None
    ref = road.points[0]
    pts = [_to_xy(ref, p) for p in road.points]
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]))

    forward = _travels_forward(road, pose, heading)
    if forward is None:
        return None
    s_pose, _ = _project(pts, cum, _to_xy(ref, pose))

    best: tuple[float, int] | None = None
    for cv in road.curves:
        s_cv, off = _project(pts, cum, _to_xy(ref, (cv.lat, cv.lon)))
        if off > CURVE_MAX_OFFSET_M:
            continue
        ahead = (s_cv - s_pose) if forward else (s_pose - s_cv)
        if ahead <= 0:
            continue
        if best is None or ahead < best[0]:
            best = (round(ahead, 1), cv.radius_m)
    return best


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


def _advisory(road: Road, distance_m: float, pose: tuple[float, float],
              heading: float | None) -> Advisory:
    cam = next_camera_ahead(pose, heading, road)
    rb = next_roundabout_ahead(pose, heading, road)
    cv = next_curve_ahead(pose, heading, road)
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
        camera_distance_m=cam[0] if cam else None,
        camera_limit=cam[1] if cam else None,
        camera_kind=cam[2] if cam else "",
        roundabout_distance_m=rb[0] if rb else None,
        roundabout_kind=rb[1] if rb else "",
        roundabout_radius_m=rb[2] if rb else 0,
        curve_distance_m=cv[0] if cv else None,
        curve_radius_m=cv[1] if cv else 0,
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
    return _advisory(best, best_d, pose, heading) if best is not None else Advisory.none()
