"""Server-side spatial joins: the attributes a single way's tags can't answer.

These are the expensive derivations the story wants baked into the tiles so the
device reads a field instead of computing a point-in-polygon on a Snapdragon:

  - is a road inside a landuse=residential area (needs OSM relations, not just
    closed ways -- mapd skips relations; we don't)
  - traffic_calming / highway=crossing density per km along a road
  - a residential_score and the comfort speed that follows from it
  - cycleway_left / cycleway_right, from BOTH the cycleway:* tags on the road and
    parallel highway=cycleway ways (a geometric join, because in NL cycle paths
    are mapped as separate ways ~5:1 over on-road tags)

Everything three-valued where the story asks for it: present / absent / unknown,
never "no tag" silently becoming "no cycleway".
"""

import math

from gateway.osm.geo import (
    bearing, point_in_polygon, point_to_polyline_m, side_of_segment, way_length_m,
)
from gateway.osm.osm_source import OsmData

# --- landuse=residential membership -----------------------------------------

def _is_residential_area(tags: dict) -> bool:
    return tags.get("landuse") == "residential"


def residential_polygons(data: OsmData) -> list[tuple[list, list]]:
    """(outer_ring, [hole_rings]) polygons for every residential area.

    Covers closed-way areas and multipolygon relations whose members are closed
    ways. Relations built by stitching several open ways into one ring are a
    documented follow-up; closed-way members are the common case.
    """
    polygons: list[tuple[list, list]] = []

    for way in data.ways:
        if _is_residential_area(way.tags):
            ring = data.coords(way.node_ids)
            if len(ring) >= 3:
                polygons.append((ring, []))

    ways_by_id = {w.id: w for w in data.ways}
    for rel in data.relations:
        if not _is_residential_area(rel.tags) and rel.tags.get("type") != "multipolygon":
            continue
        if rel.tags.get("landuse") != "residential":
            continue
        outers, inners = [], []
        for m in rel.members:
            way = ways_by_id.get(m.ref) if m.type == "way" else None
            if way is None:
                continue
            ring = data.coords(way.node_ids)
            if len(ring) >= 3:
                (inners if m.role == "inner" else outers).append(ring)
        for outer in outers:
            holes = [h for h in inners if point_in_polygon(h[0], outer, [])]
            polygons.append((outer, holes))

    return polygons


def _sample_along(coords: list, n: int = 9) -> list:
    """n points spread evenly by arc length along the polyline.

    Degrees are fine as the distance metric here -- we only need even-ish spacing
    to test membership, not true metric length.
    """
    if len(coords) <= 1:
        return list(coords)
    cum = [0.0]
    for a, b in zip(coords, coords[1:], strict=False):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = cum[-1]
    if total == 0:
        return [coords[0]]
    pts = []
    for i in range(n):
        target = total * i / (n - 1)
        j = 0
        while j < len(coords) - 2 and cum[j + 1] < target:
            j += 1
        seg = cum[j + 1] - cum[j]
        f = 0.0 if seg == 0 else (target - cum[j]) / seg
        a, b = coords[j], coords[j + 1]
        pts.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return pts


def in_residential(way_coords: list, polygons: list[tuple[list, list]],
                   min_frac: float = 0.5) -> bool:
    """Whether a way lies mostly inside a residential polygon.

    A single representative point both misses a road that only partly enters an
    area and flags a through-road that merely crosses one at its midpoint. So we
    sample along the way and require at least ``min_frac`` of the samples inside.
    """
    if not way_coords or not polygons:
        return False
    samples = _sample_along(way_coords)
    if not samples:
        return False
    inside = sum(1 for p in samples
                 if any(point_in_polygon(p, outer, holes) for outer, holes in polygons))
    return inside / len(samples) >= min_frac


# --- node densities along a road --------------------------------------------

def tagged_points(data: OsmData, predicate) -> list[tuple[float, float]]:
    """(lat, lon) of every tagged node whose tags satisfy predicate."""
    return [data.nodes[nid] for nid, tags in data.node_tags.items()
            if nid in data.nodes and predicate(tags)]


