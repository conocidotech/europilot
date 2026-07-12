#!/usr/bin/env python3
"""Deploy glue for the OSM tile gateway: build tiles from a PBF, then serve them.

`gateway/osm/` ships the pieces -- ingest (osmium), derivation (build_tiles_full),
serialization (wire), signed delivery (delivery.TileStore/serve) -- but nothing
composes "a Netherlands PBF" into "a running, signed tile server". That seam is
here, kept out of the merge-safe library so upstream imports stay clean.

Two subcommands, deliberately decoupled so the heavy batch and the long-lived
server are separate units (build on a timer, serve continuously):

    build --pbf FILE --out DIR
        osmium filter/export -> derive tiles -> serialize each to Cap'n Proto
        bytes -> write DIR/tiles/<lat>_<lon>.bin + DIR/meta.json, swapped in
        atomically so a serving process never reads a half-written set.

    serve --dir DIR [--host --port]
        load a built DIR into a delivery.TileStore and serve /osm/*. Signs
        manifests on demand from EUROPILOT_OSM_SIGNING_KEY (never stored on
        disk). Reloads the tile set in place on SIGHUP, so `build` can hand a
        fresh set to a running server with no downtime and no dropped syncs.

`generated_at` (the manifest version stamp) is the PBF's mtime: it advances only
when the extract is actually refreshed, so tile hashes -- and therefore device
sync traffic -- stay stable between rebuilds of identical data.
"""

import argparse
import json
import signal
import subprocess
import sys
import threading
from pathlib import Path

# gateway.* lives at the repo root (deploy/gateway/ -> ../../).
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gateway.osm import delivery, ingest, wire

TILES_SUBDIR = "tiles"
META_NAME = "meta.json"

# A national extract is too big to DOM-parse in one pass (expat's 2 GB limit,
# and the box's RAM), so derivation runs per 0.25 deg cell -- the tile size --
# and the cells are unioned. osmium extracts each cell with a margin so a way's
# spatial-join neighbours are present; each cell keeps only the one tile it owns
# (a way is assigned to its centroid's tile), which dedups boundary-crossing ways
# for free. Default bounds cover the Netherlands.
TILE_DEG = 0.25
CELL_MARGIN_DEG = 0.1
COARSE_DEG = 1.0            # stage-1 region size, cut once from the national file
COARSE_MARGIN_DEG = 0.15   # > CELL_MARGIN, so cells at a region edge keep context
NL_BBOX = (3.2, 50.6, 7.3, 53.7)   # (west, south, east, north)


