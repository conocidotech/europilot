"""Tests for the speed-limit fusion policy."""

from europilot.speed_limit import PRIORITY, fuse_speed_limit, motorway_day_limit


class TestFusionPriority:
    def test_mandatory_beats_everything(self):
        assert fuse_speed_limit(ndw_mandatory=100, rsa_camera=130,
                                ndw_advisory=120, osm=80) == (100, "ndwMandatory")

    def test_rsa_when_no_mandatory(self):
        assert fuse_speed_limit(rsa_camera=130, ndw_advisory=120, osm=80) == (130, "rsaCamera")

    def test_advisory_when_no_mandatory_or_rsa(self):
        assert fuse_speed_limit(ndw_advisory=120, osm=80) == (120, "ndwAdvisory")

    def test_osm_beats_time_of_day(self):
        assert fuse_speed_limit(osm=80, time_of_day=100) == (80, "osm")

    def test_time_of_day_is_the_last_fallback(self):
        assert fuse_speed_limit(time_of_day=100) == (100, "timeOfDay")

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
        # guards the policy: mandatory, camera, advisory, map, then time-of-day
        assert PRIORITY == ("ndwMandatory", "rsaCamera", "ndwAdvisory", "osm", "timeOfDay")


class TestMotorwayDayLimit:
    def test_daytime_on_a_motorway_is_100(self):
        assert motorway_day_limit("motorway", 6) == 100     # window is inclusive at 06:00
        assert motorway_day_limit("motorway", 12) == 100
        assert motorway_day_limit("motorway", 18) == 100

    def test_night_defers_to_posted_sources(self):
        assert motorway_day_limit("motorway", 19) is None   # 19:00 is already night
        assert motorway_day_limit("motorway", 3) is None
        assert motorway_day_limit("motorway", 23) is None

    def test_only_motorways(self):
        assert motorway_day_limit("trunk", 12) is None
        assert motorway_day_limit("residential", 12) is None
        assert motorway_day_limit("", 12) is None

    def test_unknown_hour_yields_nothing(self):
        assert motorway_day_limit("motorway", None) is None

    def test_it_plugs_into_fusion_as_the_daytime_fallback(self):
        assert fuse_speed_limit(time_of_day=motorway_day_limit("motorway", 12)) == (100, "timeOfDay")


def test_source_names_are_valid_capnp_enumerants():
    # every source fuse() can emit must exist in euSpeedLimit.Source
    valid = {"none", "rsaCamera", "ndwMandatory", "ndwAdvisory", "osm", "timeOfDay"}
    assert set(PRIORITY) | {"none"} == valid
