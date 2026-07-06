"""Tests for the Europilot feature flag client."""

import json
import base64
import unittest
from unittest.mock import patch, MagicMock
from nacl.signing import SigningKey

from europilot.flags import FeatureFlags, API_URL


def make_signed_response(flags: dict, kill_switch: dict | None = None) -> bytes:
    sk = SigningKey.generate()
    payload = {
        "flags": flags,
        "kill_switch": kill_switch or {"active": False, "reason": ""},
        "timestamp": 1700000000,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = sk.sign(payload_bytes).signature
    return json.dumps({
        **payload,
        "signature": base64.b64encode(sig).decode(),
        "public_key": base64.b64encode(sk.verify_key.encode()).decode(),
    }).encode(), sk.verify_key


class TestFeatureFlags(unittest.TestCase):
    def _make_client(self, verify_key) -> FeatureFlags:
        ff = FeatureFlags.__new__(FeatureFlags)
        ff._flags = {}
        ff._kill_switch_active = False
        ff._kill_switch_reason = ""
        ff._last_fetch = 0.0
        ff._lock = __import__("threading").Lock()
        from nacl.signing import VerifyKey
        ff._verify_key = verify_key
        return ff

    def test_verify_valid_signature(self):
        raw, vk = make_signed_response({"eu_speed_limits": "true"})
        ff = self._make_client(vk)
        data = ff._verify_and_parse(raw)
        self.assertIsNotNone(data)
        self.assertEqual(data["flags"]["eu_speed_limits"], "true")

    def test_reject_tampered_response(self):
        raw, vk = make_signed_response({"eu_speed_limits": "true"})
        tampered = raw.replace(b'"true"', b'"false"')
        ff = self._make_client(vk)
        data = ff._verify_and_parse(tampered)
        self.assertIsNone(data)

    def test_reject_wrong_key(self):
        raw, _ = make_signed_response({"test": "1"})
        wrong_key = SigningKey.generate().verify_key
        ff = self._make_client(wrong_key)
        data = ff._verify_and_parse(raw)
        self.assertIsNone(data)

    def test_kill_switch(self):
        raw, vk = make_signed_response(
            {}, {"active": True, "reason": "Safety recall"}
        )
        ff = self._make_client(vk)
        data = ff._verify_and_parse(raw)
        self.assertTrue(data["kill_switch"]["active"])
        self.assertEqual(data["kill_switch"]["reason"], "Safety recall")

    def test_is_enabled_parsing(self):
        ff = FeatureFlags.__new__(FeatureFlags)
        ff._flags = {"a": "true", "b": "1", "c": "yes", "d": "on", "e": "false", "f": "0"}
        ff._kill_switch_active = False
        ff._last_fetch = float("inf")
        ff._lock = __import__("threading").Lock()
        for k in ("a", "b", "c", "d"):
            self.assertTrue(ff.is_enabled(k), f"{k} should be enabled")
        for k in ("e", "f"):
            self.assertFalse(ff.is_enabled(k), f"{k} should be disabled")
        self.assertFalse(ff.is_enabled("nonexistent"))


if __name__ == "__main__":
    unittest.main()
