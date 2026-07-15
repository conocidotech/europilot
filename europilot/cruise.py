"""Approach cruise easing -- the ONE place Europilot influences control.

Pure decision logic: given what's ahead (from euMapAdvisory) and the ego speed,
decide the km/h to cap the ACC set speed at, or None (no easing). Two triggers,
each opt-in and each a gentle ease-off:
  - a speed camera -> ease toward the enforced limit as you approach it;
  - an average-speed section (trajectcontrole) -> HOLD the enforced limit for
    the whole section, since it is enforced on the average, not at a point;
  - a roundabout -> ease toward a comfortable roundabout speed;
  - a sharp bend (MTSC) -> ease toward a comfortable cornering speed.
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

import math

# Gentle target deceleration used only to decide WHEN to start easing. The MPC
# does the real braking and caps it harder (~1.2 m/s2), so this stays a taper.
COMFORT_DECEL_MS2 = 0.8
# Aim to reach the limit this far before the camera (arrive settled, not late).
START_MARGIN_M = 40.0
# Ignore a camera farther than this -- no point easing a kilometre out.
MAX_TRIGGER_M = 800.0
# Don't nag when essentially already at the limit.
SPEED_HYSTERESIS_KPH = 3.0
# Sensible speed to ARRIVE at a roundabout with (for a calm hand-over -- the car
# does not drive the roundabout). Sized to the roundabout: a big one is neared
# faster than a mini. Not the posted limit; capped to it in speed_limit.py.
ROUNDABOUT_MINI_KPH = 20            # mini-roundabout / unknown size
ROUNDABOUT_SMALL_KPH = 20           # smallest ringed roundabout
ROUNDABOUT_LARGE_KPH = 50           # large multi-lane roundabout
ROUNDABOUT_SMALL_RADIUS_M = 10.0    # at/below this radius -> SMALL
ROUNDABOUT_LARGE_RADIUS_M = 35.0    # at/above this radius -> LARGE

# Map Turn Speed Control (MTSC): comfortable cornering speed for a bend of radius
# R is v = sqrt(a_lat * R). a_lat is a gentle lateral accel; the MPC still clips
# the longitudinal taper, so this only sets the target speed, not the braking.
CURVE_LAT_ACCEL_MS2 = 1.8           # comfortable lateral acceleration through a bend
CURVE_MIN_KPH = 20                  # never advise crawling below this for a bend
CURVE_MAX_KPH = 120                 # above this a bend is gentle enough to ignore


def roundabout_approach_kph(kind: str, radius_m: int) -> int:
    """Sensible arrival speed for a roundabout, sized by its radius (km/h).

    A mini (or unknown radius) gets a fixed low speed; a ringed roundabout scales
    linearly from SMALL to LARGE between the radius anchors, rounded to 5 km/h.
    Purely a device-side policy, so it can be tuned without re-baking tiles.
    """
    if kind == "mini" or not radius_m or radius_m <= 0:
        return ROUNDABOUT_MINI_KPH
    if radius_m <= ROUNDABOUT_SMALL_RADIUS_M:
        v = ROUNDABOUT_SMALL_KPH
    elif radius_m >= ROUNDABOUT_LARGE_RADIUS_M:
        v = ROUNDABOUT_LARGE_KPH
    else:
        frac = (radius_m - ROUNDABOUT_SMALL_RADIUS_M) / (ROUNDABOUT_LARGE_RADIUS_M - ROUNDABOUT_SMALL_RADIUS_M)
        v = ROUNDABOUT_SMALL_KPH + (ROUNDABOUT_LARGE_KPH - ROUNDABOUT_SMALL_KPH) * frac
    return int(round(v / 5.0)) * 5


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


def section_hold_kph(section_limit: int | None) -> int | None:
    """km/h to cap cruise at while INSIDE an average-speed section (trajectcontrole).

    Unlike a fixed camera this is NOT distance-gated: the section is enforced on
    the average over its whole length, so once inside we hold the cap the entire
    way (the planner min()s it and the MPC still tapers the entry). Returns the
    enforced limit, or None when not in a section / limit unknown.
    """
    if not section_limit or section_limit <= 0:
        return None
    return int(section_limit)


def roundabout_target_kph(*, roundabout_distance_m: float | None, v_ego_kph: float,
                          comfort_kph: int = ROUNDABOUT_MINI_KPH) -> int | None:
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


def curve_speed_kph(radius_m: int) -> int:
    """Comfortable cornering speed for a bend of a given radius (km/h).

    v = sqrt(a_lat * R), clamped to a sane band and rounded to 5 km/h. 0 (no
    radius) means "no curve" -> no easing. Device-side policy, so a_lat can be
    tuned without re-baking tiles.
    """
    if not radius_m or radius_m <= 0:
        return 0
    v_kph = math.sqrt(CURVE_LAT_ACCEL_MS2 * radius_m) * 3.6
    v_kph = max(float(CURVE_MIN_KPH), min(float(CURVE_MAX_KPH), v_kph))
    return int(round(v_kph / 5.0)) * 5


def curve_target_kph(*, curve_distance_m: float | None, curve_radius_m: int,
                     v_ego_kph: float) -> int | None:
    """km/h to cap cruise at approaching a sharp bend, or None for no easing.

    Same shape and safety as roundabout_target_kph: only ever lowers, fires only
    for a bend AHEAD (the matcher's job) within a bounded distance and only if
    we're above the comfortable cornering speed, and starts at the last
    comfortable moment so the MPC's comfort-clipped taper does the actual slowing.
    """
    if (curve_distance_m is None or curve_distance_m < 0
            or curve_distance_m > MAX_TRIGGER_M):
        return None
    comfort = curve_speed_kph(curve_radius_m)
    if comfort <= 0 or v_ego_kph <= comfort + SPEED_HYSTERESIS_KPH:
        return None
    if curve_distance_m <= easing_distance_m(v_ego_kph, comfort):
        return comfort
    return None
