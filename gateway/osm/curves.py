"""Detect sharp bends along each road and attach them as curve markers.

Map Turn Speed Control (MTSC): like roundabouts (gateway/osm/roundabouts.py),
this is done SERVER-SIDE and ROAD-ATTACHED. A bend's geometry is pose-independent
so deriving it belongs on the gateway; the device only matches the pose and eases
toward a cornering speed sized by the baked radius. Advisory data only -- there
is no posted limit at a bend.

For each drivable way we walk its polyline and take the turning radius at every
interior vertex (the circumradius of the triangle it makes with its two
neighbours). A run of consecutive vertices below the threshold is ONE bend; we
emit a single marker at the tightest vertex of the run carrying that minimum
radius, so a road drawn with many nodes through a curve yields one marker, not
dozens.
"""

import math

from gateway.osm import tags as osm_tags
from gateway.osm.geo import meters_between

# Only bends tighter than this get a marker: at ~300 m radius the comfortable
# cornering speed is already ~85 km/h, so gentler curves never trigger an ease-off
# and would only bloat the tile. Also the stored-radius ceiling (fits UInt16).
_CURVE_THRESHOLD_M = 300
# Segments shorter than this around a vertex are treated as mapping noise, not a
# real bend -- avoids spurious tight radii from jittery geometry.
_MIN_SEG_M = 3.0
_M_PER_DEG = 111_320.0


def _curve(lat: float, lon: float, radius_m: int) -> dict:
    return {"lat": lat, "lon": lon, "radius_m": radius_m}


def _turn_radius_m(a: tuple[float, float], b: tuple[float, float],
                   c: tuple[float, float]) -> float:
    """Circumradius (m) of the triangle a-b-c; inf when a-b-c is straight/noisy.

    R = |ab|*|bc|*|ca| / (4*area). Small radius == tight bend at vertex b.
    """
    ab = meters_between(a, b)
    bc = meters_between(b, c)
    ca = meters_between(c, a)
    if ab < _MIN_SEG_M or bc < _MIN_SEG_M:
        return float("inf")   # noise around the vertex; the angle is untrustworthy
    # 2*area via the cross product of BA and BC in a local metric frame around b.
    cos_b = math.cos(math.radians(b[0]))
    ax, ay = (a[1] - b[1]) * _M_PER_DEG * cos_b, (a[0] - b[0]) * _M_PER_DEG
    cx, cy = (c[1] - b[1]) * _M_PER_DEG * cos_b, (c[0] - b[0]) * _M_PER_DEG
    area2 = abs(ax * cy - ay * cx)
    if area2 < 1e-6:
        return float("inf")
    return (ab * bc * ca) / (2.0 * area2)


def curves_by_way(data) -> dict[int, list[dict]]:
    """way_id -> the curve markers along that way (one per detected bend)."""
    out: dict[int, list[dict]] = {}
    for way in data.ways:
        if osm_tags.road_class(way.tags) is None:
            continue   # only drivable roads, never a cycleway/footway etc.
        pts = [p for p in data.coords(way.node_ids) if p]
        if len(pts) < 3:
            continue

        n = len(pts) - 1
        radii = [float("inf")] * len(pts)
        for i in range(1, n):
            radii[i] = _turn_radius_m(pts[i - 1], pts[i], pts[i + 1])

        markers: list[dict] = []
        i = 1
        while i < n:
            if radii[i] >= _CURVE_THRESHOLD_M:
                i += 1
                continue
            # start of a bend run -> walk it, keep the tightest vertex.
            best_i, best_r = i, radii[i]
            while i < n and radii[i] < _CURVE_THRESHOLD_M:
                if radii[i] < best_r:
                    best_i, best_r = i, radii[i]
                i += 1
            lat, lon = pts[best_i]
            markers.append(_curve(lat, lon, min(_CURVE_THRESHOLD_M, max(1, round(best_r)))))

        if markers:
            out[way.id] = markers
    return out
