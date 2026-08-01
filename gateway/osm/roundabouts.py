"""Attach roundabouts to the roads that approach them.

Like cameras (gateway/osm/cameras.py), this is done server-side and ROAD-ATTACHED
on purpose: the device must ease off only for a roundabout that is actually on the
road it is driving, reached at the roundabout's entry node -- not a loose "nearest
circle" it would have to disambiguate at speed.

Two OSM sources:
  - `junction=roundabout` ways -- the circular carriageway. Every road that
    enters/leaves shares a node with the ring (the entry point); the roundabout
    is attached to those approach roads at that shared node.
  - `highway=mini_roundabout` nodes -- a painted mini-roundabout sitting on the
    road itself; attached to every drivable way that passes through the node.

The circular way itself is never given a roundabout (you are already on it).
Advisory data only: the device decides a comfortable approach speed; nothing here
is a posted limit.
"""

from gateway.osm import tags as osm_tags
from gateway.osm.geo import meters_between
from gateway.osm.grid import centroid

# Radius is baked so the device can pick a sensible APPROACH speed per roundabout
# (a big roundabout is neared faster than a mini). Clamped to the UInt8 wire field;
# anything past this already maps to the max approach speed device-side.
_MAX_RADIUS_M = 255


def _roundabout(lat: float, lon: float, kind: str, radius_m: int) -> dict:
    return {"lat": lat, "lon": lon, "kind": kind, "radius_m": radius_m}


def _ring_radius_m(ring_coords: list[tuple[float, float]]) -> int:
    """Mean distance from the ring's centroid to its nodes, in metres (rounded).

    Robust for the roughly-circular carriageway of a roundabout; 0 if degenerate.
    """
    pts = [p for p in ring_coords if p]
    if len(pts) < 3:
        return 0
    c = centroid(pts)
    r = sum(meters_between(c, p) for p in pts) / len(pts)
    return min(_MAX_RADIUS_M, max(0, round(r)))


def _node_to_drivable_ways(data) -> dict[int, list]:
    """node id -> the kept drivable ways that pass through it."""
    out: dict[int, list] = {}
    for way in data.ways:
        if osm_tags.road_class(way.tags) is None:
            continue   # only attach to drivable roads, never a cycleway etc.
        for nid in way.node_ids:
            out.setdefault(nid, []).append(way)
    return out


def roundabouts_by_way(data) -> dict[int, list[dict]]:
    """way_id -> the roundabout approach points attached to that way."""
    node_to_ways = _node_to_drivable_ways(data)
    out: dict[int, list[dict]] = {}

    def attach(way_id: int, lat: float, lon: float, kind: str, radius_m: int) -> None:
        pts = out.setdefault(way_id, [])
        entry = _roundabout(lat, lon, kind, radius_m)
        if entry not in pts:   # a road touches a ring once; guard against dupes
            pts.append(entry)

    # 1) junction=roundabout ways -> the approach roads sharing a ring node.
    for ring in data.ways:
        if ring.tags.get("junction") != "roundabout":
            continue
        radius_m = _ring_radius_m(data.coords(ring.node_ids))
        for nid in ring.node_ids:
            if nid not in data.nodes:
                continue
            lat, lon = data.nodes[nid]
            for way in node_to_ways.get(nid, []):
                if way.id == ring.id:
                    continue   # you are already on the roundabout
                attach(way.id, lat, lon, "roundabout", radius_m)

    # 2) highway=mini_roundabout nodes -> every drivable way through the node.
    # A mini has no ring geometry, so radius 0 -> the device uses its mini default.
    for nid, node_tags in data.node_tags.items():
        if node_tags.get("highway") != "mini_roundabout" or nid not in data.nodes:
            continue
        lat, lon = data.nodes[nid]
        for way in node_to_ways.get(nid, []):
            attach(way.id, lat, lon, "mini", 0)

    return out
