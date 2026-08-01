"""Serialize derived tiles to the Cap'n Proto format the device mmaps.

Two halves, deliberately split by dependency:

  - normalize_tile / tile_content_hash -- PURE. Turn the derivation's dict
    records into the canonical, wire-ready form (roads sorted by id, coords
    scaled to fixed-point int, three-valued attrs mapped to enum names, unknown
    numerics collapsed to a 0 sentinel) and hash it. No capnp, so hashing and
    the delivery/cache key work anywhere, even where the library is absent.
  - to_capnp_bytes / from_capnp_bytes -- need pycapnp (a root dependency, and
    the same implementation the device's reader uses, so the bytes are correct
    by construction). Only the encode/decode step touches it; it's imported
    lazily so this module loads without it.

The normalized form is the single source of truth: it's what we hash AND what
feeds the capnp builder, so the stored contentHash always matches the payload.
"""

import hashlib
import json
from pathlib import Path

COORD_SCALE = 10_000_000            # 1e7: degrees -> 100-nanodegree fixed point
FORMAT_VERSION = 1
ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"

_ROAD_CLASS_ENUM = {
    "motorway": "motorway", "trunk": "trunk", "primary": "primary",
    "secondary": "secondary", "tertiary": "tertiary", "unclassified": "unclassified",
    "residential": "residential", "living_street": "livingStreet", "service": "service",
}
_PRESENCE_ENUM = {None: "unknown", False: "absent", True: "present"}
_CAMERA_KIND_ENUM = {"fixed": "fixed", "section": "section"}
_ROUNDABOUT_KIND_ENUM = {"roundabout": "roundabout", "mini": "mini"}

_SCHEMA = None


def _scale(deg: float) -> int:
    return int(round(deg * COORD_SCALE))


def _u8(value) -> int:
    """Clamp an optional small count/speed to the UInt8 range; None/0 -> 0."""
    return min(255, max(0, int(value or 0)))


def normalize_camera(cam: dict) -> dict:
    """Canonical, wire-ready form of one camera (point scaled, kind -> enum)."""
    return {
        "point": [_scale(cam["lat"]), _scale(cam["lon"])],
        "maxspeed": _u8(cam.get("maxspeed")),
        "kind": _CAMERA_KIND_ENUM.get(cam.get("kind"), "fixed"),
    }


def normalize_roundabout(rb: dict) -> dict:
    """Canonical, wire-ready form of one roundabout (point scaled, kind -> enum)."""
    return {
        "point": [_scale(rb["lat"]), _scale(rb["lon"])],
        "kind": _ROUNDABOUT_KIND_ENUM.get(rb.get("kind"), "roundabout"),
        "radiusM": _u8(rb.get("radius_m")),
    }


def normalize_road(rec: dict) -> dict:
    """Canonical, wire-ready form of one derived way record.

    Accepts both slice-1 records and the full spatial records; missing spatial
    attributes fall back to their unknown/absent defaults.
    """
    road = {
        "id": int(rec["id"]),
        "roadClass": _ROAD_CLASS_ENUM.get(rec.get("roadClass"), "unknown"),
        "maxspeed": _u8(rec.get("maxspeed")),
        "oneway": bool(rec.get("oneway", False)),
        "lanes": _u8(rec.get("lanes")),
        "name": rec.get("name") or "",
        "points": [[_scale(lat), _scale(lon)] for lat, lon in rec["coords"]],
        "inResidential": bool(rec.get("inResidential", False)),
        "calmingPerKm": round(float(rec.get("calmingPerKm", 0.0)), 3),
        "crossingPerKm": round(float(rec.get("crossingPerKm", 0.0)), 3),
        "cyclewayLeft": _PRESENCE_ENUM[rec.get("cyclewayLeft")],
        "cyclewayRight": _PRESENCE_ENUM[rec.get("cyclewayRight")],
        "cyclestreet": bool(rec.get("cyclestreet", False)),
        "residentialScore": round(float(rec.get("residentialScore", 0.0)), 3),
        "comfortSpeed": _u8(rec.get("comfortSpeed")),
    }
    # Only carried when present, so a camera-less tile hashes exactly as before
    # (no needless re-sync of every tile when this field was introduced).
    cameras = [normalize_camera(c) for c in rec.get("cameras", [])]
    if cameras:
        road["cameras"] = sorted(cameras, key=lambda c: (c["point"][0], c["point"][1], c["kind"]))
    # Same "only when present" rule so a roundabout-less tile hashes as before.
    roundabouts = [normalize_roundabout(r) for r in rec.get("roundabouts", [])]
    if roundabouts:
        road["roundabouts"] = sorted(roundabouts, key=lambda r: (r["point"][0], r["point"][1], r["kind"]))
    return road


def normalize_tile(tile_lat: int, tile_lon: int, records: list[dict],
                   *, attribution: str = ATTRIBUTION) -> dict:
    """Canonical tile: roads sorted by id, ready to hash and to encode.

    Excludes contentHash (can't contain its own hash) and generatedAt (a
    serialization timestamp, not data -- keeping it out makes the hash identify
    the data, so two tiles built at different times but identical still match).
    """
    roads = sorted((normalize_road(r) for r in records), key=lambda r: r["id"])
    return {
        "formatVersion": FORMAT_VERSION,
        "tileLat": tile_lat,
        "tileLon": tile_lon,
        "attribution": attribution,
        "roads": roads,
    }


