"""Tests for the speed-limit fusion policy."""

from europilot.speed_limit import fuse_speed_limit, PRIORITY


class TestFusionPriority:
    def test_mandatory_beats_everything(self):
        assert fuse_speed_limit(ndw_mandatory=100, rsa_camera=130,
                                ndw_advisory=120, osm=80) == (100, "ndwMandatory")

    def test_rsa_when_no_mandatory(self):
        assert fuse_speed_limit(rsa_camera=130, ndw_advisory=120, osm=80) == (130, "rsaCamera")

    def test_advisory_when_no_mandatory_or_rsa(self):
        assert fuse_speed_limit(ndw_advisory=120, osm=80) == (120, "ndwAdvisory")

    def test_osm_is_the_last_fallback(self):
        assert fuse_speed_limit(osm=80) == (80, "osm")

    def test_nothing_available(self):
        assert fuse_speed_limit() == (None, "none")
        assert fuse_speed_limit(ndw_mandatory=None, rsa_camera=None) == (None, "none")


class TestFusionValues:
    def test_zero_or_negative_is_not_a_value(self):
        # a source reporting 0 / -1 is "no reading", so fusion falls through
        assert fuse_speed_limit(ndw_mandatory=0, rsa_camera=100) == (100, "rsaCamera")
        assert fuse_speed_limit(ndw_mandatory=-1, ndw_advisory=90) == (90, "ndwAdvisory")

    def test_only_lower_priority_present(self):
        assert fuse_speed_limit(ndw_advisory=90) == (90, "ndwAdvisory")

    def test_priority_order_is_the_documented_one(self):
        # guards the policy: mandatory, then camera, then advisory, then map
        assert PRIORITY == ("ndwMandatory", "rsaCamera", "ndwAdvisory", "osm")


def test_source_names_are_valid_capnp_enumerants():
    # every source fuse() can emit must exist in euSpeedLimit.Source
    valid = {"none", "rsaCamera", "ndwMandatory", "ndwAdvisory", "osm"}
    assert set(PRIORITY) | {"none"} == valid
