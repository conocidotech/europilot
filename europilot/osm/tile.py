"""Decode a Cap'n Proto map tile into device-side Road objects.

The device reads the same maptile.capnp schema the gateway serialized with, so
the wire contract is single-sourced. pycapnp is already on the device (cereal
uses it). Unknown sentinels (maxspeed/lanes/comfortSpeed == 0) become None here,
and fixed-point 1e-7 coords become lat/lon degrees, so the matcher works in
ordinary units.
"""

from pathlib import Path

from europilot.osm.types import Camera, Curve, Road, Roundabout

COORD_SCALE = 10_000_000.0
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "gateway" / "osm" / "maptile.capnp"
_SCHEMA = None


def _schema():
    global _SCHEMA
    if _SCHEMA is None:
        import capnp
        capnp.remove_import_hook()
        _SCHEMA = capnp.load(str(_SCHEMA_PATH))
    return _SCHEMA


def _none_if_zero(value: int) -> int | None:
    return value or None


def _camera(c) -> Camera:
    return Camera(
        lat=c.point.lat / COORD_SCALE,
        lon=c.point.lon / COORD_SCALE,
        maxspeed=_none_if_zero(c.maxspeed),
        kind=str(c.kind),
    )


def _roundabout(rb) -> Roundabout:
    return Roundabout(
        lat=rb.point.lat / COORD_SCALE,
        lon=rb.point.lon / COORD_SCALE,
        kind=str(rb.kind),
        radius_m=rb.radiusM,
    )


def _curve(cv) -> Curve:
    return Curve(
        lat=cv.point.lat / COORD_SCALE,
        lon=cv.point.lon / COORD_SCALE,
        radius_m=cv.radiusM,
    )


def _road(r) -> Road:
    return Road(
        id=r.id,
        road_class=str(r.roadClass),
        maxspeed=_none_if_zero(r.maxspeed),
        oneway=r.oneway,
        lanes=_none_if_zero(r.lanes),
        name=r.name,
        points=[(p.lat / COORD_SCALE, p.lon / COORD_SCALE) for p in r.points],
        in_residential=r.inResidential,
        calming_per_km=r.calmingPerKm,
        crossing_per_km=r.crossingPerKm,
        cycleway_left=str(r.cyclewayLeft),
        cycleway_right=str(r.cyclewayRight),
        cyclestreet=r.cyclestreet,
        residential_score=r.residentialScore,
        comfort_speed=_none_if_zero(r.comfortSpeed),
        cameras=tuple(_camera(c) for c in r.cameras),
        roundabouts=tuple(_roundabout(rb) for rb in r.roundabouts),
        curves=tuple(_curve(cv) for cv in r.curves),
    )


def decode_tile(tile_bytes: bytes) -> list[Road]:
    """Roads in a serialized tile. The device mmaps these bytes in production."""
    schema = _schema()
    with schema.MapTile.from_bytes(tile_bytes) as tile:
        return [_road(r) for r in tile.roads]
