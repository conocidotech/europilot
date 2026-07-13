"""Tests for europilot_heartbeatd's payload building and posting.

No network and no openpilot build required: build_payload takes a duck-typed
params object, and post_heartbeat is exercised against a stub opener.
"""

import json

from europilot import heartbeat


class FakeParams:
    def __init__(self, values: dict):
        self._v = values

    def get(self, key, *args, **kwargs):
        return self._v.get(key)


def test_none_without_dongle():
    assert heartbeat.build_payload(FakeParams({})) is None
    assert heartbeat.build_payload(FakeParams({"DongleId": ""})) is None


def test_decodes_bytes_dongle():
    p = heartbeat.build_payload(FakeParams({"DongleId": b"057af248360e1706"}))
    assert p["dongle_id"] == "057af248360e1706"


def test_default_name_when_no_file():
    assert heartbeat.build_payload(FakeParams({"DongleId": "x"}))["device_name"] == heartbeat.DEVICE_NAME


def test_name_file_override(tmp_path, monkeypatch):
    f = tmp_path / "name"
    f.write_text("Lexus NX\n")
    monkeypatch.setattr(heartbeat, "NAME_FILE", str(f))
    assert heartbeat.build_payload(FakeParams({"DongleId": "x"}))["device_name"] == "Lexus NX"


def test_payload_shape():
    p = heartbeat.build_payload(FakeParams({"DongleId": "x"}))
    assert set(p) == {"dongle_id", "device_name", "car_model", "software_version"}


def test_posts_json_body_to_endpoint(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data)
        seen["ctype"] = req.headers.get("Content-type")
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    heartbeat.post_heartbeat("https://app.europilot.eu", {"dongle_id": "x"})

    assert seen["url"] == "https://app.europilot.eu/api/devices/heartbeat"
    assert seen["body"] == {"dongle_id": "x"}
    assert seen["ctype"] == "application/json"
