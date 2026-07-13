"""EUROPILOT-60: confidence-based relaxation of the vision attention timeouts.

Verifies the stretch is opt-in, bounded, fail-safe, and only lengthens the
vision decay (never shortens it, never touches the post-orange escalation).
"""

import math

from openpilot.selfdrive.monitoring.policy import (
    DriverMonitoring, DRIVER_MONITOR_SETTINGS, MonitoringPolicy, DT_DMON,
)


def _dm(enabled: bool) -> DriverMonitoring:
    dm = DriverMonitoring()
    dm._conf_relax_enabled = enabled
    return dm


def test_disabled_is_exactly_stock():
    dm = _dm(False)
    for c in (0.0, 0.7, 1.0, float("nan")):
        dm._set_confidence_relax(c)
        assert dm.vision_timeout_factor == 1.0


def test_low_confidence_no_relaxation():
    dm = _dm(True)
    s = dm.settings
    dm._set_confidence_relax(s._CONF_RELAX_LO)      # at the floor
    assert dm.vision_timeout_factor == 1.0
    dm._set_confidence_relax(0.0)
    assert dm.vision_timeout_factor == 1.0


def test_high_confidence_capped():
    dm = _dm(True)
    s = dm.settings
    dm._set_confidence_relax(1.0)
    assert dm.vision_timeout_factor == s._CONF_RELAX_MAX_FACTOR
    # never exceeds the cap even for out-of-range input
    dm._set_confidence_relax(5.0)
    assert dm.vision_timeout_factor == s._CONF_RELAX_MAX_FACTOR


def test_monotonic_between_bounds():
    dm = _dm(True)
    s = dm.settings
    mid = (s._CONF_RELAX_LO + s._CONF_RELAX_HI) / 2
    dm._set_confidence_relax(mid)
    assert 1.0 < dm.vision_timeout_factor < s._CONF_RELAX_MAX_FACTOR


def test_nan_fails_safe():
    dm = _dm(True)
    dm._set_confidence_relax(float("nan"))
    assert dm.vision_timeout_factor == 1.0


def test_relaxation_slows_vision_decay():
    # higher factor -> smaller step_change -> awareness decays slower (more time)
    dm = _dm(True)
    dm.vision_timeout_factor = 1.0
    dm._set_policy(MonitoringPolicy.vision)
    stock_step = dm.step_change

    dm.vision_timeout_factor = dm.settings._CONF_RELAX_MAX_FACTOR
    dm._set_policy(MonitoringPolicy.vision)
    relaxed_step = dm.step_change

    assert relaxed_step < stock_step
    assert math.isclose(relaxed_step * dm.settings._CONF_RELAX_MAX_FACTOR, stock_step, rel_tol=1e-6)


def test_thresholds_unchanged_by_relaxation():
    # only the decay rate scales; the alert thresholds are ratios and stay put
    dm = _dm(True)
    dm.vision_timeout_factor = 1.0
    dm._set_policy(MonitoringPolicy.vision)
    t1, t2 = dm.threshold_alert_1, dm.threshold_alert_2
    dm.vision_timeout_factor = dm.settings._CONF_RELAX_MAX_FACTOR
    dm._set_policy(MonitoringPolicy.vision)
    assert (dm.threshold_alert_1, dm.threshold_alert_2) == (t1, t2)


def test_model_confidence_from_probs():
    class FakePred:
        brakeDisengageProbs = [0.0, 0.1]
        steerOverrideProbs = [0.0]

    class FakeModel:
        class meta:
            disengagePredictions = FakePred()

    dm = _dm(True)
    sm = {"modelV2": FakeModel()}
    # (1-max(0,0.1))*(1-max(0)) = 0.9
    assert math.isclose(dm._model_confidence(sm), 0.9, rel_tol=1e-6)


def test_model_confidence_missing_is_zero():
    dm = _dm(True)
    assert dm._model_confidence({}) == 0.0


def test_settings_bounds_sane():
    s = DRIVER_MONITOR_SETTINGS()
    assert 0.0 < s._CONF_RELAX_LO < s._CONF_RELAX_HI <= 1.0
    assert s._CONF_RELAX_MAX_FACTOR > 1.0
    assert DT_DMON > 0
