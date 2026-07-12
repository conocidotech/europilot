"""Tests for the NDW matrix-sign advisory consumer helpers.

The accessors are pure and read via attribute access, so we drive them with
lightweight doubles that mimic the capnp readers.
"""

from types import SimpleNamespace as NS

from europilot.advisories import (
    governing_gantry,
    upcoming_gantry,
    mandatory_speed,
    advisory_speed,
    target_speed,
    upcoming_target_speed,
    closed_lanes,
    is_flashing,
)


def gantry(valid=True, distance=100.0, mandatory=-1, advisory=-1, target=-1,
           flashing=False, lanes=()):
    return NS(valid=valid, distance=distance, mandatorySpeed=mandatory,
              advisorySpeed=advisory, targetSpeed=target, flashing=flashing,
              closedLanes=list(lanes), road="A2", carriageway="R")


def signs(valid=True, governing=None, upcoming=None):
    return NS(valid=valid,
              governing=governing if governing is not None else gantry(valid=False),
              upcoming=upcoming if upcoming is not None else gantry(valid=False))


class TestValidity:
    def test_no_message_yields_nothing(self):
        assert governing_gantry(None) is None
        assert mandatory_speed(None) is None
        assert closed_lanes(None) == []
        assert is_flashing(None) is False

    def test_invalid_message_yields_nothing(self):
        s = signs(valid=False, governing=gantry(mandatory=100))
        assert governing_gantry(s) is None
        assert mandatory_speed(s) is None

    def test_unmatched_gantry_yields_nothing(self):
        s = signs(governing=gantry(valid=False, mandatory=100))
        assert governing_gantry(s) is None
        assert mandatory_speed(s) is None


class TestSpeeds:
    def test_mandatory_and_advisory_are_not_conflated(self):
        s = signs(governing=gantry(mandatory=70, advisory=90, target=70))
        assert mandatory_speed(s) == 70
        assert advisory_speed(s) == 90
        assert target_speed(s) == 70

    def test_advisory_only_gantry_has_no_mandatory_speed(self):
        s = signs(governing=gantry(mandatory=-1, advisory=90, target=90))
        assert mandatory_speed(s) is None      # nothing legally binding shown
        assert advisory_speed(s) == 90
        assert target_speed(s) == 90

    def test_gantry_showing_no_speed(self):
        s = signs(governing=gantry(mandatory=-1, advisory=-1, target=-1))
        assert mandatory_speed(s) is None
        assert advisory_speed(s) is None
        assert target_speed(s) is None


class TestUpcoming:
    def test_upcoming_speed_and_distance(self):
        s = signs(upcoming=gantry(target=50, distance=300.0))
        assert upcoming_target_speed(s) == (50, 300.0)
        assert upcoming_gantry(s) is not None

    def test_no_upcoming_gantry(self):
        assert upcoming_target_speed(signs()) is None

    def test_upcoming_without_a_speed(self):
        assert upcoming_target_speed(signs(upcoming=gantry(target=-1))) is None


class TestLanesAndFlashing:
    def test_closed_lanes(self):
        s = signs(governing=gantry(lanes=[1, 3]))
        assert closed_lanes(s) == [1, 3]

    def test_no_closed_lanes(self):
        assert closed_lanes(signs(governing=gantry())) == []

    def test_flashing(self):
        assert is_flashing(signs(governing=gantry(flashing=True))) is True
        assert is_flashing(signs(governing=gantry(flashing=False))) is False
