"""Tests for road-attached speed-camera derivation."""

from pathlib import Path

from gateway.osm.cameras import cameras_by_way
from gateway.osm.osm_source import parse_full

CAMERAS = (Path(__file__).parent / "test_data" / "cameras.osm").read_bytes()
MAINLINE, RAMP = 200, 201


def _by_way():
    return cameras_by_way(parse_full(CAMERAS))


class TestCameraAttachment:
    def test_fixed_camera_on_the_mainline(self):
        fixed = [c for c in _by_way()[MAINLINE] if c["kind"] == "fixed"]
        assert any(abs(c["lat"] - 52.0001) < 1e-6 and c["maxspeed"] == 100 for c in fixed)

    def test_section_camera_from_the_enforcement_relation(self):
        section = [c for c in _by_way()[MAINLINE] if c["kind"] == "section"]
        assert len(section) == 1
        assert section[0]["maxspeed"] == 100
        assert abs(section[0]["lon"] - 5.002) < 1e-6   # the section 'from' point

    def test_ramp_camera_attaches_to_the_ramp_not_the_mainline(self):
        by_way = _by_way()
        # the ramp camera binds to the ramp...
        assert any(abs(c["lat"] - 52.0051) < 1e-6 for c in by_way[RAMP])
        # ...and NEVER to the mainline. This is the whole point: no phantom braking
        # on the motorway for a camera that actually sits on the off-ramp.
        assert not any(abs(c["lat"] - 52.0051) < 1e-6 for c in by_way.get(MAINLINE, []))

    def test_camera_far_from_any_road_is_dropped(self):
        cams = [c for cs in _by_way().values() for c in cs]
        assert not any(c["maxspeed"] == 80 for c in cams)   # node 32, ~55 m off a road

    def test_unknown_limit_stays_none(self):
        ramp = _by_way()[RAMP]
        assert ramp and ramp[0]["maxspeed"] is None   # camera 31 carried no maxspeed
