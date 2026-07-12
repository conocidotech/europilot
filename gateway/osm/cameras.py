"""Attach speed cameras and average-speed sections to the road they enforce.

Done server-side and ROAD-ATTACHED on purpose: the device must never react to a
camera that sits on the off-ramp while it stays on the motorway. So a camera is
tied to a specific way here, not served as a loose "nearest point" the device
would have to disambiguate at speed.

Two OSM sources:
  - `type=enforcement` relations -- authoritative: they name the way(s) they
    enforce and carry the `maxspeed`. `enforcement=average_speed` is
    trajectcontrole (a section); its point is the section start (`from`).
  - standalone `highway=speed_camera` nodes -- attached to the nearest drivable
    road within a tight tolerance, only if not already claimed by a relation.

Advisory data only. Unknown limit stays 0 (the device defers to the road limit).
"""

from gateway.osm import tags as osm_tags
from gateway.osm.geo import point_to_polyline_m

# A standalone camera must be within this of a road's geometry to attach to it.
# Tight, so a camera between the main road and its ramp does not bind to both.
MAX_ATTACH_M = 15.0


def _int_or_none(value) -> int | None:
    raw = str(value or "").strip()
    return int(raw) if raw.isdigit() else None


def _camera(lat: float, lon: float, maxspeed: int | None, kind: str) -> dict:
    return {"lat": lat, "lon": lon, "maxspeed": maxspeed, "kind": kind}


def _relation_point(rel, data, kind: str):
    """A representative (lat, lon) for an enforcement relation, or None.

    Section: the `from` node (where enforcement begins). Fixed: a `device` node.
    Falls back to any node member so a sparsely-tagged relation still yields one.
    """
    by_role: dict = {}
    for m in rel.members:
        if m.type == "node" and m.ref in data.nodes:
            by_role.setdefault(m.role, data.nodes[m.ref])
    if kind == "section" and "from" in by_role:
        return by_role["from"]
    if "device" in by_role:
        return by_role["device"]
    return next(iter(by_role.values()), None)


def _nearest_drivable_way(point, data, ways_by_id) -> int | None:
    best_id, best_d = None, MAX_ATTACH_M
    for way in data.ways:
        if osm_tags.road_class(way.tags) is None:
            continue   # only attach to kept drivable roads, never a cycleway etc.
        coords = data.coords(way.node_ids)
        if len(coords) < 2:
            continue
        d = point_to_polyline_m(point, coords)
        if d < best_d:
            best_id, best_d = way.id, d
    return best_id


def _relation_camera_node_ids(data) -> set:
    ids = set()
    for rel in data.relations:
        if rel.tags.get("type") != "enforcement":
            continue
        for m in rel.members:
            if m.type == "node":
                ids.add(m.ref)
    return ids


def cameras_by_way(data) -> dict[int, list[dict]]:
    """way_id -> the enforcement points attached to that way."""
    ways_by_id = {w.id: w for w in data.ways}
    out: dict[int, list[dict]] = {}

    # 1) enforcement relations: authoritative way attachment + limit.
    for rel in data.relations:
        if rel.tags.get("type") != "enforcement":
            continue
        kind = "section" if rel.tags.get("enforcement") == "average_speed" else "fixed"
        point = _relation_point(rel, data, kind)
        if point is None:
            continue
        limit = _int_or_none(rel.tags.get("maxspeed"))
        for m in rel.members:
            if m.type == "way" and m.ref in ways_by_id:
                out.setdefault(m.ref, []).append(_camera(point[0], point[1], limit, kind))

    # 2) standalone highway=speed_camera nodes -> nearest drivable road.
    claimed = _relation_camera_node_ids(data)
    for nid, node_tags in data.node_tags.items():
        if node_tags.get("highway") != "speed_camera" or nid in claimed or nid not in data.nodes:
            continue
        lat, lon = data.nodes[nid]
        way_id = _nearest_drivable_way((lat, lon), data, ways_by_id)
        if way_id is None:
            continue
        out.setdefault(way_id, []).append(
            _camera(lat, lon, _int_or_none(node_tags.get("maxspeed")), "fixed"))

    return out
