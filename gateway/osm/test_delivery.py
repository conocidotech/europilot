"""End-to-end delivery tests: signed manifests + conditional tiles over HTTP."""

import base64
import hashlib
import http.client
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from gateway.osm.delivery import SIGNING_KEY_ENV, TileStore, make_handler, signing_key_from_env
from gateway.osm.grid import group_of
from gateway.osm.manifest import verify_manifest
from gateway.osm.osm_source import parse_full
from gateway.osm.tiles import build_tiles_full

pytest.importorskip("capnp")   # serialize_tile needs it

RESIDENTIAL = (Path(__file__).parent / "test_data" / "residential.osm").read_bytes()
_SK = SigningKey.generate()
SIGNING_B64 = base64.b64encode(_SK.encode()).decode()
PUB_B64 = base64.b64encode(_SK.verify_key.encode()).decode()

TILES = build_tiles_full(parse_full(RESIDENTIAL))
TILE_KEY = next(k for k, v in TILES.items() if any(r["id"] == 101 for r in v))
GROUP_KEY = group_of(*TILE_KEY)


def _store() -> TileStore:
    store = TileStore(SIGNING_B64)
    store.add_records(TILES)
    return store


class TestStore:
    def test_group_membership_and_manifest(self):
        store = _store()
        assert GROUP_KEY in store.groups()
        m = store.manifest(*GROUP_KEY)
        assert verify_manifest(m, PUB_B64) is True
        assert store.manifest(999, 999) is None   # empty group

    def test_served_hash_matches_bytes(self):
        store = _store()
        body = store.tile(*TILE_KEY)
        assert store.tile_hash(*TILE_KEY) == hashlib.sha256(body).hexdigest()


@pytest.fixture
def conn():
    store = _store()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    c = http.client.HTTPConnection(host, port)
    yield c, store
    c.close()
    srv.shutdown()


def _get(c, path, headers=None):
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    etag = (r.getheader("ETag") or "").strip('"')
    return r.status, etag, body, r


class TestHealth:
    def test_health(self, conn):
        c, _ = conn
        status, _, body, _ = _get(c, "/health")
        assert status == 200
        import json
        assert json.loads(body)["ok"] is True


class TestManifestEndpoint:
    def _path(self):
        return f"/osm/manifest?group_lat={GROUP_KEY[0]}&group_lon={GROUP_KEY[1]}"

    def test_signed_manifest_served(self, conn):
        c, _ = conn
        import json
        status, etag, body, _ = _get(c, self._path())
        assert status == 200 and etag
        assert verify_manifest(json.loads(body), PUB_B64) is True

    def test_conditional_304(self, conn):
        c, _ = conn
        _, etag, _, _ = _get(c, self._path())
        status, _, body, _ = _get(c, self._path(), {"If-None-Match": f'"{etag}"'})
        assert status == 304 and body == b""

    def test_unknown_group_404(self, conn):
        c, _ = conn
        status, _, _, _ = _get(c, "/osm/manifest?group_lat=1&group_lon=1")
        assert status == 404

    def test_missing_params_400(self, conn):
        c, _ = conn
        status, _, _, _ = _get(c, "/osm/manifest")
        assert status == 400


class TestTileEndpoint:
    def _path(self):
        return f"/osm/tile?tile_lat={TILE_KEY[0]}&tile_lon={TILE_KEY[1]}"

    def test_tile_bytes_and_integrity_chain(self, conn):
        c, store = conn
        status, etag, body, r = _get(c, self._path())
        assert status == 200
        assert r.getheader("Content-Type") == "application/octet-stream"
        # the ETag == manifest's contentHash == sha256 of the body: the chain the
        # device verifies after trusting the signed manifest.
        assert etag == hashlib.sha256(body).hexdigest()
        assert etag == store.tile_hash(*TILE_KEY)

    def test_conditional_304(self, conn):
        c, _ = conn
        _, etag, _, _ = _get(c, self._path())
        status, _, body, _ = _get(c, self._path(), {"If-None-Match": f'"{etag}"'})
        assert status == 304 and body == b""

    def test_unknown_tile_404(self, conn):
        c, _ = conn
        status, _, _, _ = _get(c, "/osm/tile?tile_lat=1&tile_lon=1")
        assert status == 404


class TestSigningKeyFromEnv:
    def test_missing_raises(self, monkeypatch):
        monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
        with pytest.raises(RuntimeError):
            signing_key_from_env()

    def test_present_returned(self, monkeypatch):
        monkeypatch.setenv(SIGNING_KEY_ENV, SIGNING_B64)
        assert signing_key_from_env() == SIGNING_B64
