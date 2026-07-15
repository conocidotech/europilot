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


def test_telemetry_none_without_gps():
    assert heartbeat.build_telemetry("x", None, [], []) is None
    assert heartbeat.build_telemetry("x", {"lat": None, "lon": 4.0}, [], []) is None
    assert heartbeat.build_telemetry("", {"lat": 52.0, "lon": 4.0}, [], []) is None


def test_telemetry_basic_frame():
    gps = {"lat": 52.1, "lon": 4.2, "bearing": 90.0, "speed": 13.4}
    leads = [{"x": 30.0, "y": 0.5, "v": 12.0, "prob": 0.9}]
    tracks = [{"x": 40.0, "y": -3.0, "v": -2.0}, {"x": 25.0, "y": 3.5, "v": 1.0}]
    f = heartbeat.build_telemetry("dongle", gps, leads, tracks)
    assert f["lat"] == 52.1 and f["lon"] == 4.2
    assert f["bearing"] == 90.0 and f["speed_mps"] == 13.4
    assert f["leads"] == [{"x": 30.0, "y": 0.5, "v": 12.0, "prob": 0.9}]
    assert len(f["tracks"]) == 2


def test_telemetry_drops_nan_and_caps():
    nan = float("nan")
    gps = {"lat": 52.0, "lon": 4.0, "bearing": nan, "speed": 10.0}
    # one object has NaN x -> dropped; more than the cap -> truncated
    objs = [{"x": nan, "y": 1.0}] + [{"x": float(i), "y": 0.0} for i in range(50)]
    f = heartbeat.build_telemetry("d", gps, objs, [])
    assert f["bearing"] is None            # NaN scalar -> None
    assert len(f["leads"]) == heartbeat.TELEMETRY_MAX_OBJECTS
    assert all("x" in o and "y" in o for o in f["leads"])


def test_telemetry_omits_missing_optional_fields():
    gps = {"lat": 52.0, "lon": 4.0, "bearing": 0.0, "speed": 0.0}
    f = heartbeat.build_telemetry("d", gps, [{"x": 10.0, "y": 0.0}], [])
    assert f["leads"] == [{"x": 10.0, "y": 0.0}]   # no v / prob keys when absent


def test_telemetry_carries_easing_when_present():
    gps = {"lat": 52.0, "lon": 4.0, "bearing": 0.0, "speed": 0.0}
    easing = {"reason": "curve", "target_kph": 60, "distance_m": 120.0}
    f = heartbeat.build_telemetry("d", gps, [], [], easing)
    assert f["easing"] == easing


def test_telemetry_omits_easing_when_absent():
    gps = {"lat": 52.0, "lon": 4.0, "bearing": 0.0, "speed": 0.0}
    assert "easing" not in heartbeat.build_telemetry("d", gps, [], [])
    assert "easing" not in heartbeat.build_telemetry("d", gps, [], [], None)


class _Gps:
    def __init__(self, hasFix, lat=52.0, lon=4.0, bearing=90.0, speed=10.0):
        self.hasFix, self.latitude, self.longitude = hasFix, lat, lon
        self.bearingDeg, self.speed = bearing, speed


class _SM:
    def __init__(self, msgs, valid):
        self._msgs, self.valid = msgs, valid

    def __getitem__(self, k):
        return self._msgs[k]


def test_read_gps_prefers_first_valid_with_fix():
    sm = _SM({"gpsLocation": _Gps(True, lat=51.5), "gpsLocationExternal": _Gps(True, lat=52.5)},
             {"gpsLocation": True, "gpsLocationExternal": True})
    assert heartbeat._read_gps(sm)["lat"] == 51.5   # gpsLocation wins (listed first)


def test_read_gps_falls_back_to_external():
    sm = _SM({"gpsLocation": _Gps(False), "gpsLocationExternal": _Gps(True, lat=52.5)},
             {"gpsLocation": True, "gpsLocationExternal": True})
    assert heartbeat._read_gps(sm)["lat"] == 52.5   # gpsLocation has no fix -> external


def test_read_gps_none_when_no_fix_or_invalid():
    sm = _SM({"gpsLocation": _Gps(False), "gpsLocationExternal": _Gps(True)},
             {"gpsLocation": True, "gpsLocationExternal": False})   # external not valid
    assert heartbeat._read_gps(sm) is None


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