def tile_content_hash(norm: dict) -> str:
    """sha256 hex over the canonical form -- stable across runs and machines."""
    canonical = json.dumps(norm, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _schema():
    global _SCHEMA
    if _SCHEMA is None:
        import capnp
        capnp.remove_import_hook()
        _SCHEMA = capnp.load(str(Path(__file__).parent / "maptile.capnp"))
    return _SCHEMA


def to_capnp_bytes(norm: dict, *, generated_at_unix_s: int = 0) -> bytes:
    """Encode a normalized tile to Cap'n Proto bytes (needs pycapnp).

    Deterministic for a given (norm, generated_at_unix_s): the delivery layer
    can content-address the payload. contentHash is stamped from norm, so it
    does not depend on the timestamp.
    """
    schema = _schema()
    tile = schema.MapTile.new_message()
    tile.formatVersion = norm["formatVersion"]
    tile.tileLat = norm["tileLat"]
    tile.tileLon = norm["tileLon"]
    tile.attribution = norm["attribution"]
    tile.contentHash = tile_content_hash(norm)
    tile.generatedAtUnixS = generated_at_unix_s

    roads = tile.init("roads", len(norm["roads"]))
    for i, rn in enumerate(norm["roads"]):
        r = roads[i]
        r.id = rn["id"]
        r.roadClass = rn["roadClass"]
        r.maxspeed = rn["maxspeed"]
        r.oneway = rn["oneway"]
        r.lanes = rn["lanes"]
        r.name = rn["name"]
        r.inResidential = rn["inResidential"]
        r.calmingPerKm = rn["calmingPerKm"]
        r.crossingPerKm = rn["crossingPerKm"]
        r.cyclewayLeft = rn["cyclewayLeft"]
        r.cyclewayRight = rn["cyclewayRight"]
        r.cyclestreet = rn["cyclestreet"]
        r.residentialScore = rn["residentialScore"]
        r.comfortSpeed = rn["comfortSpeed"]
        pts = r.init("points", len(rn["points"]))
        for j, (lat, lon) in enumerate(rn["points"]):
            pts[j].lat = lat
            pts[j].lon = lon
        cams = rn.get("cameras", [])
        cam_list = r.init("cameras", len(cams))
        for k, cn in enumerate(cams):
            cam_list[k].point.lat = cn["point"][0]
            cam_list[k].point.lon = cn["point"][1]
            cam_list[k].maxspeed = cn["maxspeed"]
            cam_list[k].kind = cn["kind"]
        rbs = rn.get("roundabouts", [])
        rb_list = r.init("roundabouts", len(rbs))
        for k, rbn in enumerate(rbs):
            rb_list[k].point.lat = rbn["point"][0]
            rb_list[k].point.lon = rbn["point"][1]
            rb_list[k].kind = rbn["kind"]
            rb_list[k].radiusM = rbn["radiusM"]
    return tile.to_bytes()


def serialize_tile(tile_lat: int, tile_lon: int, records: list[dict], *,
                   attribution: str = ATTRIBUTION, generated_at_unix_s: int = 0) -> bytes:
    """normalize + encode in one call -- the gateway's entry point per tile."""
    norm = normalize_tile(tile_lat, tile_lon, records, attribution=attribution)
    return to_capnp_bytes(norm, generated_at_unix_s=generated_at_unix_s)


def from_capnp_bytes(data: bytes) -> dict:
    """Decode tile bytes back to plain Python (round-trip / device read path).

    Illustrates what the device reads; the real device matcher (EUROPILOT-15)
    reads the mapped struct directly instead of copying into dicts.
    """
    schema = _schema()
    with schema.MapTile.from_bytes(data) as tile:
        return {
            "formatVersion": tile.formatVersion,
            "tileLat": tile.tileLat,
            "tileLon": tile.tileLon,
            "contentHash": tile.contentHash,
            "generatedAtUnixS": tile.generatedAtUnixS,
            "attribution": tile.attribution,
            "roads": [{
                "id": r.id,
                "roadClass": str(r.roadClass),
                "maxspeed": r.maxspeed,
                "oneway": r.oneway,
                "lanes": r.lanes,
                "name": r.name,
                "inResidential": r.inResidential,
                "calmingPerKm": r.calmingPerKm,
                "crossingPerKm": r.crossingPerKm,
                "cyclewayLeft": str(r.cyclewayLeft),
                "cyclewayRight": str(r.cyclewayRight),
                "cyclestreet": r.cyclestreet,
                "residentialScore": r.residentialScore,
                "comfortSpeed": r.comfortSpeed,
                "points": [[p.lat, p.lon] for p in r.points],
                "cameras": [{"point": [c.point.lat, c.point.lon],
                             "maxspeed": c.maxspeed, "kind": str(c.kind)} for c in r.cameras],
                "roundabouts": [{"point": [rb.point.lat, rb.point.lon],
                                 "kind": str(rb.kind), "radiusM": rb.radiusM} for rb in r.roundabouts],
            } for r in tile.roads],
        }
