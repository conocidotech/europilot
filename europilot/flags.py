"""Europilot feature flag client with Ed25519 signature verification.

Fetches flags from app.europilot.eu, verifies the response signature,
and caches locally for offline use. Merge-safe: this file is new and
does not modify any upstream openpilot code.
"""

import json
import time
import threading
from pathlib import Path

from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

API_URL = "https://app.europilot.eu/flags"
PUBLIC_KEY_B64 = "Pw0CvtpvxfI3G6skI3LMtILJpfvZ/AyH19Vr4HvOUmU="
CACHE_PATH = Path("/data/europilot/flags_cache.json")
REFRESH_INTERVAL = 300  # seconds


class FeatureFlags:
    def __init__(self):
        self._flags: dict[str, str] = {}
        self._kill_switch_active = False
        self._kill_switch_reason = ""
        self._last_fetch = 0.0
        self._lock = threading.Lock()
        self._verify_key = VerifyKey(
            __import__("base64").b64decode(PUBLIC_KEY_B64)
        )
        self._load_cache()

    def _load_cache(self):
        try:
            if CACHE_PATH.exists():
                data = json.loads(CACHE_PATH.read_text())
                self._flags = data.get("flags", {})
                ks = data.get("kill_switch", {})
                self._kill_switch_active = ks.get("active", False)
                self._kill_switch_reason = ks.get("reason", "")
                self._last_fetch = data.get("timestamp", 0)
        except Exception:
            pass

    def _save_cache(self, payload: dict):
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(payload))
        except Exception:
            pass

    def _verify_and_parse(self, raw: bytes) -> dict | None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None

        signature = __import__("base64").b64decode(data.pop("signature", ""))
        data.pop("public_key", None)

        payload_bytes = json.dumps(
            data, separators=(",", ":"), sort_keys=True
        ).encode()

        try:
            self._verify_key.verify(payload_bytes, signature)
        except (BadSignatureError, Exception):
            return None

        return data

    def refresh(self) -> bool:
        import urllib.request

        try:
            req = urllib.request.Request(API_URL, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
        except Exception:
            return False

        data = self._verify_and_parse(raw)
        if data is None:
            return False

        with self._lock:
            self._flags = data.get("flags", {})
            ks = data.get("kill_switch", {})
            self._kill_switch_active = ks.get("active", False)
            self._kill_switch_reason = ks.get("reason", "")
            self._last_fetch = time.monotonic()

        self._save_cache(data)
        return True

    def _maybe_refresh(self):
        if time.monotonic() - self._last_fetch > REFRESH_INTERVAL:
            self.refresh()

    def is_enabled(self, key: str) -> bool:
        self._maybe_refresh()
        with self._lock:
            val = self._flags.get(key)
        if val is None:
            return False
        return val.lower() in ("1", "true", "yes", "on")

    def get(self, key: str, default: str = "") -> str:
        self._maybe_refresh()
        with self._lock:
            return self._flags.get(key, default)

    def is_kill_switch_active(self) -> bool:
        self._maybe_refresh()
        with self._lock:
            return self._kill_switch_active

    def kill_switch_reason(self) -> str:
        with self._lock:
            return self._kill_switch_reason

    @property
    def all_flags(self) -> dict[str, str]:
        self._maybe_refresh()
        with self._lock:
            return dict(self._flags)


_instance: FeatureFlags | None = None


def get_flags() -> FeatureFlags:
    global _instance
    if _instance is None:
        _instance = FeatureFlags()
        _instance.refresh()
    return _instance
