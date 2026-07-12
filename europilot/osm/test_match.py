"""Tests for the pure pose->road matcher."""

from europilot.osm.match import match
from europilot.osm.types import Camera, Road


def _road(rid, points, **kw):
    base = dict(road_class="residential", maxspeed=30, oneway=False, lanes=None,
               name="Straat", in_residential=True, calming_per_km=0.0,
               crossing_per_km=0.0, cycleway_left="unknown", cycleway_right="unknown",
               cyclestreet=False, residential_score=0.8, comfort_speed=30)
    base.update(kw)
    return Road(id=rid, points=points, **base)


# A W->E road at lat 52.010 and a N->S cross street at lon 5.010.
EW = _road(1, [(52.010, 5.000), (52.010, 5.020)], name="Hoofdstraat")
NS = _road(2, [(52.000, 5.010), (52.020, 5.010)], name="Kruisstraat")


class TestMatch:
    def test_on_the_road(self):
        adv = match((52.010, 5.010), heading=90.0, roads=[EW, NS])
        assert adv.valid and adv.road_id == 1
        assert adv.speed_limit == 30 and adv.comfort_speed == 30
        assert adv.name == "Hoofdstraat"
        assert adv.distance_m is not None and adv.distance_m < 5

    def test_heading_selects_between_crossing_roads(self):
        # Same point, but heading north -> the N/S road wins.
        adv = match((52.010, 5.010), heading=0.0, roads=[EW, NS])
        assert adv.road_id == 2

    def test_too_far_no_match(self):
        adv = match((52.050, 5.010), heading=90.0, roads=[EW, NS])
        assert adv.valid is False and adv.road_id is None

    def test_wrong_heading_rejected(self):
        # On the E/W road but heading across it: E/W fails the heading test, and
        # the N/S road is ~700 m away, so nothing matches.
        adv = match((52.010, 5.0005), heading=0.0, roads=[EW])
        assert adv.valid is False

    def test_undirected_heading_accepts_reverse(self):
        # Driving west (270) on an eastbound-drawn road still matches.
        adv = match((52.010, 5.010), heading=270.0, roads=[EW])
        assert adv.valid and adv.road_id == 1

    def test_no_heading_matches_nearest(self):
        adv = match((52.010, 5.010), heading=None, roads=[EW, NS])
        assert adv.valid   # both cross here; nearest wins, no heading filter

    def test_empty_roads(self):
        assert match((52.0, 5.0), 90.0, []).valid is False


# EW road with a fixed camera at lon 5.015 (100 km/h).
_CAM = Camera(lat=52.010, lon=5.015, maxspeed=100, kind="fixed")
EW_CAM = _road(3, [(52.010, 5.000), (52.010, 5.020)], cameras=(_CAM,))


class TestCameraAhead:
    def test_camera_ahead_in_travel_direction(self):
        adv = match((52.010, 5.005), heading=90.0, roads=[EW_CAM])
        assert adv.valid and adv.camera_kind == "fixed" and adv.camera_limit == 100
        assert 600 < adv.camera_distance_m < 760   # ~685 m east along the road

    def test_camera_behind_is_not_reported(self):
        adv = match((52.010, 5.018), heading=90.0, roads=[EW_CAM])
        assert adv.valid and adv.camera_distance_m is None and adv.camera_kind == ""

    def test_reverse_direction_flips_what_is_ahead(self):
        # Same pose, now driving west: the camera is ahead again.
        adv = match((52.010, 5.018), heading=270.0, roads=[EW_CAM])
        assert adv.camera_distance_m is not None

    def test_no_heading_is_fail_safe(self):
        # Without a heading we cannot tell ahead from behind -> report nothing.
        adv = match((52.010, 5.005), heading=None, roads=[EW_CAM])
        assert adv.valid and adv.camera_distance_m is None

    def test_only_the_matched_road_cameras_count(self):
        # The matched road (EW, no camera) is what we read; a camera on the other
        # road never bleeds in -- the device half of the off-ramp guard.
        ns_cam = _road(2, [(52.000, 5.010), (52.020, 5.010)],
                       cameras=(Camera(52.015, 5.010, 100, "fixed"),))
        adv = match((52.010, 5.005), heading=90.0, roads=[EW, ns_cam])
        assert adv.road_id == 1 and adv.camera_distance_m is None
