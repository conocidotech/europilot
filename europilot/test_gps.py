"""Tests for multi-source, fix-gated GPS pose selection."""

from europilot.gps import read_pose, GpsHealth, MAX_HACC_M


class _G:
    def __init__(self, hasFix, lat=52.0, lon=5.0, bearing=90.0, hacc=5.0):
        self.hasFix = hasFix
        self.latitude, self.longitude, self.bearingDeg = lat, lon, bearing
        self.horizontalAccuracy = hacc


class _SM:
    def __init__(self, msgs, valid=None, frames=None):
        self._m = msgs
        self.valid = valid or dict.fromkeys(msgs, True)
        self.recv_frame = frames or dict.fromkeys(msgs, 1)

    def __getitem__(self, k):
        return self._m[k]


def test_none_when_no_source_has_a_fix():
    sm = _SM({"gpsLocation": _G(False), "gpsLocationExternal": _G(False)})
    assert read_pose(sm) is None


def test_uses_quectel_when_only_it_has_a_fix():
    sm = _SM({"gpsLocation": _G(True, lat=52.1), "gpsLocationExternal": _G(False)})
    p = read_pose(sm)
    assert p is not None and p.source == "gpsLocation" and p.lat == 52.1


def test_prefers_external_when_both_have_a_fix():
    sm = _SM({"gpsLocation": _G(True), "gpsLocationExternal": _G(True, lat=52.5)})
    p = read_pose(sm)
    assert p.source == "gpsLocationExternal" and p.lat == 52.5


def test_falls_back_when_external_has_no_fix():
    sm = _SM({"gpsLocation": _G(True, lat=52.2), "gpsLocationExternal": _G(False)})
    assert read_pose(sm).source == "gpsLocation"


def test_rejects_a_fix_too_crude():
    sm = _SM({"gpsLocationExternal": _G(True, hacc=MAX_HACC_M + 10),
              "gpsLocation": _G(True, lat=52.3, hacc=8.0)})
    p = read_pose(sm)
    assert p.source == "gpsLocation" and p.lat == 52.3   # crude ublox skipped


def test_unknown_accuracy_is_not_rejected():
    sm = _SM({"gpsLocationExternal": _G(True, hacc=0.0), "gpsLocation": _G(False)})
    assert read_pose(sm) is not None


def test_skips_a_source_with_no_recv_frame():
    sm = _SM({"gpsLocationExternal": _G(True), "gpsLocation": _G(True, lat=52.4)},
             frames={"gpsLocationExternal": 0, "gpsLocation": 3})
    assert read_pose(sm).lat == 52.4


def test_tolerates_a_caller_that_subscribed_one_source():
    sm = _SM({"gpsLocation": _G(True, lat=52.9)})   # no external key at all
    assert read_pose(sm).lat == 52.9


def test_health_reports_transitions_only():
    h = GpsHealth()
    sm_fix = _SM({"gpsLocationExternal": _G(True)})
    p = read_pose(sm_fix)
    assert h.transition(p).startswith("GPS: fix from gpsLocationExternal")
    assert h.transition(p) is None                     # steady -> silent
    assert "no usable fix" in h.transition(None)       # lost
    assert h.transition(None) is None                  # steady -> silent
