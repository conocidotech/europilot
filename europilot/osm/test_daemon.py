"""Tests for the europilot_osmd Advisory -> euMapAdvisory field mapping."""

from pathlib import Path

import pytest

from europilot.osm.daemon import advisory_to_fields
from europilot.osm.types import Advisory


class TestAdvisoryToFields:
    def test_valid_advisory(self):
        adv = Advisory(valid=True, road_id=101, road_class="residential",
                       name="Speeltuinlaan", speed_limit=30, comfort_speed=30,
                       in_residential=True, cycleway_left="present",
                       cycleway_right="unknown", cyclestreet=False, distance_m=3.4)
        f = advisory_to_fields(adv)
        assert f["valid"] is True
        assert f["roadClass"] == "residential" and f["name"] == "Speeltuinlaan"
        assert f["speedLimit"] == 30 and f["comfortSpeed"] == 30
        assert f["cyclewayLeft"] == "present" and f["cyclewayRight"] == "unknown"
        assert f["distance"] == pytest.approx(3.4)

    def test_none_advisory_uses_sentinels(self):
        f = advisory_to_fields(Advisory.none())
        assert f["valid"] is False
        assert f["speedLimit"] == -1 and f["comfortSpeed"] == -1
        assert f["distance"] == -1.0
        assert f["roadClass"] == "" and f["name"] == ""
        assert f["cyclewayLeft"] == "unknown"
        assert f["cameraDistance"] == -1.0 and f["cameraLimit"] == -1 and f["cameraKind"] == ""

    def test_camera_ahead_fields(self):
        adv = Advisory(valid=True, camera_distance_m=685.0, camera_limit=100,
                       camera_kind="fixed", distance_m=2.0)
        f = advisory_to_fields(adv)
        assert f["cameraDistance"] == pytest.approx(685.0)
        assert f["cameraLimit"] == 100 and f["cameraKind"] == "fixed"

    def test_unknown_speed_is_sentinel(self):
        adv = Advisory(valid=True, speed_limit=None, comfort_speed=None, distance_m=12.0)
        f = advisory_to_fields(adv)
        assert f["speedLimit"] == -1 and f["comfortSpeed"] == -1

    def test_out_of_range_speed_is_clamped_to_sentinel(self):
        # A corrupt tile value must not reach the Int16 wire field unbounded.
        adv = Advisory(valid=True, speed_limit=40000, comfort_speed=-5, distance_m=1.0)
        f = advisory_to_fields(adv)
        assert f["speedLimit"] == -1 and f["comfortSpeed"] == -1


class TestSchemaMapping:
    """The mapped fields must actually exist on the MapAdvisory struct."""

    def setup_method(self):
        pytest.importorskip("capnp")

    def test_fields_apply_to_capnp_struct(self):
        import capnp
        capnp.remove_import_hook()
        schema = capnp.load(str(Path(__file__).resolve().parents[2] / "cereal" / "custom.capnp"))

        adv = Advisory(valid=True, road_class="residential", name="X", speed_limit=30,
                       comfort_speed=40, in_residential=True, cycleway_left="present",
                       cycleway_right="absent", cyclestreet=True, distance_m=2.0,
                       camera_distance_m=685.0, camera_limit=100, camera_kind="section")
        msg = schema.MapAdvisory.new_message()
        for field, value in advisory_to_fields(adv).items():
            setattr(msg, field, value)   # raises if any field/enum name is wrong

        with schema.MapAdvisory.from_bytes(msg.to_bytes()) as r:
            assert r.valid and r.speedLimit == 30 and r.comfortSpeed == 40
            assert str(r.cyclewayLeft) == "present" and str(r.cyclewayRight) == "absent"
            assert r.inResidential and r.cyclestreet
            assert r.cameraLimit == 100 and str(r.cameraKind) == "section"
            assert r.cameraDistance == pytest.approx(685.0)
