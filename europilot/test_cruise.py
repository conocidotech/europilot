"""Tests for the camera-approach cruise-easing decision (the one control output)."""

from europilot.cruise import MAX_TRIGGER_M, cruise_target_kph, easing_distance_m


class TestEasingDistance:
    def test_grows_with_speed_delta(self):
        assert easing_distance_m(120, 100) > easing_distance_m(110, 100)

    def test_has_a_margin_even_at_the_limit(self):
        assert easing_distance_m(100, 100) >= 40.0   # START_MARGIN_M


class TestCruiseTarget:
    def test_eases_to_the_camera_limit_when_close_and_above(self):
        # 120 -> 100 camera: easing begins ~250 m out.
        assert cruise_target_kph(camera_distance_m=200, camera_limit=100,
                                 fused_limit_kph=120, v_ego_kph=120) == 100

    def test_not_yet_when_still_far(self):
        assert cruise_target_kph(camera_distance_m=400, camera_limit=100,
                                 fused_limit_kph=120, v_ego_kph=120) is None

    def test_ignores_a_camera_beyond_the_trigger_range(self):
        assert cruise_target_kph(camera_distance_m=MAX_TRIGGER_M + 1, camera_limit=50,
                                 fused_limit_kph=100, v_ego_kph=100) is None

    def test_no_camera_no_easing(self):
        assert cruise_target_kph(camera_distance_m=None, camera_limit=100,
                                 fused_limit_kph=120, v_ego_kph=120) is None
        assert cruise_target_kph(camera_distance_m=-1.0, camera_limit=100,
                                 fused_limit_kph=120, v_ego_kph=120) is None

    def test_already_at_or_below_limit_no_easing(self):
        assert cruise_target_kph(camera_distance_m=100, camera_limit=100,
                                 fused_limit_kph=100, v_ego_kph=100) is None

    def test_falls_back_to_the_fused_limit_when_camera_has_none(self):
        assert cruise_target_kph(camera_distance_m=100, camera_limit=None,
                                 fused_limit_kph=80, v_ego_kph=100) == 80

    def test_no_limit_anywhere_no_easing(self):
        assert cruise_target_kph(camera_distance_m=100, camera_limit=None,
                                 fused_limit_kph=None, v_ego_kph=100) is None

    def test_only_ever_lowers(self):
        # target is the enforced limit, always <= current speed when it fires
        target = cruise_target_kph(camera_distance_m=150, camera_limit=80,
                                   fused_limit_kph=100, v_ego_kph=110)
        assert target == 80 and target < 110
