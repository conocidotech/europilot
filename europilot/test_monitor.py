"""Tests for the Europilot gateway monitor formatting."""

import unittest

from europilot.monitor import format_summary


class TestFormatSummary(unittest.TestCase):
    def test_all_present(self):
        out = format_summary(
            100,
            {"intersectionId": 2, "distance": 80.0, "timeToChange": 6.0},
            {"speedLimit": 70, "distance": 120.0},
        )
        self.assertEqual(out, "limit=100 km/h | stop@80m(ttc 6s) | next 70km/h in 120m")

    def test_all_absent(self):
        self.assertEqual(format_summary(None, None, None), "limit=-- | stop=-- | next=--")

    def test_unknown_ttc(self):
        out = format_summary(None, {"distance": 40.0, "timeToChange": -1.0}, None)
        self.assertEqual(out, "limit=-- | stop@40m(ttc ?) | next=--")


if __name__ == "__main__":
    unittest.main()
