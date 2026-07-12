"""Gateway endpoint: region snapshots of Dutch matrix signs.

    GET /ndw/region?tile_lat=208&tile_lon=20

This is the only place that knows an NDW address. The device never sees one; it
receives a normalised region snapshot and matches poses locally. Deployed as part
of app.europilot.eu, not shipped to the car.

NDW publishes a fresh snapshot roughly every minute. Serving continues from the
last good snapshot if a refresh fails, with `age_s` telling the device how stale
it is so it can fall back to the car's own sign recognition.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from europilot.ndw.types import Display
from gateway.ndw import feed, region, sign
from gateway.ndw.static_index import load_signs

REFRESH_INTERVAL_S = 60.0
STALE_AFTER_S = 300.0


class FeedStore:
    def __init__(self, url: str = feed.MSI_URL):
        self._url = url
        self._lock = threading.Lock()
        self._states: dict[str, Display] = {}
        self._updated_at: float | None = None
        self._failures = 0

    @property
    def snapshot(self) -> tuple[dict[str, Display], float | None, int]:
        """States, the monotonic reading of the last good refresh, failures since."""
        with self._lock:
            return self._states, self._updated_at, self._failures

    def refresh(self) -> None:
        try:
            states = feed.fetch(self._url)
        except Exception:
            with self._lock:
                self._failures += 1
            return
        with self._lock:
            self._states = states
            self._updated_at = time.monotonic()
            self._failures = 0

    def run_forever(self, interval: float = REFRESH_INTERVAL_S) -> None:
        while True:
            self.refresh()
            time.sleep(interval)


def make_handler(signs, store: FeedStore, signing_key: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet by default
            pass

        def do_GET(self):
            url = urlparse(self.path)

            if url.path == "/health":
                _, updated_at, failures = store.snapshot
                age = None if updated_at is None else time.monotonic() - updated_at
                healthy = age is not None and age < STALE_AFTER_S
                return self._send(
                    200 if healthy else 503,
                    {"ok": healthy, "age_s": age, "consecutive_failures": failures},
                )

            if url.path != "/ndw/region":
                return self._send(404, {"error": "not found"})

            q = parse_qs(url.query)
            try:
                tile_lat = int(q["tile_lat"][0])
                tile_lon = int(q["tile_lon"][0])
            except (KeyError, IndexError, ValueError):
                return self._send(400, {"error": "tile_lat and tile_lon are required"})

            states, updated_at, _ = store.snapshot
            if not states or updated_at is None:
                return self._send(503, {"error": "no feed snapshot yet"})

            age = time.monotonic() - updated_at
            snapshot = region.build(signs, states, tile_lat, tile_lon, age)
            self._send(200, sign.sign_snapshot(snapshot, signing_key))

    return Handler


def serve(shapefile: str, host: str = "127.0.0.1", port: int = 8080,
          signing_key: str | None = None) -> None:
    signing_key = signing_key or sign.signing_key_from_env()
    signs = load_signs(shapefile)
    store = FeedStore()
    store.refresh()
    threading.Thread(target=store.run_forever, daemon=True).start()

    server = ThreadingHTTPServer((host, port), make_handler(signs, store, signing_key))
    print(f"europilot ndw gateway on http://{host}:{port} ({len(signs)} signs)")
    server.serve_forever()


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--shapefile", default="gateway/ndw/test_data/msi_shp.zip")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    a = p.parse_args()
    serve(a.shapefile, a.host, a.port)