def _osmconvert(src: Path, w: float, s: float, e: float, n: float, out: Path, cwd: Path) -> None:
    """Cut a bbox (complete ways) with osmconvert -- streaming, low-memory."""
    subprocess.run(["osmconvert", str(src), f"-b={w},{s},{e},{n}",
                    "--complete-ways", f"-o={out}"],
                   check=True, cwd=str(cwd),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _tile_path(tiles_dir: Path, lat: int, lon: int) -> Path:
    return tiles_dir / f"{lat}_{lon}.bin"


def _parse_tile_name(name: str) -> tuple[int, int]:
    lat_s, lon_s = name.removesuffix(".bin").split("_", 1)
    return int(lat_s), int(lon_s)


def _derive_by_cell(source_pbf: Path, work_dir: Path, bbox: tuple[float, float, float, float]) -> dict:
    """Filter the source PBF, then derive 0.25 deg tiles via a two-stage cut.

    A single pass over a national extract blows expat's 2 GB DOM limit, and
    osmium's extractor needs >1 GB and OOMs a small box. Cutting every 0.25 deg
    cell straight from the national file works but re-reads it 200+ times. So:
    stage 1 cuts the country into 1 deg regions once; stage 2 cuts each small
    region's cells (a cheap read) and derives them. All cuts use osmconvert
    (streaming, low-memory) and are margined so a way's spatial-join neighbours
    are present; each cell keeps only the tile it owns, so a boundary-crossing way
    is derived exactly once, by whichever cell holds its centroid.
    """
    # Only the highway classes the derivation actually serves (plus cycleway as
    # spatial-join geometry) -- dropping footway/path/track etc. is output-neutral
    # (_way_record discards them anyway) but shrinks dense-city cells a lot.
    highways = ("motorway,motorway_link,trunk,trunk_link,primary,primary_link,"
                + "secondary,secondary_link,tertiary,tertiary_link,unclassified,"
                + "residential,living_street,service,cycleway")
    tight_filter = (
        f"w/highway={highways}",
        "n/highway=crossing,traffic_signals", "n/highway=speed_camera",
        "n/traffic_calming", "nw/landuse=residential", "r/landuse=residential",
        "r/type=enforcement",
    )
    filtered = work_dir / "filtered.osm.pbf"
    if not (filtered.exists() and filtered.stat().st_mtime >= source_pbf.stat().st_mtime):
        ingest._run(ingest.tags_filter_cmd(source_pbf, filtered, tight_filter))

    coarse_dir = work_dir / "coarse"
    cells_dir = work_dir / "cells"
    coarse_dir.mkdir(parents=True, exist_ok=True)
    cells_dir.mkdir(parents=True, exist_ok=True)

    west, south, east, north = bbox
    step = int(round(COARSE_DEG / TILE_DEG))
    coarse = [(clat, clon)
              for clat in range(int(south // COARSE_DEG), int(north // COARSE_DEG) + 1)
              for clon in range(int(west // COARSE_DEG), int(east // COARSE_DEG) + 1)]

    tiles: dict[tuple[int, int], list[dict]] = {}
    for idx, (clat, clon) in enumerate(coarse):
        region = coarse_dir / f"r_{clat}_{clon}.osm.pbf"
        _osmconvert(filtered,
                    clon * COARSE_DEG - COARSE_MARGIN_DEG, clat * COARSE_DEG - COARSE_MARGIN_DEG,
                    (clon + 1) * COARSE_DEG + COARSE_MARGIN_DEG, (clat + 1) * COARSE_DEG + COARSE_MARGIN_DEG,
                    region, work_dir)
        for tl in range(clat * step, clat * step + step):
            for to in range(clon * step, clon * step + step):
                cell_osm = cells_dir / f"{tl}_{to}.osm"
                _osmconvert(region,
                            to * TILE_DEG - CELL_MARGIN_DEG, tl * TILE_DEG - CELL_MARGIN_DEG,
                            (to + 1) * TILE_DEG + CELL_MARGIN_DEG, (tl + 1) * TILE_DEG + CELL_MARGIN_DEG,
                            cell_osm, work_dir)
                records = ingest.build_tiles_from_osm(cell_osm).get((tl, to))
                cell_osm.unlink(missing_ok=True)
                if records:
                    tiles[(tl, to)] = records
        region.unlink(missing_ok=True)
        print(f"  region {idx + 1}/{len(coarse)} done, {len(tiles)} non-empty tiles")
    return tiles


def build(pbf: Path, out: Path, work_dir: Path,
          bbox: tuple[float, float, float, float] = NL_BBOX) -> int:
    """Filter+derive a PBF into a signed-serving-ready tile directory (atomic)."""
    ingest.require_osmium()
    work_dir.mkdir(parents=True, exist_ok=True)

    generated_at = int(pbf.stat().st_mtime)
    tiles = _derive_by_cell(pbf, work_dir, bbox)

    staging = out.with_name(out.name + ".tmp")
    if staging.exists():
        _rmtree(staging)
    tiles_dir = staging / TILES_SUBDIR
    tiles_dir.mkdir(parents=True)

    for (lat, lon), records in tiles.items():
        payload = wire.serialize_tile(lat, lon, records, generated_at_unix_s=generated_at)
        _tile_path(tiles_dir, lat, lon).write_bytes(payload)

    (staging / META_NAME).write_text(json.dumps({
        "generated_at": generated_at,
        "count": len(tiles),
        "source_pbf": str(pbf),
    }))

    if out.exists():
        _rmtree(out)
    staging.replace(out)
    print(f"built {len(tiles)} tiles into {out} (generated_at={generated_at})")
    return 0


def _rmtree(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    path.rmdir()


def _load_store(directory: Path, signing_key: str) -> delivery.TileStore:
    meta = json.loads((directory / META_NAME).read_text())
    store = delivery.TileStore(signing_key, generated_at_unix_s=int(meta["generated_at"]))
    tiles_dir = directory / TILES_SUBDIR
    for tile_file in tiles_dir.glob("*.bin"):
        lat, lon = _parse_tile_name(tile_file.name)
        store.put_tile(lat, lon, tile_file.read_bytes())
    return store


def serve(directory: Path, host: str, port: int) -> int:
    signing_key = delivery.signing_key_from_env()  # raises if unset -- fail closed
    proxy = _Reloadable(_load_store(directory, signing_key))

    def _reload(*_):
        try:
            proxy.swap(_load_store(directory, signing_key))
        except Exception as e:  # never let a bad rebuild kill a healthy server
            print(f"reload failed, keeping current tiles: {e}", file=sys.stderr)

    try:
        signal.signal(signal.SIGHUP, _reload)
    except ValueError:  # not the main thread (e.g. embedded/tested): no hot-reload
        pass
    server = delivery.ThreadingHTTPServer((host, port), delivery.make_handler(proxy))
    print(f"europilot osm delivery on http://{host}:{port} "
          + f"({len(proxy._bytes)} tiles, {len(proxy.groups())} groups)")
    server.serve_forever()
    return 0


class _Reloadable:
    """A TileStore proxy whose backing store can be swapped atomically on SIGHUP.

    delivery.make_handler closes over one store; wrapping it lets serve() replace
    the tile set under a running server without rebuilding the HTTP handler.
    """

    def __init__(self, store: delivery.TileStore):
        self._lock = threading.Lock()
        self._store = store

    def swap(self, store: delivery.TileStore) -> None:
        with self._lock:
            self._store = store
        print(f"reloaded {len(store._bytes)} tiles")

    def __getattr__(self, name):
        with self._lock:
            return getattr(self._store, name)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="build/serve signed OSM tiles for the gateway")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="derive + serialize tiles from a PBF")
    b.add_argument("--pbf", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True, help="tile directory to (re)build")
    b.add_argument("--work-dir", type=Path, default=Path("/var/lib/europilot/osm/work"))

    s = sub.add_parser("serve", help="serve a built tile directory over HTTP")
    s.add_argument("--dir", type=Path, required=True)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8081)

    a = p.parse_args(argv)
    if a.cmd == "build":
        return build(a.pbf, a.out, a.work_dir)
    return serve(a.dir, a.host, a.port)


if __name__ == "__main__":
    raise SystemExit(main())
