"""HTTP service: pose in, matrix sign state out.

    GET /speedlimit?lat=51.9&lon=5.1&heading=161

The static sign index is loaded once. The live feed refreshes on an interval;
NDW publishes a fresh snapshot roughly every minute. Serving continues from the
last good snapshot if a refresh fails, with `age_s` telling the caller how stale
it is so the device can fall back to the car's own sign recognition.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import feed
from .match import Gantry, GantryIndex
from .static_index import load_signs

REFRESH_INTERVAL_S = 60.0
STALE_AFTER_S = 300.0


class FeedStore:
    def __init__(self, url: str = feed.MSI_URL):
        self._url = url
        self._lock = threading.Lock()
        self._states: dict[str, feed.Display] = {}
        self._updated_at: float | None = None
        self._failures = 0

    @property
    def snapshot(self) -> tuple[dict[str, feed.Display], float | None, int]:
        """States, the monotonic clock reading of the last good refresh, failures since."""
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


def _gantry_json(g: Gantry | None) -> dict | None:
    if g is None:
        return None
    return {
        "road": g.road,
        "carriageway": g.carriageway,
        "km": g.km,
        "wvk_id": g.wvk_id,
        "distance_m": g.distance_m,
        "target_speed": g.target_speed,
        "mandatory_speed": g.mandatory_speed,
        "advisory_speed": g.advisory_speed,
        "flashing": g.flashing,
        "closed_lanes": g.closed_lanes,
        "lanes": {
            str(lane): {"aspect": d.aspect, "speed": d.speed, "red_ring": d.red_ring}
            for lane, d in sorted(g.lanes.items())
        },
    }


def make_handler(index: GantryIndex, store: FeedStore):
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

            if url.path != "/speedlimit":
                return self._send(404, {"error": "not found"})

            q = parse_qs(url.query)
            try:
                lat = float(q["lat"][0])
                lon = float(q["lon"][0])
                heading = float(q["heading"][0])
            except (KeyError, IndexError, ValueError):
                return self._send(400, {"error": "lat, lon and heading are required"})

            states, updated_at, _ = store.snapshot
            if not states or updated_at is None:
                return self._send(503, {"error": "no feed snapshot yet"})

            age = time.monotonic() - updated_at
            m = index.match(lat, lon, heading, states)
            self._send(
                200,
                {
                    "age_s": round(age, 1),
                    "stale": age > STALE_AFTER_S,
                    "governing": _gantry_json(m.governing),
                    "upcoming": _gantry_json(m.upcoming),
                },
            )

    return Handler


def serve(shapefile: str, host: str = "127.0.0.1", port: int = 8080) -> None:
    index = GantryIndex(load_signs(shapefile))
    store = FeedStore()
    store.refresh()
    threading.Thread(target=store.run_forever, daemon=True).start()

    server = ThreadingHTTPServer((host, port), make_handler(index, store))
    print(f"europilot-ndw listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--shapefile", default="data/msi_shp.zip")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    a = p.parse_args()
    serve(a.shapefile, a.host, a.port)
