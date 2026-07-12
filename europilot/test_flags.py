"""Tests for the Europilot feature flag client."""

import json
import base64
import threading
from nacl.signing import SigningKey

from europilot.flags import FeatureFlags


def make_signed_response(flags: dict, kill_switch: dict | None = None):
    sk = SigningKey.generate()
    payload = {
        "flags": flags,
        "kill_switch": kill_switch or {"active": False, "reason": ""},
        "timestamp": 1700000000,
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = sk.sign(payload_bytes).signature
    body = json.dumps({
        **payload,
        "signature": base64.b64encode(sig).decode(),
        "public_key": base64.b64encode(sk.verify_key.encode()).decode(),
    }).encode()
    return body, sk.verify_key


def _make_client(verify_key) -> FeatureFlags:
    ff = FeatureFlags.__new__(FeatureFlags)
    ff._flags = {}
    ff._kill_switch_active = False
    ff._kill_switch_reason = ""
    ff._last_fetch = 0.0
    ff._lock = threading.Lock()
    ff._verify_key = verify_key
    return ff


class TestFeatureFlags:
    def test_verify_valid_signature(self):
        raw, vk = make_signed_response({"eu_speed_limits": "true"})
        data = _make_client(vk)._verify_and_parse(raw)
        assert data is not None
        assert data["flags"]["eu_speed_limits"] == "true"

    def test_reject_tampered_response(self):
        raw, vk = make_signed_response({"eu_speed_limits": "true"})
        tampered = raw.replace(b'"true"', b'"false"')
        assert _make_client(vk)._verify_and_parse(tampered) is None

    def test_reject_wrong_key(self):
        raw, _ = make_signed_response({"test": "1"})
        wrong_key = SigningKey.generate().verify_key
        assert _make_client(wrong_key)._verify_and_parse(raw) is None

    def test_kill_switch(self):
        raw, vk = make_signed_response({}, {"active": True, "reason": "Safety recall"})
        data = _make_client(vk)._verify_and_parse(raw)
        assert data["kill_switch"]["active"]
        assert data["kill_switch"]["reason"] == "Safety recall"

    def test_is_enabled_parsing(self):
        ff = FeatureFlags.__new__(FeatureFlags)
        ff._flags = {"a": "true", "b": "1", "c": "yes", "d": "on", "e": "false", "f": "0"}
        ff._kill_switch_active = False
        ff._last_fetch = float("inf")
        ff._lock = threading.Lock()
        for k in ("a", "b", "c", "d"):
            assert ff.is_enabled(k), f"{k} should be enabled"
        for k in ("e", "f"):
            assert not ff.is_enabled(k), f"{k} should be disabled"
        assert not ff.is_enabled("nonexistent")
