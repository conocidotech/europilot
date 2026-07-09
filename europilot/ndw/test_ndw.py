"""Tests run against a real NDW snapshot checked into test_data/, so they assert
on structure and geometry rather than on specific live sign values. Drift in the
live feed is caught by europilot/ndw/schema_check.py, not here."""

import math
import pathlib
import time

import pytest

from europilot.ndw.feed import BLOCKING, load
from europilot.ndw.match import MAX_BEARING_DELTA_DEG, GantryIndex
from europilot.ndw.static_index import load_signs

DATA = pathlib.Path(__file__).resolve().parent / "test_data"
EARTH_RADIUS_M = 6_371_000.0

PLAUSIBLE_SPEEDS = {30, 50, 70, 80, 90, 100, 120, 130}


def offset(lat, lon, bearing, dist_m):
    """Move dist_m along bearing from (lat, lon). Negative goes backwards."""
    br = math.radians(bearing)
    d_north = math.cos(br) * dist_m
    d_east = math.sin(br) * dist_m
    return (
        lat + math.degrees(d_north / EARTH_RADIUS_M),
        lon + math.degrees(d_east / (EARTH_RADIUS_M * math.cos(math.radians(lat)))),
    )


@pytest.fixture(scope="module")
def signs():
    return load_signs(str(DATA / "msi_shp.zip"))


@pytest.fixture(scope="module")
def states():
    return load(str(DATA / "msi.xml.gz"))


@pytest.fixture(scope="module")
def index(signs):
    return GantryIndex(signs)


@pytest.fixture(scope="module")
def by_gantry(signs, states):
    out = {}
    for s in signs:
        if s.uuid in states:
            out.setdefault(s.gantry_key, []).append(s)
    return out


@pytest.fixture(scope="module")
def speed_sign(by_gantry, states):
    """A sign on a gantry that is currently showing a speed on several lanes."""
    for members in by_gantry.values():
        if len(members) >= 2 and any(states[s.uuid].speed for s in members):
            return members[0]
    pytest.skip("no gantry showing a speed in this snapshot")


def test_static_index_is_within_the_netherlands(signs):
    assert len(signs) > 15_000
    for s in signs:
        assert 50.0 < s.lat < 54.0, s
        assert 3.0 < s.lon < 7.5, s
        assert 0.0 <= s.bearing < 360.0, s


def test_nearly_every_static_sign_has_a_live_state(signs, states):
    matched = sum(1 for s in signs if s.uuid in states)
    assert matched / len(signs) > 0.99


def test_speed_limits_are_plausible(states):
    speeds = [d.speed for d in states.values() if d.speed is not None]
    assert speeds
    assert set(speeds) <= PLAUSIBLE_SPEEDS


def test_blocking_aspects_carry_no_speed(states):
    for d in states.values():
        if d.aspect in BLOCKING:
            assert d.speed is None, d


def test_governing_gantry_is_the_one_just_passed(index, states, speed_sign):
    lat, lon = offset(speed_sign.lat, speed_sign.lon, speed_sign.bearing, 120)
    m = index.match(lat, lon, speed_sign.bearing, states)

    assert m.governing is not None
    assert m.governing.km == speed_sign.km
    assert m.governing.distance_m <= 0.0
    assert m.governing.distance_m == pytest.approx(-120.0, abs=5.0)


def test_upcoming_gantry_is_ahead(index, states, speed_sign):
    lat, lon = offset(speed_sign.lat, speed_sign.lon, speed_sign.bearing, -400)
    m = index.match(lat, lon, speed_sign.bearing, states)

    assert m.upcoming is not None
    assert m.upcoming.km == speed_sign.km
    assert m.upcoming.distance_m > 0.0


def test_target_speed_prefers_the_lower_of_mandatory_and_advisory(index, states, speed_sign):
    lat, lon = offset(speed_sign.lat, speed_sign.lon, speed_sign.bearing, -400)
    g = index.match(lat, lon, speed_sign.bearing, states).upcoming

    shown = [s for s in (g.mandatory_speed, g.advisory_speed) if s is not None]
    assert shown
    assert g.target_speed == min(shown)


def test_closed_lanes_are_excluded_from_the_speed(index, states, by_gantry):
    for members in by_gantry.values():
        blocked = [s for s in members if states[s.uuid].blocks_lane]
        speeds = [s for s in members if states[s.uuid].speed is not None]
        if not (blocked and speeds):
            continue

        sign = members[0]
        lat, lon = offset(sign.lat, sign.lon, sign.bearing, 100)
        g = index.match(lat, lon, sign.bearing, states).governing
        if g is None or g.km != sign.km:
            continue

        assert g.closed_lanes
        assert g.target_speed is not None
        assert all(g.lanes[lane].speed is None for lane in g.closed_lanes)
        return

    pytest.skip("no gantry with both a closed lane and a speed in this snapshot")


def test_perpendicular_heading_matches_nothing(index, states, speed_sign):
    lat, lon = offset(speed_sign.lat, speed_sign.lon, speed_sign.bearing, -200)
    across = (speed_sign.bearing + 90.0) % 360.0

    m = index.match(lat, lon, across, states)
    assert m.governing is None
    assert m.upcoming is None


def test_bearing_tolerance_is_respected(index, states, speed_sign):
    # Put the car on its own heading axis through the sign, so cross-track is ~0
    # and only the bearing filter can reject the match.
    def match_at(heading):
        lat, lon = offset(speed_sign.lat, speed_sign.lon, heading, -300)
        return index.match(lat, lon, heading, states).upcoming

    assert match_at((speed_sign.bearing + MAX_BEARING_DELTA_DEG - 5.0) % 360.0) is not None
    assert match_at((speed_sign.bearing + MAX_BEARING_DELTA_DEG + 15.0) % 360.0) is None


def test_opposite_carriageway_is_never_matched(index, states, speed_sign):
    lat, lon = offset(speed_sign.lat, speed_sign.lon, speed_sign.bearing, 100)
    reverse = (speed_sign.bearing + 180.0) % 360.0

    m = index.match(lat, lon, reverse, states)
    for g in (m.governing, m.upcoming):
        if g is not None:
            assert g.carriageway != speed_sign.carriageway


def test_match_is_fast_enough_for_20hz(index, states, speed_sign):
    lat, lon = offset(speed_sign.lat, speed_sign.lon, speed_sign.bearing, -300)

    start = time.monotonic()
    for _ in range(200):
        index.match(lat, lon, speed_sign.bearing, states)
    per_call_ms = (time.monotonic() - start) / 200 * 1000

    assert per_call_ms < 5.0, f"{per_call_ms:.2f} ms per match"
