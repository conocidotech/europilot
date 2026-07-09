"""Static MSI sign locations, read from the NDW shapefile.

The shapefile is already WGS84 (see MSI/shapes.prj), so no reprojection is
needed. Signs are grouped into gantries: all signs sharing road, carriageway
and hectometre are physically one portal spanning the lanes.
"""

import struct
import zipfile
from dataclasses import dataclass


@dataclass(frozen=True)
class Sign:
    uuid: str
    road: str
    carriageway: str
    lane: int
    km: float
    wvk_id: str
    bearing: float
    lat: float
    lon: float

    @property
    def gantry_key(self) -> tuple[str, str, float]:
        return (self.road, self.carriageway, round(self.km, 3))


def _read_dbf(raw: bytes) -> list[dict]:
    n_records, header_len, record_len = struct.unpack("<IHH", raw[4:12])
    fields, pos = [], 32
    while raw[pos : pos + 1] != b"\r":
        d = raw[pos : pos + 32]
        fields.append((d[0:11].split(b"\x00")[0].decode("latin-1"), d[16]))
        pos += 32

    rows = []
    for i in range(n_records):
        rec = raw[header_len + i * record_len : header_len + (i + 1) * record_len]
        if not rec:
            break
        row, off = {}, 1
        for name, flen in fields:
            row[name] = rec[off : off + flen].decode("latin-1").strip()
            off += flen
        rows.append(row)
    return rows


def _read_shp_points(raw: bytes) -> list[tuple[float, float]]:
    points, pos = [], 100
    while pos < len(raw):
        _, content_len = struct.unpack(">II", raw[pos : pos + 8])
        pos += 8
        if struct.unpack("<I", raw[pos : pos + 4])[0] == 1:
            lon, lat = struct.unpack("<dd", raw[pos + 4 : pos + 20])
            points.append((lat, lon))
        pos += content_len * 2
    return points


def load_signs(zip_path: str) -> list[Sign]:
    with zipfile.ZipFile(zip_path) as z:
        rows = _read_dbf(z.read("MSI/shapes.dbf"))
        points = _read_shp_points(z.read("MSI/shapes.shp"))

    if len(rows) != len(points):
        raise ValueError(f"dbf/shp mismatch: {len(rows)} attributes, {len(points)} points")

    signs = []
    for row, (lat, lon) in zip(rows, points, strict=True):
        carriageway = row.get("carriagew0") or row.get("carriageway", "")
        try:
            lane, km, bearing = int(row["lane"]), float(row["km"]), float(row["bearing"])
        except (KeyError, ValueError):
            continue
        signs.append(
            Sign(
                uuid=row["uuid"],
                road=row["road"],
                carriageway=carriageway,
                lane=lane,
                km=km,
                wvk_id=row.get("wegvak", ""),
                bearing=bearing % 360.0,
                lat=lat,
                lon=lon,
            )
        )
    return signs
