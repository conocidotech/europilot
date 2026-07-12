"""End-to-end service tests: the region endpoint serves a signed snapshot."""

import base64
import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest
from nacl.signing import SigningKey

from europilot.ndw.types import Display, Sign
from gateway.ndw.service import make_handler
from gateway.ndw.sign import verify_snapshot

_SK = SigningKey.generate()
SIGNING_B64 = base64.b64encode(_SK.encode()).decode()
PUB_B64 = base64.b64encode(_SK.verify_key.encode()).decode()

SIGN = Sign(uuid="a", road="A2", carriageway="R", lane=1, km=10.0,
            wvk_id="", bearing=90.0, lat=52.1, lon=5.1)
STATE = Display(uuid="a", aspect="speed", speed=100, flashing=False,
                red_ring=True, ts_state="2026-01-01T00:00:00Z")
TILE = (208, 20)   # floor(52.1/0.25), floor(5.1/0.25)


class _Store:
    def __init__(self, states, updated=True):
        self._states = states
        self._updated = updated

    @property
    def snapshot(self):
        return self._states, (time.monotonic() if self._updated else None), 0


def _serve(store):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler([SIGN], store, SIGNING_B64))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _get(srv, path):
    host, port = srv.server_address
    c = http.client.HTTPConnection(host, port)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


class TestRegionEndpoint:
    def test_served_snapshot_is_signed_and_verifies(self):
        srv = _serve(_Store({"a": STATE}))
        try:
            status, body = _get(srv, f"/ndw/region?tile_lat={TILE[0]}&tile_lon={TILE[1]}")
            assert status == 200
            payload = json.loads(body)
            assert verify_snapshot(payload, PUB_B64) is True
            assert payload["signs"] and payload["signs"][0]["uuid"] == "a"
        finally:
            srv.shutdown()

    def test_no_snapshot_yet_503(self):
        srv = _serve(_Store({}, updated=False))
        try:
            status, _ = _get(srv, f"/ndw/region?tile_lat={TILE[0]}&tile_lon={TILE[1]}")
            assert status == 503
        finally:
            srv.shutdown()

    def test_missing_params_400(self):
        srv = _serve(_Store({"a": STATE}))
        try:
            status, _ = _get(srv, "/ndw/region")
            assert status == 400
        finally:
            srv.shutdown()

    def test_unknown_path_404(self):
        srv = _serve(_Store({"a": STATE}))
        try:
            status, _ = _get(srv, "/nope")
            assert status == 404
        finally:
            srv.shutdown()


@pytest.mark.parametrize("path", ["/ndw/region?tile_lat=208&tile_lon=20"])
def test_snapshot_round_trips_through_the_device_verify(path):
    # The exact contract the car relies on: what the gateway signs is what the
    # device's pinned key accepts, byte for byte after JSON round-trip.
    srv = _serve(_Store({"a": STATE}))
    try:
        _, body = _get(srv, path)
        assert verify_snapshot(json.loads(body), PUB_B64) is True
    finally:
        srv.shutdown()
