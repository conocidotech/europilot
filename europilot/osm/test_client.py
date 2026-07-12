"""Device sync-client tests: verify -> diff -> fetch -> integrity, all offline.

A fake transport routes /osm/manifest and /osm/tile straight to a gateway
TileStore, so the real signing and serving logic is exercised without sockets.
"""

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("capnp")

from nacl.signing import SigningKey

import europilot.osm.client as client_mod
from europilot.osm.client import OsmTileClient
from gateway.osm.delivery import TileStore
from gateway.osm.grid import group_of, tile_of
from gateway.osm.manifest import manifest_etag
from gateway.osm.osm_source import parse_full
from gateway.osm.tiles import build_tiles_full

RESIDENTIAL = (Path(__file__).resolve().parents[2]
               / "gateway" / "osm" / "test_data" / "residential.osm").read_bytes()

_SK = SigningKey.generate()
SIGNING_B64 = base64.b64encode(_SK.encode()).decode()
PUB_B64 = base64.b64encode(_SK.verify_key.encode()).decode()
WRONG_PUB_B64 = base64.b64encode(SigningKey.generate().verify_key.encode()).decode()

# A pose on residential road A (id 101), which runs along lat 52.010.
POSE = (52.010, 5.010)
TILE = tile_of(*POSE)
GROUP = group_of(*TILE)


def _store():
    store = TileStore(SIGNING_B64)
    store.add_records(build_tiles_full(parse_full(RESIDENTIAL)))
    return store


class Transport:
    """Routes device requests to a gateway store; counts calls; can corrupt."""

    def __init__(self, store, *, corrupt_tiles=False):
        self.store = store
        self.corrupt = corrupt_tiles
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append(url)
        u = urlparse(url)
        q = parse_qs(u.query)
        if u.path == "/osm/manifest":
            g = (int(q["group_lat"][0]), int(q["group_lon"][0]))
            m = self.store.manifest(*g)
            if m is None:
                return 404, b"", ""
            etag = manifest_etag(m)
            if (headers.get("If-None-Match", "").strip('"')) == etag:
                return 304, b"", etag
            return 200, json.dumps(m).encode(), etag
        if u.path == "/osm/tile":
            t = (int(q["tile_lat"][0]), int(q["tile_lon"][0]))
            body = self.store.tile(*t)
            if body is None:
                return 404, b"", ""
            if self.corrupt:
                body = body + b"\x00"   # break the sha256 integrity check
            return 200, body, self.store.tile_hash(*t)
        return 404, b"", ""


class TestSyncAndMatch:
    def test_happy_path(self):
        client = OsmTileClient(host="http://gw", pubkey_b64=PUB_B64, transport=Transport(_store()))
        assert client.sync(*POSE) is True
        adv = client.match(*POSE, heading=90.0)
        assert adv.valid and adv.road_id == 101
        assert adv.speed_limit == 30 and adv.in_residential is True

    def test_match_before_sync_is_none(self):
        client = OsmTileClient(host="http://gw", pubkey_b64=PUB_B64, transport=Transport(_store()))
        assert client.match(*POSE, heading=90.0).valid is False

    def test_bad_signature_rejected(self):
        client = OsmTileClient(host="http://gw", pubkey_b64=WRONG_PUB_B64, transport=Transport(_store()))
        assert client.sync(*POSE) is False
        assert client.match(*POSE, heading=90.0).valid is False

    def test_tampered_manifest_rejected(self):
        store = _store()
        tr = Transport(store)

        def tamper(url, headers):
            status, body, etag = tr(url, headers)
            if urlparse(url).path == "/osm/manifest" and status == 200:
                m = json.loads(body)
                m["tiles"][0]["contentHash"] = "deadbeef"   # attacker swaps a hash
                return 200, json.dumps(m).encode(), etag
            return status, body, etag

        client = OsmTileClient(host="http://gw", pubkey_b64=PUB_B64, transport=tamper)
        assert client.sync(*POSE) is False

    def test_corrupt_tile_dropped(self):
        client = OsmTileClient(host="http://gw", pubkey_b64=PUB_B64,
                               transport=Transport(_store(), corrupt_tiles=True))
        client.sync(*POSE)   # manifest verifies, but tile bytes fail sha256
        assert client.match(*POSE, heading=90.0).valid is False

    def test_conditional_304_skips_refetch(self):
        tr = Transport(_store())
        client = OsmTileClient(host="http://gw", pubkey_b64=PUB_B64, transport=tr)
        assert client.sync(*POSE) is True
        first = len(tr.calls)
        assert client.sync(*POSE) is True     # second sync: manifest 304
        after = tr.calls[first:]
        assert len(after) == 1 and "/osm/manifest" in after[0]   # only the manifest, no tiles

    def test_gateway_unreachable_fails_closed(self):
        client = OsmTileClient(host="http://gw", pubkey_b64=PUB_B64,
                               transport=lambda url, headers: (0, b"", ""))
        assert client.sync(*POSE) is False
        assert client.match(*POSE, heading=90.0).valid is False


class TestPollGroupAware:
    def test_freshness_is_per_group(self):
        client = OsmTileClient(host="http://gw", pubkey_b64=PUB_B64, transport=Transport(_store()))
        assert client.sync(*POSE) is True
        assert client._group_fresh(GROUP) is True
        # a different 2-degree group is not fresh just because we synced this one
        assert client._group_fresh((GROUP[0] + 2, GROUP[1] + 2)) is False

    def test_poll_refreshes_after_crossing_a_group_boundary(self, monkeypatch):
        # Run the background refresh synchronously so its effect is observable.
        class _SyncThread:
            def __init__(self, target, daemon=None):
                self._target = target

            def start(self):
                self._target()

        monkeypatch.setattr(client_mod.threading, "Thread", _SyncThread)

        tr = Transport(_store())
        client = OsmTileClient(host="http://gw", pubkey_b64=PUB_B64, transport=tr)

        client.poll(*POSE)                              # first poll syncs GROUP
        assert client.match(*POSE, heading=90.0).valid is True
        n = len(tr.calls)

        client.poll(*POSE)                              # same group, still fresh
        assert len(tr.calls) == n                       # no new fetch

        far = (POSE[0] + 3.0, POSE[1] + 3.0)
        assert group_of(*tile_of(*far)) != GROUP
        client.poll(*far)                               # crossed a group boundary
        assert len(tr.calls) > n                        # a sync was attempted, not skipped
