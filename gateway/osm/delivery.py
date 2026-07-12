"""Serve versioned, signed OSM tiles to the device over HTTP.

Part of app.europilot.eu, not shipped to the car. Three endpoints:

    GET /osm/manifest?group_lat=&group_lon=   -> signed manifest for a 2 deg group
    GET /osm/tile?tile_lat=&tile_lon=          -> Cap'n Proto tile bytes
    GET /health

Conditional delivery both levels: the manifest and every tile carry an ETag
(the manifest's own hash, and the tile's content hash), so an `If-None-Match`
that still matches gets a 304 and no body. The device syncs a region by fetching
the group manifest, verifying its signature once, then pulling only the tiles
whose hash changed -- see manifest.py for the trust argument.

The signing key is never hardcoded: serve() reads it from the environment. Tiles
are serialized once with a fixed timestamp, so their bytes -- and therefore their
hashes -- are a pure function of the data and stay stable until the data changes.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from gateway.osm import manifest as mf
from gateway.osm import wire
from gateway.osm.grid import group_of

SIGNING_KEY_ENV = "EUROPILOT_OSM_SIGNING_KEY"   # base64 Ed25519 seed


class TileStore:
    """Serialized tiles, grouped, with signed manifests built on demand."""

    def __init__(self, signing_key_b64: str, *, generated_at_unix_s: int = 0):
        self._signing_key = signing_key_b64
        self._generated_at = generated_at_unix_s
        self._bytes: dict[tuple[int, int], bytes] = {}
        self._hash: dict[tuple[int, int], str] = {}

    def put_tile(self, tile_lat: int, tile_lon: int, tile_bytes: bytes) -> None:
        self._bytes[(tile_lat, tile_lon)] = tile_bytes
        self._hash[(tile_lat, tile_lon)] = mf.content_hash(tile_bytes)

    def add_records(self, tiles: dict[tuple[int, int], list[dict]]) -> None:
        """Serialize and store a batch of derived tiles (build_tiles_full output)."""
        for (tile_lat, tile_lon), records in tiles.items():
            self.put_tile(tile_lat, tile_lon,
                          wire.serialize_tile(tile_lat, tile_lon, records,
                                              generated_at_unix_s=self._generated_at))

    def tile(self, tile_lat: int, tile_lon: int) -> bytes | None:
        return self._bytes.get((tile_lat, tile_lon))

    def tile_hash(self, tile_lat: int, tile_lon: int) -> str | None:
        return self._hash.get((tile_lat, tile_lon))

    def groups(self) -> set[tuple[int, int]]:
        return {group_of(lat, lon) for lat, lon in self._bytes}

    def manifest(self, group_lat: int, group_lon: int) -> dict | None:
        """Signed manifest for a group, or None if the group holds no tiles."""
        refs = [
            mf.TileRef(lat, lon, self._hash[(lat, lon)], len(self._bytes[(lat, lon)]))
            for (lat, lon) in self._bytes
            if group_of(lat, lon) == (group_lat, group_lon)
        ]
        if not refs:
            return None
        unsigned = mf.build_manifest(group_lat, group_lon, refs,
                                     generated_at_unix_s=self._generated_at)
        return mf.sign_manifest(unsigned, self._signing_key)


def make_handler(store: TileStore):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send_json(self, code: int, payload: dict, etag: str | None = None) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            if etag is not None:
                self.send_header("ETag", f'"{etag}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_not_modified(self, etag: str) -> None:
            self.send_response(304)
            self.send_header("ETag", f'"{etag}"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _if_none_match(self) -> str | None:
            raw = self.headers.get("If-None-Match")
            return raw.strip().strip('"') if raw else None

        def log_message(self, *args):   # quiet by default
            pass

        def _int_params(self, q, *names):
            return tuple(int(q[n][0]) for n in names)

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)

            if url.path == "/health":
                return self._send_json(200, {"ok": True, "tiles": len(store._bytes),
                                             "groups": len(store.groups())})

            if url.path == "/osm/manifest":
                try:
                    group_lat, group_lon = self._int_params(q, "group_lat", "group_lon")
                except (KeyError, IndexError, ValueError):
                    return self._send_json(400, {"error": "group_lat and group_lon are required"})
                manifest = store.manifest(group_lat, group_lon)
                if manifest is None:
                    return self._send_json(404, {"error": "no such group"})
                etag = mf.manifest_etag(manifest)
                if self._if_none_match() == etag:
                    return self._send_not_modified(etag)
                return self._send_json(200, manifest, etag=etag)

            if url.path == "/osm/tile":
                try:
                    tile_lat, tile_lon = self._int_params(q, "tile_lat", "tile_lon")
                except (KeyError, IndexError, ValueError):
                    return self._send_json(400, {"error": "tile_lat and tile_lon are required"})
                body = store.tile(tile_lat, tile_lon)
                if body is None:
                    return self._send_json(404, {"error": "no such tile"})
                etag = store.tile_hash(tile_lat, tile_lon)
                if self._if_none_match() == etag:
                    return self._send_not_modified(etag)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("ETag", f'"{etag}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None

            return self._send_json(404, {"error": "not found"})

    return Handler


def serve(store: TileStore, host: str = "127.0.0.1", port: int = 8081) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(store))
    print(f"europilot osm delivery on http://{host}:{port} "
          + f"({len(store._bytes)} tiles, {len(store.groups())} groups)")
    server.serve_forever()


def signing_key_from_env() -> str:
    key = os.environ.get(SIGNING_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"{SIGNING_KEY_ENV} is not set -- generate an Ed25519 key and pin its "
            + "public half in the device client (do not commit the private key)"
        )
    return key
