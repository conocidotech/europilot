"""Tests for the signed tile-manifest protocol (auth + versioning core)."""

import base64

from nacl.signing import SigningKey

from gateway.osm.manifest import (
    TileRef, build_manifest, content_hash, manifest_etag, sign_manifest,
    tiles_to_fetch, verify_manifest,
)

_SK = SigningKey.generate()
SIGNING_B64 = base64.b64encode(_SK.encode()).decode()
PUB_B64 = base64.b64encode(_SK.verify_key.encode()).decode()
WRONG_PUB_B64 = base64.b64encode(SigningKey.generate().verify_key.encode()).decode()

REFS = [
    TileRef(208, 21, "bbbb", 120),
    TileRef(208, 20, "aaaa", 88),   # out of order on purpose
]


class TestBuild:
    def test_tiles_are_sorted(self):
        m = build_manifest(208, 16, REFS)
        assert [(t["tileLat"], t["tileLon"]) for t in m["tiles"]] == [(208, 20), (208, 21)]
        assert m["formatVersion"] == 1 and m["groupLat"] == 208 and m["groupLon"] == 16

    def test_content_hash_is_sha256_of_bytes(self):
        import hashlib
        assert content_hash(b"tile-bytes") == hashlib.sha256(b"tile-bytes").hexdigest()


class TestSignVerify:
    def test_round_trip(self):
        signed = sign_manifest(build_manifest(208, 16, REFS), SIGNING_B64)
        assert "signature" in signed
        assert verify_manifest(signed, PUB_B64) is True

    def test_wrong_key_rejected(self):
        signed = sign_manifest(build_manifest(208, 16, REFS), SIGNING_B64)
        assert verify_manifest(signed, WRONG_PUB_B64) is False

    def test_tampered_tile_rejected(self):
        signed = sign_manifest(build_manifest(208, 16, REFS), SIGNING_B64)
        signed["tiles"][0]["contentHash"] = "deadbeef"   # attacker swaps a hash
        assert verify_manifest(signed, PUB_B64) is False

    def test_missing_signature_rejected(self):
        assert verify_manifest(build_manifest(208, 16, REFS), PUB_B64) is False

    def test_signature_is_deterministic(self):
        a = sign_manifest(build_manifest(208, 16, REFS), SIGNING_B64)
        b = sign_manifest(build_manifest(208, 16, REFS), SIGNING_B64)
        assert a["signature"] == b["signature"]


class TestEtag:
    def test_stable_and_data_sensitive(self):
        m1 = sign_manifest(build_manifest(208, 16, REFS), SIGNING_B64)
        m2 = sign_manifest(build_manifest(208, 16, REFS), SIGNING_B64)
        assert manifest_etag(m1) == manifest_etag(m2)

        changed = sign_manifest(
            build_manifest(208, 16, [TileRef(208, 20, "aaaa", 88)]), SIGNING_B64)
        assert manifest_etag(changed) != manifest_etag(m1)


class TestDiff:
    def test_fetches_new_and_changed_skips_unchanged(self):
        m = build_manifest(208, 16, REFS)
        local = {(208, 20): "aaaa", (208, 21): "OLD"}   # 20 current, 21 stale, 22 absent
        got = {(r.tile_lat, r.tile_lon) for r in tiles_to_fetch(local, m)}
        assert got == {(208, 21)}

    def test_empty_cache_fetches_all(self):
        m = build_manifest(208, 16, REFS)
        assert len(tiles_to_fetch({}, m)) == 2
