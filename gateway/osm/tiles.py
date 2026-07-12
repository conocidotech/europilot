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
from gateway.osm.grid import centroid, tile_of
from gateway.osm.osm_source import Way


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


def _canonical(records: list[dict]) -> bytes:
    """Deterministic bytes for a tile's records (order-independent)."""
    ordered = sorted(records, key=lambda r: r["id"])
    return json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode()


def tile_hash(records: list[dict]) -> str:
    """Content hash of a tile -- stable across runs, changes iff the data does."""
    return hashlib.sha256(_canonical(records)).hexdigest()
