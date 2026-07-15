"""Tests for the average-speed section (trajectcontrole) hold."""

from europilot.cruise import section_hold_kph


def test_no_hold_when_not_in_a_section():
    assert section_hold_kph(0) is None
    assert section_hold_kph(None) is None
    assert section_hold_kph(-1) is None


def test_holds_at_the_enforced_limit_when_inside():
    # a hold is continuous -> it returns the limit regardless of ego speed,
    # so the planner keeps capping cruise for the whole section.
    assert section_hold_kph(80) == 80
    assert section_hold_kph(100) == 100
