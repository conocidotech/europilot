"""Tests for the Road Sign Assist speed-limit decoding.

The decoding semantics match sunnypilot/opendbc#289. The msgq publish loop in
main() needs a compiled cereal and runs on-device.
"""

from europilot.rsa import (
    parse_rsa1,
    resolve_speed_limit,
    speed_limit_from_can,
    RSA1_ADDR,
)


def rsa1_bytes(tsgn1=0, spdval1=0):
    """Build an 8-byte RSA1 payload with TSGN1 at byte 0 and SPDVAL1 at byte 2."""
    b = bytearray(8)
    b[0] = tsgn1
    b[2] = spdval1
    return bytes(b)


class TestResolveSpeedLimit:
    def test_kph_sign_carries_limit_directly(self):
        assert resolve_speed_limit(1, 100) == 100
        assert resolve_speed_limit(1, 50) == 50

    def test_kph_bounds(self):
        assert resolve_speed_limit(1, 200) == 200   # upper bound inclusive
        assert resolve_speed_limit(1, 201) is None   # out of range
        assert resolve_speed_limit(1, 0) is None      # 0 is not a valid limit

    def test_mph_sign_is_converted_to_kph(self):
        assert resolve_speed_limit(36, 50) == 80     # 50 mph -> 80 km/h
        assert resolve_speed_limit(36, 60) == 97     # 60 mph -> 97 km/h
        assert resolve_speed_limit(36, 120) == 193   # upper bound

    def test_mph_bounds(self):
        assert resolve_speed_limit(36, 121) is None
        assert resolve_speed_limit(36, 0) is None

    def test_non_speed_sign_yields_nothing(self):
        assert resolve_speed_limit(0, 100) is None    # no sign
        assert resolve_speed_limit(65, 0) is None     # no-overtake sign
        assert resolve_speed_limit(66, 80) is None     # not a speed sign


class TestParseRsa1:
    def test_byte_layout(self):
        assert parse_rsa1(rsa1_bytes(tsgn1=1, spdval1=80)) == (1, 80)
        assert parse_rsa1(rsa1_bytes(tsgn1=36, spdval1=55)) == (36, 55)

    def test_short_or_missing_payload(self):
        assert parse_rsa1(b"\x01\x00") is None   # too short
        assert parse_rsa1(b"") is None
        assert parse_rsa1(None) is None


class TestSpeedLimitFromCan:
    def test_end_to_end_kph(self):
        assert speed_limit_from_can(rsa1_bytes(tsgn1=1, spdval1=130)) == 130

    def test_end_to_end_mph(self):
        assert speed_limit_from_can(rsa1_bytes(tsgn1=36, spdval1=50)) == 80

    def test_no_sign(self):
        assert speed_limit_from_can(rsa1_bytes(tsgn1=0, spdval1=0)) is None

    def test_malformed(self):
        assert speed_limit_from_can(b"\x01") is None


def test_rsa1_address_is_the_camera_message():
    # guards against an accidental edit of the message id we decode
    assert RSA1_ADDR == 0x489
