"""Device-side tests. They run against a region snapshot exactly as the gateway
would hand it over, so nothing here touches the network.

The most important test in this file is `test_device_never_names_a_third_party`:
it enforces the binding rule that the device talks to app.europilot.eu and to
nobody else.
"""

import json
import math
import pathlib
import time

import pytest

from europilot.ndw import client as client_mod
from europilot.ndw.client import MatrixSignClient, tile_of
from europilot.ndw.match import MAX_BEARING_DELTA_DEG, GantryIndex
from europilot.ndw.types import BLOCKING, Display, Sign

DATA = pathlib.Path(__file__).resolve().parent / "test_data"
PACKAGE = pathlib.Path(__file__).resolve().parent
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
def payload():
    return json.loads((DATA / "region_a2.json").read_text())


@pytest.fixture(scope="module")
def signs(payload):
    return [Sign.from_json(s) for s in payload["signs"]]


@pytest.fixture(scope="module")
def states(payload):
    return {u: Display.from_json(u, d) for u, d in payload["states"].items()}


@pytest.fixture(scope="module")
def index(signs):
    return GantryIndex(signs)


@pytest.fixture(scope="module")
def bounds(payload):
    return payload["bounds"]


def snapshot_at(bounds, signs, states, age_offset=0.0):
    return client_mod._Snapshot(
        (0, 0), bounds, GantryIndex(signs), states, time.monotonic() - age_offset
    )


@pytest.fixture(scope="module")
def speed_sign(signs, states):
    """A sign on a gantry currently showing a speed on several lanes."""
    by_gantry = {}
    for s in signs:
        by_gantry.setdefault(s.gantry_key, []).append(s)
    for members in by_gantry.values():
        if len(members) >= 2 and any(states[s.uuid].speed for s in members):
            return members[0]
    pytest.skip("no gantry showing a speed in this snapshot")


# --- the binding architecture rule ---------------------------------------


def test_device_never_names_a_third_party():
    """The device knows one host: app.europilot.eu. See `architectuur-data-gateway`."""
    forbidden = ("ndw.nu", "opendata.ndw", "rijkswaterstaat", "openstreetmap", "comma.ai", "mapbox")
    for path in PACKAGE.glob("*.py"):
        if path.name.startswith("test_"):
            continue
        source = path.read_text().lower()
        for needle in forbidden:
            assert needle not in source, f"{path.name} names {needle}"


def test_client_default_host_is_the_gateway(monkeypatch):
    monkeypatch.delenv("EUROPILOT_API_HOST", raising=False)
    assert client_mod.gateway_host() == "https://app.europilot.eu"


# --- cold path / hot path separation --------------------------------------


def test_match_returns_none_without_a_snapshot():
    assert MatrixSignClient(host="https://example.invalid").match(52.0, 5.0, 90.0) is None


def test_match_never_hits_the_network(monkeypatch, bounds, signs, states, speed_sign):
    c = MatrixSignClient(host="https://example.invalid")
    c._snapshot = snapshot_at(bounds, signs, states)

    def explode(*a, **k):
        raise AssertionError("hot path opened a socket")

    monkeypatch.setattr(client_mod.urllib.request, "urlopen", explode)

    lat, lon = offset(speed_sign.lat, speed_sign.lon, speed_sign.bearing, -400)
    assert c.match(lat, lon, speed_sign.bearing) is not None


def test_snapshot_answers_across_the_raw_tile_boundary(bounds, signs, states, speed_sign):
    """The approach to a gantry can straddle a tile edge. The padding exists for
    exactly this; an exact-tile check would blind the car right before the sign."""
    lat, lon = offset(speed_sign.lat, speed_sign.lon, speed_sign.bearing, -400)
    assert tile_of(lat, lon) != tile_of(speed_sign.lat, speed_sign.lon)

    c = MatrixSignClient(host="https://example.invalid")
    c._snapshot = snapshot_at(bounds, signs, states)
    m = c.match(lat, lon, speed_sign.bearing)
    assert m is not None and m.upcoming is not None
    assert m.upcoming.km == speed_sign.km


def test_stale_snapshot_yields_no_hint(bounds, signs, states, speed_sign):
    c = MatrixSignClient(host="https://example.invalid")
    c._snapshot = snapshot_at(bounds, signs, states, age_offset=client_mod.STALE_AFTER_S + 1.0)
    assert c.match(speed_sign.lat, speed_sign.lon, speed_sign.bearing) is None


def test_pose_outside_the_snapshot_bounds_yields_no_hint(bounds, signs, states):
    c = MatrixSignClient(host="https://example.invalid")
    c._snapshot = snapshot_at(bounds, signs, states)
    assert c.match(bounds["max_lat"] + 1.0, bounds["max_lon"] + 1.0, 90.0) is None


def test_pose_just_inside_the_bounds_edge_yields_no_hint(bounds, signs, states):
    """Within a search radius of the edge, a matching sign might be missing."""
    c = MatrixSignClient(host="https://example.invalid")
    c._snapshot = snapshot_at(bounds, signs, states)
    edge_lat = bounds["max_lat"] - 0.001
    assert c.match(edge_lat, (bounds["min_lon"] + bounds["max_lon"]) / 2, 180.0) is None


# --- geometry --------------------------------------------------------------


def test_region_payload_is_self_consistent(signs, states):
    assert signs
    assert len(states) == len(signs)
    for s in signs:
        assert s.uuid in states
        assert 50.0 < s.lat < 54.0
        assert 3.0 < s.lon < 7.5
        assert 0.0 <= s.bearing < 360.0


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


def test_closed_lanes_are_excluded_from_the_speed(index, states, signs):
    by_gantry = {}
    for s in signs:
        by_gantry.setdefault(s.gantry_key, []).append(s)

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
