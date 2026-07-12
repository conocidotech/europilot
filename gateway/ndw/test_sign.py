"""Tests for NDW region-snapshot signing (the device's trust boundary)."""

import base64

import pytest
from nacl.signing import SigningKey

from gateway.ndw.sign import (
    SIGNING_KEY_ENV, SNAPSHOT_KIND, sign_snapshot, signing_key_from_env, verify_snapshot,
)

_SK = SigningKey.generate()
SIGNING_B64 = base64.b64encode(_SK.encode()).decode()
PUB_B64 = base64.b64encode(_SK.verify_key.encode()).decode()
WRONG_PUB_B64 = base64.b64encode(SigningKey.generate().verify_key.encode()).decode()

SNAPSHOT = {
    "tile_lat": 208, "tile_lon": 20, "tile_deg": 0.25,
    "bounds": {"min_lat": 52.0, "min_lon": 5.0, "max_lat": 52.25, "max_lon": 5.25},
    "age_s": 12.3,
    "signs": [{"uuid": "a", "road": "A2"}],
    "states": {"a": {"aspect": "speed", "speed": 100}},
}


class TestSignVerify:
    def test_round_trip_and_kind_stamped(self):
        signed = sign_snapshot(SNAPSHOT, SIGNING_B64)
        assert signed["kind"] == SNAPSHOT_KIND
        assert "signature" in signed
        assert verify_snapshot(signed, PUB_B64) is True

    def test_wrong_key_rejected(self):
        assert verify_snapshot(sign_snapshot(SNAPSHOT, SIGNING_B64), WRONG_PUB_B64) is False

    def test_tampered_field_rejected(self):
        signed = sign_snapshot(SNAPSHOT, SIGNING_B64)
        signed["states"]["a"]["speed"] = 50   # attacker lowers a mandatory speed
        assert verify_snapshot(signed, PUB_B64) is False

    def test_missing_signature_rejected(self):
        stamped = {**SNAPSHOT, "kind": SNAPSHOT_KIND}
        assert verify_snapshot(stamped, PUB_B64) is False

    def test_missing_or_wrong_kind_rejected(self):
        # A validly-signed payload of another kind must not pass as an NDW snapshot.
        signed = sign_snapshot(SNAPSHOT, SIGNING_B64)
        signed["kind"] = "somethingElse"
        assert verify_snapshot(signed, PUB_B64) is False
        assert verify_snapshot({**SNAPSHOT}, PUB_B64) is False   # no kind at all

    def test_non_dict_body_rejected_not_crashed(self):
        for body in (5, [], None, "x"):
            assert verify_snapshot(body, PUB_B64) is False


class TestSigningKeyFromEnv:
    def test_missing_raises(self, monkeypatch):
        monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
        monkeypatch.delenv("EUROPILOT_OSM_SIGNING_KEY", raising=False)
        with pytest.raises(RuntimeError):
            signing_key_from_env()

    def test_falls_back_to_the_osm_key(self, monkeypatch):
        monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
        monkeypatch.setenv("EUROPILOT_OSM_SIGNING_KEY", SIGNING_B64)
        assert signing_key_from_env() == SIGNING_B64

    def test_dedicated_key_takes_precedence(self, monkeypatch):
        monkeypatch.setenv(SIGNING_KEY_ENV, SIGNING_B64)
        monkeypatch.setenv("EUROPILOT_OSM_SIGNING_KEY", WRONG_PUB_B64)
        assert signing_key_from_env() == SIGNING_B64

    def test_malformed_key_raises(self, monkeypatch):
        monkeypatch.setenv(SIGNING_KEY_ENV, "not-base64-!!")
        with pytest.raises(RuntimeError):
            signing_key_from_env()
