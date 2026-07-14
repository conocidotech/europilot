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


def _roundabout(lat: float, lon: float, kind: str) -> dict:
    return {"lat": lat, "lon": lon, "kind": kind}


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

    def attach(way_id: int, lat: float, lon: float, kind: str) -> None:
        pts = out.setdefault(way_id, [])
        entry = _roundabout(lat, lon, kind)
        if entry not in pts:   # a road touches a ring once; guard against dupes
            pts.append(entry)

    # 1) junction=roundabout ways -> the approach roads sharing a ring node.
    for ring in data.ways:
        if ring.tags.get("junction") != "roundabout":
            continue
        for nid in ring.node_ids:
            if nid not in data.nodes:
                continue
            lat, lon = data.nodes[nid]
            for way in node_to_ways.get(nid, []):
                if way.id == ring.id:
                    continue   # you are already on the roundabout
                attach(way.id, lat, lon, "roundabout")

    # 2) highway=mini_roundabout nodes -> every drivable way through the node.
    for nid, node_tags in data.node_tags.items():
        if node_tags.get("highway") != "mini_roundabout" or nid not in data.nodes:
            continue
        lat, lon = data.nodes[nid]
        for way in node_to_ways.get(nid, []):
            attach(way.id, lat, lon, "mini")

    return out
