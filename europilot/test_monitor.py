"""Tests for the Europilot matrix-sign monitor formatting."""

from europilot.monitor import format_summary


class TestFormatSummary:
    def test_mandatory_is_labelled_and_wins(self):
        out = format_summary(70, 90, None, [], False)
        assert out == "now=70 km/h (mandatory) | next=--"

    def test_advisory_when_nothing_binding(self):
        out = format_summary(None, 90, None, [], False)
        assert out == "now=90 km/h (advisory) | next=--"

    def test_nothing_shown(self):
        assert format_summary(None, None, None, [], False) == "now=-- | next=--"

    def test_upcoming_gantry(self):
        out = format_summary(100, None, (50, 300.0), [], False)
        assert out == "now=100 km/h (mandatory) | next=50km/h in 300m"

    def test_closed_lanes_and_flashing(self):
        out = format_summary(50, None, None, [1, 3], True)
        assert out == "now=50 km/h (mandatory) | next=-- | closed=1,3 | FLASHING"
