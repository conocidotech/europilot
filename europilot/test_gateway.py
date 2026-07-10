"""Tests for the Europilot data-gateway client.

Covers the untrusted-input surface: Ed25519 verification, freshness/replay
rejection, and the normalizers that coerce gateway JSON into safe, typed
values (including bogus enums and out-of-range numbers). The msgq/capnp
publish path in main() needs a compiled cereal and is exercised on-device.
"""

import json
import base64
from nacl.signing import SigningKey

from europilot.gateway import (
    GatewayClient,
    normalize_matrix_signs,
    normalize_map_data,
    normalize_traffic_lights,
    normalize_speed_limit,
    MAX_PAYLOAD_AGE,
)


def make_signed(payload: dict, sk: SigningKey | None = None) -> tuple[bytes, SigningKey]:
    sk = sk or SigningKey.generate()
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = sk.sign(payload_bytes).signature
    body = json.dumps({
        **payload,
        "signature": base64.b64encode(sig).decode(),
        "public_key": base64.b64encode(sk.verify_key.encode()).decode(),
    }).encode()
    return body, sk


def client_for(sk: SigningKey) -> GatewayClient:
    return GatewayClient(public_key_b64=base64.b64encode(sk.verify_key.encode()).decode())


class TestVerification:
    def test_valid_signature(self):
        raw, sk = make_signed({"timestamp": 1700000000, "speed_limit": {"value": 130}})
        data = client_for(sk)._verify_and_parse(raw)
        assert data is not None
        assert data["speed_limit"]["value"] == 130

    def test_tampered_payload_rejected(self):
        raw, sk = make_signed({"timestamp": 1700000000, "speed_limit": {"value": 130}})
        tampered = raw.replace(b"130", b"999")
        assert client_for(sk)._verify_and_parse(tampered) is None

    def test_wrong_key_rejected(self):
        raw, _ = make_signed({"timestamp": 1700000000})
        other = client_for(SigningKey.generate())  # different key
        assert other._verify_and_parse(raw) is None

    def test_garbage_rejected(self):
        c = client_for(SigningKey.generate())
        for junk in (b"not json", b"[]", b"123", b'{"no":"sig"}'):
            assert c._verify_and_parse(junk) is None

    def test_stale_payload_rejected(self):
        c = client_for(SigningKey.generate())
        fresh = {"timestamp": 1000.0}
        assert c._fresh(fresh, now=1000.0 + MAX_PAYLOAD_AGE - 1)
        assert not c._fresh(fresh, now=1000.0 + MAX_PAYLOAD_AGE + 1)
        # missing timestamp is allowed (freshness enforced by poll cadence)
        assert c._fresh({}, now=1e12)


class TestNormalizers:
    def test_matrix_signs_valid(self):
        out = normalize_matrix_signs({"signs": [
            {"lane_index": 1, "kind": "speedLimit", "speed_limit": 100,
             "distance": 250.0, "lat": 52.1, "lon": 4.9},
        ]})
        s = out["signs"][0]
        assert s["kind"] == "speedLimit"
        assert s["speedLimit"] == 100
        assert s["laneIndex"] == 1

    def test_matrix_signs_bogus_enum_and_ranges(self):
        out = normalize_matrix_signs({"signs": [
            {"lane_index": 999, "kind": "DROP TABLE", "speed_limit": "x"},
        ]})
        s = out["signs"][0]
        assert s["kind"] == "none"        # unknown enum -> safe default
        assert s["laneIndex"] == 15       # clamped
        assert s["speedLimit"] == -1      # unparseable -> unknown

    def test_matrix_signs_empty(self):
        assert normalize_matrix_signs({})["signs"] == []
        assert normalize_matrix_signs(None)["signs"] == []

    def test_map_data_defaults_and_enum(self):
        out = normalize_map_data({"current_road": {"road_class": "nonsense"}})
        assert out["currentRoad"]["roadClass"] == "unknown"
        assert out["currentRoad"]["speedLimit"] == -1
        assert out["upcoming"] == []

    def test_map_data_upcoming(self):
        out = normalize_map_data({"upcoming": [
            {"kind": "curve", "distance": 120.0, "curvature": 0.01},
            {"kind": "bogus"},
        ]})
        assert out["upcoming"][0]["kind"] == "curve"
        assert out["upcoming"][1]["kind"] == "speedChange"  # default

    def test_traffic_lights(self):
        out = normalize_traffic_lights({"intersections": [
            {"id": 42, "distance": 60.0, "movements": [
                {"signal_group": 1, "phase": "green", "time_to_change": 4.5},
                {"signal_group": 300, "phase": "explode"},
            ]},
        ]})
        i = out["intersections"][0]
        assert i["intersectionId"] == 42
        assert i["movements"][0]["phase"] == "green"
        assert i["movements"][1]["phase"] == "unknown"     # bad enum
        assert i["movements"][1]["signalGroupId"] == 255   # clamped

    def test_speed_limit_confidence_clamped(self):
        assert normalize_speed_limit({"confidence": 5.0})["confidence"] == 1.0
        assert normalize_speed_limit({"confidence": -2.0})["confidence"] == 0.0
        assert normalize_speed_limit({"source": "hack"})["source"] == "none"
        assert normalize_speed_limit({"value": 130, "source": "osm"}) == \
            {"speedLimit": 130, "source": "osm", "confidence": 0.0}

    def test_nan_inf_rejected(self):
        # advisory floats must never carry NaN/inf
        out = normalize_map_data({"upcoming": [{"kind": "curve", "curvature": float("inf")}]})
        assert out["upcoming"][0]["curvature"] == 0.0