def density_per_km(way_coords: list, points: list, buffer_m: float = 15.0) -> float:
    """Count points within buffer_m of the way, per km of way length."""
    length_km = way_length_m(way_coords) / 1000.0
    if length_km <= 0:
        return 0.0
    near = sum(1 for p in points if point_to_polyline_m(p, way_coords) <= buffer_m)
    return near / length_km


# --- cycleway sidedness ------------------------------------------------------

_NO_CYCLEWAY = {"no", "none", "separate", ""}


def _tag_present(value: str) -> bool:
    return value.strip().lower() not in _NO_CYCLEWAY


def cycleway_tag_sides(tags: dict) -> tuple[bool | None, bool | None]:
    """(left, right) from the on-road cycleway:* tags. None = tag absent."""
    left = right = None
    if "cycleway:both" in tags or "cycleway" in tags:
        val = tags.get("cycleway:both", tags.get("cycleway", ""))
        left = right = _tag_present(val)
    if "cycleway:left" in tags:
        left = _tag_present(tags["cycleway:left"])
    if "cycleway:right" in tags:
        right = _tag_present(tags["cycleway:right"])
    return left, right


def _parallel(b1: float, b2: float, tol: float) -> bool:
    d = abs(b1 - b2) % 180.0
    return min(d, 180.0 - d) <= tol


def _side_to_road(point: tuple[float, float], road: list) -> int:
    """Which side of the road the point is on, using the nearest road segment."""
    best_i, best_d = 0, float("inf")
    for i in range(len(road) - 1):
        d = point_to_polyline_m(point, [road[i], road[i + 1]])
        if d < best_d:
            best_i, best_d = i, d
    return side_of_segment(road[best_i], road[best_i + 1], point)


def cycleway_geometry_sides(road: list, cycleways: list[list], max_m: float = 20.0,
                            min_frac: float = 0.5, bearing_tol: float = 25.0) -> tuple[bool, bool]:
    """(left, right) from parallel highway=cycleway ways running beside the road."""
    left = right = False
    if len(road) < 2:
        return left, right
    road_brg = bearing(road[0], road[-1])
    for cw in cycleways:
        if len(cw) < 2:
            continue
        near = [p for p in cw if point_to_polyline_m(p, road) <= max_m]
        if len(near) / len(cw) < min_frac:
            continue
        if not _parallel(road_brg, bearing(cw[0], cw[-1]), bearing_tol):
            continue
        side = _side_to_road(cw[len(cw) // 2], road)
        if side > 0:
            left = True
        elif side < 0:
            right = True
    return left, right


def combine_side(tag: bool | None, geom: bool) -> bool | None:
    """Fuse the tag and geometry evidence for one side into present/absent/unknown."""
    if geom or tag is True:
        return True
    if tag is False:
        return False
    return None   # no tag, no parallel way seen -> unknown


def cyclestreet(tags: dict) -> bool:
    """NL fietsstraat is cyclestreet=yes; bicycle_road=yes barely occurs in NL."""
    return tags.get("cyclestreet") == "yes" or tags.get("bicycle_road") == "yes"


# --- residential score + comfort speed --------------------------------------

def residential_score(*, road_class: str, maxspeed: int | None, living_street: bool,
                      calming_per_km: float, crossing_per_km: float,
                      in_residential: bool) -> float:
    """A 0..1 signal that a road is a calm residential street.

    A weighted sum of independent cues; tuned to be easy to reason about, not
    learned. The comfort speed follows from it in comfort_speed().
    """
    score = 0.0
    if in_residential:
        score += 0.40
    if road_class in ("residential", "living_street", "service"):
        score += 0.25
    if living_street:
        score += 0.20
    if maxspeed is not None and maxspeed <= 30:
        score += 0.20
    score += min(0.30, 0.10 * calming_per_km)
    score += min(0.15, 0.05 * crossing_per_km)
    return round(min(1.0, score), 3)


def comfort_speed(score: float, maxspeed: int | None) -> int | None:
    """Advisory comfort speed (km/h) from the score, or None to defer to posted.

    Never above the posted limit. A simple, tunable mapping -- the exact policy
    is EUROPILOT-46's call.
    """
    if score >= 0.6:
        target = 30
    elif score >= 0.35:
        target = 40
    else:
        return None
    return min(target, maxspeed) if maxspeed is not None else target
