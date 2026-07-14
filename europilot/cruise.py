"""Approach cruise easing -- the ONE place Europilot influences control.

Pure decision logic: given what's ahead (from euMapAdvisory) and the ego speed,
decide the km/h to cap the ACC set speed at, or None (no easing). Two triggers,
each opt-in and each a gentle ease-off:
  - a speed camera / trajectcontrole -> ease toward the enforced limit;
  - a roundabout -> ease toward a comfortable roundabout speed.
europilot/speed_limit.py takes the lower of the two and publishes it as
euSpeedLimit.cruiseTarget; the longitudinal planner caps cruise to it with a
min() when the relevant opt-in toggle is on and openpilot is engaged.

Design (kept safe and boring):
  - Only ever LOWERS toward a real posted limit -- a min(), never a speed-up.
  - Fires only for a camera AHEAD on the matched road (the matcher's job) within
    a bounded distance, and only if we are actually above the enforced limit.
  - Starts easing at the last comfortable moment, so we arrive at the limit near
    the camera rather than crawling for a kilometre. The actual taper is done by
    the MPC, which clips any target to a comfort deceleration -- so this can only
    ever produce a gentle ease-off, never a hard brake.
"""

# Gentle target deceleration used only to decide WHEN to start easing. The MPC
# does the real braking and caps it harder (~1.2 m/s2), so this stays a taper.
COMFORT_DECEL_MS2 = 0.8
# Aim to reach the limit this far before the camera (arrive settled, not late).
START_MARGIN_M = 40.0
# Ignore a camera farther than this -- no point easing a kilometre out.
MAX_TRIGGER_M = 800.0
# Don't nag when essentially already at the limit.
SPEED_HYSTERESIS_KPH = 3.0
# Comfortable speed to arrive at a roundabout with. A roundabout carries no
# posted limit, so this is the target the ease-off aims for (tunable).
ROUNDABOUT_COMFORT_KPH = 30


def easing_distance_m(v_ego_kph: float, limit_kph: int) -> float:
    """Distance needed to shed v_ego -> limit at a comfortable decel, plus margin."""
    v = v_ego_kph / 3.6
    vl = limit_kph / 3.6
    return max(0.0, (v * v - vl * vl) / (2.0 * COMFORT_DECEL_MS2)) + START_MARGIN_M


def cruise_target_kph(*, camera_distance_m: float | None, camera_limit: int | None,
                      fused_limit_kph: int | None, v_ego_kph: float) -> int | None:
    """km/h to cap cruise at approaching a camera, or None for no easing.

    camera_limit is preferred; if the camera carries no limit we fall back to the
    current fused limit as the enforced speed there.
    """
    if camera_distance_m is None or camera_distance_m < 0 or camera_distance_m > MAX_TRIGGER_M:
        return None
    limit = camera_limit if (camera_limit and camera_limit > 0) else fused_limit_kph
    if not limit or limit <= 0:
        return None
    if v_ego_kph <= limit + SPEED_HYSTERESIS_KPH:
        return None   # already at/below the enforced limit -- nothing to ease
    if camera_distance_m <= easing_distance_m(v_ego_kph, limit):
        return limit
    return None


def roundabout_target_kph(*, roundabout_distance_m: float | None, v_ego_kph: float,
                          comfort_kph: int = ROUNDABOUT_COMFORT_KPH) -> int | None:
    """km/h to cap cruise at approaching a roundabout, or None for no easing.

    Same shape and safety as cruise_target_kph: only ever lowers, fires only for
    a roundabout AHEAD (the matcher's job) within a bounded distance and only if
    we're above the comfortable roundabout speed, and starts at the last
    comfortable moment so the MPC's comfort-clipped taper does the actual slowing.
    """
    if (roundabout_distance_m is None or roundabout_distance_m < 0
            or roundabout_distance_m > MAX_TRIGGER_M):
        return None
    if comfort_kph <= 0 or v_ego_kph <= comfort_kph + SPEED_HYSTERESIS_KPH:
        return None
    if roundabout_distance_m <= easing_distance_m(v_ego_kph, comfort_kph):
        return comfort_kph
    return None
