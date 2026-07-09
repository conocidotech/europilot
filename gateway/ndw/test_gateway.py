"""Gateway tests run against a real NDW snapshot checked into test_data/, so they
assert on structure rather than on specific live sign values. Drift in the live
feed is caught by gateway/ndw/schema_check.py, not here."""

import math
import pathlib

import pytest

from europilot.ndw.types import BLOCKING
from gateway.ndw import region
from gateway.ndw.feed import load
from gateway.ndw.static_index import load_signs

DATA = pathlib.Path(__file__).resolve().parent / "test_data"
PLAUSIBLE_SPEEDS = {30, 50, 70, 80, 90, 100, 120, 130}


@pytest.fixture(scope="module")
def signs():
    return load_signs(str(DATA / "msi_shp.zip"))


@pytest.fixture(scope="module")
def states():
    return load(str(DATA / "msi.xml.gz"))


def test_shapefile_covers_the_netherlands(signs):
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


def test_mandatory_and_advisory_speeds_both_occur(states):
    """Red ring means legally binding; without it, advice. Both must survive parsing."""
    speed_signs = [d for d in states.values() if d.aspect == "speedlimit"]
    assert any(d.red_ring for d in speed_signs)
    assert any(not d.red_ring for d in speed_signs)


# --- region tiling ---------------------------------------------------------


def test_tile_bounds_are_padded_beyond_the_raw_tile():
    min_lat, min_lon, max_lat, max_lon = region.tile_bounds(207, 20)
    assert min_lat < 207 * region.TILE_DEG
    assert max_lat > 208 * region.TILE_DEG
    assert min_lon < 20 * region.TILE_DEG
    assert max_lon > 21 * region.TILE_DEG


def test_padding_is_at_least_the_matcher_search_radius():
    """A car at a tile edge must still see the gantry it is about to pass."""
    min_lat, _, max_lat, _ = region.tile_bounds(207, 20)
    pad_deg = (207 * region.TILE_DEG) - min_lat
    pad_m = math.radians(pad_deg) * region.EARTH_RADIUS_M
    assert pad_m >= 2000.0
    assert max_lat - min_lat > region.TILE_DEG


def test_region_payload_only_contains_signs_with_a_state(signs, states):
    payload = region.build(signs, states, 207, 20, age_s=1.0)
    assert payload["signs"]
    assert set(payload["states"]) == {s["uuid"] for s in payload["signs"]}
    assert payload["tile_deg"] == region.TILE_DEG
    assert payload["age_s"] == 1.0


def test_region_payload_is_a_strict_subset(signs, states):
    payload = region.build(signs, states, 207, 20, age_s=1.0)
    assert 0 < len(payload["signs"]) < len(signs)


def test_signs_outside_the_padded_tile_are_dropped(signs, states):
    payload = region.build(signs, states, 207, 20, age_s=1.0)
    min_lat, min_lon, max_lat, max_lon = region.tile_bounds(207, 20)
    for s in payload["signs"]:
        assert min_lat <= s["lat"] <= max_lat
        assert min_lon <= s["lon"] <= max_lon


def test_empty_tile_yields_an_empty_payload(signs, states):
    payload = region.build(signs, states, 100, 100, age_s=1.0)
    assert payload["signs"] == []
    assert payload["states"] == {}
