"""Tests for the Europilot data-gateway client.

Covers the untrusted-input surface: Ed25519 verification, freshness/replay
rejection, and the normalizers that coerce gateway JSON into safe, typed
values (including bogus enums and out-of-range numbers). The msgq/capnp
publish path in main() needs a compiled cereal and is exercised on-device.
"""

import json
import base64
import unittest
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


class TestVerification(unittest.TestCase):
    def test_valid_signature(self):
        raw, sk = make_signed({"timestamp": 1700000000, "speed_limit": {"value": 130}})
        data = client_for(sk)._verify_and_parse(raw)
        self.assertIsNotNone(data)
        self.assertEqual(data["speed_limit"]["value"], 130)

    def test_tampered_payload_rejected(self):
        raw, sk = make_signed({"timestamp": 1700000000, "speed_limit": {"value": 130}})
        tampered = raw.replace(b"130", b"999")
        self.assertIsNone(client_for(sk)._verify_and_parse(tampered))

    def test_wrong_key_rejected(self):
        raw, _ = make_signed({"timestamp": 1700000000})
        other = client_for(SigningKey.generate())  # different key
        self.assertIsNone(other._verify_and_parse(raw))

    def test_garbage_rejected(self):
        c = client_for(SigningKey.generate())
        for junk in (b"not json", b"[]", b"123", b'{"no":"sig"}'):
            self.assertIsNone(c._verify_and_parse(junk))

    def test_stale_payload_rejected(self):
        c = client_for(SigningKey.generate())
        fresh = {"timestamp": 1000.0}
        self.assertTrue(c._fresh(fresh, now=1000.0 + MAX_PAYLOAD_AGE - 1))
        self.assertFalse(c._fresh(fresh, now=1000.0 + MAX_PAYLOAD_AGE + 1))
        # missing timestamp is allowed (freshness enforced by poll cadence)
        self.assertTrue(c._fresh({}, now=1e12))


class TestNormalizers(unittest.TestCase):
    def test_matrix_signs_valid(self):
        out = normalize_matrix_signs({"signs": [
            {"lane_index": 1, "kind": "speedLimit", "speed_limit": 100,
             "distance": 250.0, "lat": 52.1, "lon": 4.9},
        ]})
        s = out["signs"][0]
        self.assertEqual(s["kind"], "speedLimit")
        self.assertEqual(s["speedLimit"], 100)
        self.assertEqual(s["laneIndex"], 1)

    def test_matrix_signs_bogus_enum_and_ranges(self):
        out = normalize_matrix_signs({"signs": [
            {"lane_index": 999, "kind": "DROP TABLE", "speed_limit": "x"},
        ]})
        s = out["signs"][0]
        self.assertEqual(s["kind"], "none")        # unknown enum -> safe default
        self.assertEqual(s["laneIndex"], 15)       # clamped
        self.assertEqual(s["speedLimit"], -1)      # unparseable -> unknown

    def test_matrix_signs_empty(self):
        self.assertEqual(normalize_matrix_signs({})["signs"], [])
        self.assertEqual(normalize_matrix_signs(None)["signs"], [])

    def test_map_data_defaults_and_enum(self):
        out = normalize_map_data({"current_road": {"road_class": "nonsense"}})
        self.assertEqual(out["currentRoad"]["roadClass"], "unknown")
        self.assertEqual(out["currentRoad"]["speedLimit"], -1)
        self.assertEqual(out["upcoming"], [])

    def test_map_data_upcoming(self):
        out = normalize_map_data({"upcoming": [
            {"kind": "curve", "distance": 120.0, "curvature": 0.01},
            {"kind": "bogus"},
        ]})
        self.assertEqual(out["upcoming"][0]["kind"], "curve")
        self.assertEqual(out["upcoming"][1]["kind"], "speedChange")  # default

    def test_traffic_lights(self):
        out = normalize_traffic_lights({"intersections": [
            {"id": 42, "distance": 60.0, "movements": [
                {"signal_group": 1, "phase": "green", "time_to_change": 4.5},
                {"signal_group": 300, "phase": "explode"},
            ]},
        ]})
        i = out["intersections"][0]
        self.assertEqual(i["intersectionId"], 42)
        self.assertEqual(i["movements"][0]["phase"], "green")
        self.assertEqual(i["movements"][1]["phase"], "unknown")     # bad enum
        self.assertEqual(i["movements"][1]["signalGroupId"], 255)   # clamped

    def test_speed_limit_confidence_clamped(self):
        self.assertEqual(normalize_speed_limit({"confidence": 5.0})["confidence"], 1.0)
        self.assertEqual(normalize_speed_limit({"confidence": -2.0})["confidence"], 0.0)
        self.assertEqual(normalize_speed_limit({"source": "hack"})["source"], "none")
        self.assertEqual(normalize_speed_limit({"value": 130, "source": "osm"}),
                         {"speedLimit": 130, "source": "osm", "confidence": 0.0})

    def test_nan_inf_rejected(self):
        # advisory floats must never carry NaN/inf
        out = normalize_map_data({"upcoming": [{"kind": "curve", "curvature": float("inf")}]})
        self.assertEqual(out["upcoming"][0]["curvature"], 0.0)


if __name__ == "__main__":
    unittest.main()
