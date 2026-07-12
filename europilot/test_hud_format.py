"""Tests for the pure onroad-HUD value logic."""

from europilot.hud_format import context_chip, sign_number, source_badge


class TestSignNumber:
    def test_valid_limit_shows(self):
        assert sign_number(True, 30) == "30"
        assert sign_number(True, 130) == "130"

    def test_invalid_or_missing_shows_nothing(self):
        assert sign_number(False, 50) is None       # message not valid
        assert sign_number(True, -1) is None         # the "unknown" sentinel
        assert sign_number(True, 0) is None
        assert sign_number(True, None) is None

    def test_implausible_high_shows_nothing(self):
        assert sign_number(True, 200) is None        # bad read -> draw nothing

    def test_bool_is_not_a_speed(self):
        assert sign_number(True, True) is None


class TestSourceBadge:
    def test_known_sources(self):
        assert source_badge("ndwMandatory") == "NDW"
        assert source_badge("ndwAdvisory") == "NDW"
        assert source_badge("rsaCamera") == "CAM"
        assert source_badge("osm") == "KAART"
        assert source_badge("timeOfDay") == "DAG"

    def test_unknown_or_none(self):
        assert source_badge("none") is None
        assert source_badge("") is None


class TestContextChip:
    def test_cyclestreet_wins(self):
        assert context_chip(True, in_residential=True, cyclestreet=True) == "FIETSSTRAAT"

    def test_residential(self):
        assert context_chip(True, in_residential=True, cyclestreet=False) == "WOONWIJK"

    def test_nothing_when_plain_or_invalid(self):
        assert context_chip(True, in_residential=False, cyclestreet=False) is None
        assert context_chip(False, in_residential=True, cyclestreet=True) is None
