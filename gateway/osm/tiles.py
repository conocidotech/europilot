"""Derive grid tiles from parsed OSM.

Filters ways to the kept road classes, stamps each with its tag-derived
attributes, and buckets them into 0.25 degree tiles by centroid. Each tile gets
a content hash so the delivery layer can ship only changed tiles and devices can
skip ones they already have.

Reproducible by construction: the same input yields byte-identical tiles and
therefore identical hashes (records sorted by way id, canonical JSON). The
production wire format is Cap'n Proto (mmap-friendly on the device); this slice
uses the same canonical bytes for hashing and leaves the capnp serialization to
the device-format slice.
"""

import hashlib
import json

from gateway.osm import tags as osm_tags
from gateway.osm.cameras import cameras_by_way
from gateway.osm.curves import curves_by_way
from gateway.osm.roundabouts import roundabouts_by_way
from gateway.osm.grid import centroid, tile_of
from gateway.osm.osm_source import OsmData, Way
from gateway.osm.spatial import (
    combine_side, comfort_speed, cyclestreet, cycleway_geometry_sides,
    cycleway_tag_sides, density_per_km, in_residential, residential_polygons,
    residential_score, tagged_points,
)


def _way_record(way: Way, nodes: dict[int, tuple[float, float]]) -> dict | None:
    """A tile record for one way, or None if it isn't a kept road / has no geometry."""
    attrs = osm_tags.derive_way(way.tags)
    if attrs is None:
        return None
    coords = [nodes[n] for n in way.node_ids if n in nodes]
    if not coords:
        return None
    return {"id": way.id, "coords": coords, **attrs}


def build_tiles(nodes: dict[int, tuple[float, float]], ways: list[Way]) -> dict[tuple[int, int], list[dict]]:
    """Bucket kept ways into 0.25 degree tiles, keyed by (tile_lat, tile_lon)."""
    tiles: dict[tuple[int, int], list[dict]] = {}
    for way in ways:
        record = _way_record(way, nodes)
        if record is None:
            continue
        tile = tile_of(*centroid(record["coords"]))
        tiles.setdefault(tile, []).append(record)
    return tiles


def build_tiles_full(data: OsmData) -> dict[tuple[int, int], list[dict]]:
    """build_tiles plus the spatial-join attributes stamped on each way record."""
    polygons = residential_polygons(data)
    cycleways = [data.coords(w.node_ids) for w in data.ways if w.tags.get("highway") == "cycleway"]
    calming = tagged_points(data, lambda t: "traffic_calming" in t)
    crossings = tagged_points(data, lambda t: t.get("highway") == "crossing")
    cameras = cameras_by_way(data)
    roundabouts = roundabouts_by_way(data)
    curves = curves_by_way(data)

    tiles: dict[tuple[int, int], list[dict]] = {}
    for way in data.ways:
        record = _way_record(way, data.nodes)
        if record is None:
            continue
        coords = record["coords"]

        inres = in_residential(coords, polygons)
        calming_pk = density_per_km(coords, calming)
        crossing_pk = density_per_km(coords, crossings)
        tag_l, tag_r = cycleway_tag_sides(way.tags)
        geo_l, geo_r = cycleway_geometry_sides(coords, cycleways)
        score = residential_score(
            road_class=record["roadClass"], maxspeed=record["maxspeed"],
            living_street=way.tags.get("highway") == "living_street",
            calming_per_km=calming_pk, crossing_per_km=crossing_pk, in_residential=inres,
        )
        record.update({
            "inResidential": inres,
            "calmingPerKm": round(calming_pk, 3),
            "crossingPerKm": round(crossing_pk, 3),
            "cyclewayLeft": combine_side(tag_l, geo_l),
            "cyclewayRight": combine_side(tag_r, geo_r),
            "cyclestreet": cyclestreet(way.tags),
            "residentialScore": score,
            "comfortSpeed": comfort_speed(score, record["maxspeed"]),
            "cameras": cameras.get(way.id, []),
            "roundabouts": roundabouts.get(way.id, []),
            "curves": curves.get(way.id, []),
        })
        tiles.setdefault(tile_of(*centroid(coords)), []).append(record)
    return tiles


def _canonical(records: list[dict]) -> bytes:
    """Deterministic bytes for a tile's records (order-independent)."""
    ordered = sorted(records, key=lambda r: r["id"])
    return json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode()


def tile_hash(records: list[dict]) -> str:
    """Content hash of a tile -- stable across runs, changes iff the data does."""
    return hashlib.sha256(_canonical(records)).hexdigest()
