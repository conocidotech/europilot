"""Tests for the Europilot gateway advisory consumer helpers.

The selection logic is pure and reads its inputs via attribute access, so we
drive it with lightweight test doubles that mimic the capnp readers.
"""

import unittest
from types import SimpleNamespace as NS

from europilot.advisories import (
    resolve_speed_limit,
    relevant_matrix_speed,
    advised_speed_limit,
    nearest_stop_signal,
    upcoming_speed_change,
)


def sign(kind="speedLimit", speed=100, lane=-1, distance=100.0):
    return NS(kind=kind, speedLimit=speed, laneIndex=lane, distance=distance)


def movement(phase="green", ttc=-1.0):
    return NS(phase=phase, timeToChange=ttc)


def intersection(iid=1, distance=50.0, movements=()):
    return NS(intersectionId=iid, distance=distance, movements=list(movements))


class TestSpeedLimit(unittest.TestCase):
    def test_resolve_valid(self):
        self.assertEqual(resolve_speed_limit(NS(valid=True, speedLimit=130)), 130)

    def test_resolve_invalid_or_unknown(self):
        self.assertIsNone(resolve_speed_limit(NS(valid=False, speedLimit=130)))
        self.assertIsNone(resolve_speed_limit(NS(valid=True, speedLimit=-1)))
        self.assertIsNone(resolve_speed_limit(None))

    def test_matrix_picks_nearest_applicable(self):
        signs = [
            sign(speed=100, lane=-1, distance=300.0),
            sign(speed=80, lane=1, distance=120.0),   # ego lane, nearer
            sign(speed=50, lane=2, distance=60.0),     # other lane, ignored
        ]
        self.assertEqual(relevant_matrix_speed(signs, ego_lane=1), 80)

    def test_matrix_ignores_non_speed_and_unknown(self):
        signs = [sign(kind="laneClosed", speed=-1, lane=-1),
                 sign(kind="speedLimit", speed=-1, lane=-1)]
        self.assertIsNone(relevant_matrix_speed(signs, ego_lane=0))

    def test_matrix_all_lane_applies(self):
        self.assertEqual(relevant_matrix_speed([sign(speed=90, lane=-1)], ego_lane=3), 90)

    def test_advised_prefers_live_matrix_over_resolved(self):
        sl = NS(valid=True, speedLimit=130)
        signs = [sign(speed=90, lane=-1, distance=100.0)]
        self.assertEqual(advised_speed_limit(sl, signs, ego_lane=0), 90)

    def test_advised_falls_back_to_resolved(self):
        sl = NS(valid=True, speedLimit=130)
        self.assertEqual(advised_speed_limit(sl, [], ego_lane=0), 130)

    def test_advised_none_when_nothing(self):
        self.assertIsNone(advised_speed_limit(NS(valid=False, speedLimit=0), [], 0))


class TestTrafficLights(unittest.TestCase):
    def test_nearest_stop_signal(self):
        inter = [
            intersection(iid=1, distance=200.0, movements=[movement("green")]),
            intersection(iid=2, distance=80.0, movements=[movement("red", ttc=6.0)]),
            intersection(iid=3, distance=150.0, movements=[movement("amber", ttc=2.0)]),
        ]
        out = nearest_stop_signal(inter)
        self.assertEqual(out["intersectionId"], 2)
        self.assertEqual(out["distance"], 80.0)
        self.assertEqual(out["timeToChange"], 6.0)

    def test_no_stop_signal_when_all_green(self):
        inter = [intersection(movements=[movement("green"), movement("green")])]
        self.assertIsNone(nearest_stop_signal(inter))

    def test_soonest_ttc_among_stop_movements(self):
        inter = [intersection(distance=40.0, movements=[
            movement("red", ttc=9.0), movement("red", ttc=3.0), movement("red", ttc=-1.0)])]
        self.assertEqual(nearest_stop_signal(inter)["timeToChange"], 3.0)

    def test_ttc_unknown_when_none_reported(self):
        inter = [intersection(movements=[movement("red", ttc=-1.0)])]
        self.assertEqual(nearest_stop_signal(inter)["timeToChange"], -1.0)


class TestMapNudge(unittest.TestCase):
    def test_nearest_speed_change(self):
        md = NS(valid=True, upcoming=[
            NS(kind="speedChange", speedLimit=50, distance=300.0),
            NS(kind="speedChange", speedLimit=70, distance=120.0),
            NS(kind="curve", speedLimit=-1, distance=40.0),
        ])
        self.assertEqual(upcoming_speed_change(md), {"speedLimit": 70, "distance": 120.0})

    def test_invalid_map_data(self):
        self.assertIsNone(upcoming_speed_change(NS(valid=False, upcoming=[])))
        self.assertIsNone(upcoming_speed_change(None))

    def test_no_speed_change_feature(self):
        md = NS(valid=True, upcoming=[NS(kind="curve", speedLimit=-1, distance=40.0)])
        self.assertIsNone(upcoming_speed_change(md))


if __name__ == "__main__":
    unittest.main()
