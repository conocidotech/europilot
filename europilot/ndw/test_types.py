"""Tests for the NDW value types' input coercion.

The gateway feed is not signed yet, so values arriving here are untrusted on
their way into fixed-width capnp fields. coerce_speed / int8_lanes turn anything
implausible into a safe "no reading" instead of a daemon crash or a bogus limit.
"""

from europilot.gateway import int8_lanes
from europilot.ndw.types import Display, coerce_speed


class TestCoerceSpeed:
    def test_plain_int_kept(self):
        assert coerce_speed(80) == 80
        assert coerce_speed(1) == 1

    def test_numeric_string_parsed(self):
        assert coerce_speed("100") == 100

    def test_junk_becomes_none(self):
        assert coerce_speed(None) is None
        assert coerce_speed("fast") is None
        assert coerce_speed("80.5") is None
        assert coerce_speed([80]) is None

    def test_out_of_range_becomes_none(self):
        assert coerce_speed(0) is None
        assert coerce_speed(-30) is None
        assert coerce_speed(40000) is None   # would overflow the Int16 wire field

    def test_bool_is_not_a_speed(self):
        assert coerce_speed(True) is None
        assert coerce_speed(False) is None


class TestDisplayFromJson:
    def test_bad_speed_is_coerced_to_none(self):
        d = Display.from_json("uuid-1", {"aspect": "speed", "speed": "80000"})
        assert d.speed is None

    def test_good_speed_survives(self):
        d = Display.from_json("uuid-1", {"aspect": "speed", "speed": 50})
        assert d.speed == 50


class TestInt8Lanes:
    def test_in_range_kept(self):
        assert int8_lanes([0, 1, 2, 3]) == [0, 1, 2, 3]

    def test_out_of_range_dropped(self):
        assert int8_lanes([1, 200, 2, -300]) == [1, 2]

    def test_non_int_dropped(self):
        assert int8_lanes([1, "2", None, True, 3]) == [1, 3]
