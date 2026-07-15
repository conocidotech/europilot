"""Attach stop / give-way signs to the roads they govern.

Server-side and ROAD-ATTACHED like roundabouts (gateway/osm/roundabouts.py): a
highway=stop / highway=give_way node sits on the road it controls, so we attach
it to every drivable way passing through that node. The device then finds the
next one ahead and shows a HEADS-UP -- advisory only, never an ease (you slow for
the actual situation, not for a sign you may already be stopping for).
"""

from gateway.osm import tags as osm_tags

# OSM highway value -> our wire enum name.
_SIGN_KINDS = {"stop": "stop", "give_way": "giveWay"}


def _sign(lat: float, lon: float, kind: str) -> dict:
    return {"lat": lat, "lon": lon, "kind": kind}


def signs_by_way(data) -> dict[int, list[dict]]:
    """way_id -> the stop/give-way signs sitting on that way (at their nodes)."""
    node_to_ways: dict[int, list] = {}
    for way in data.ways:
        if osm_tags.road_class(way.tags) is None:
            continue   # only drivable roads, never a cycleway/footway
        for nid in way.node_ids:
            node_to_ways.setdefault(nid, []).append(way)

    out: dict[int, list[dict]] = {}
    for nid, node_tags in data.node_tags.items():
        kind = _SIGN_KINDS.get(node_tags.get("highway"))
        if kind is None or nid not in data.nodes:
            continue
        lat, lon = data.nodes[nid]
        for way in node_to_ways.get(nid, []):
            out.setdefault(way.id, []).append(_sign(lat, lon, kind))
    return out
