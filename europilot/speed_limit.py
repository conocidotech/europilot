#!/usr/bin/env python3
"""europilot_speedlimitd -- one fused advisory speed limit from every source.

Reads all the speed-limit sources the device has and publishes a single
resolved euSpeedLimit, tagged with the source it came from:

  - NDW matrix signs (euNdwMatrixSigns): mandatory (red-ringed, binding) and
    advisory (no red ring) speeds
  - Road Sign Assist camera (raw CAN, decoded via europilot.rsa)
  - OSM static map limit -- not wired yet (EUROPILOT-35); it plugs into fuse()
    and main() with no other change

The fusion policy is a single pure function so it stays explicit and easy to
tune. Everything published is ADVISORY -- surfaced to the driver, never used to
hard-limit control.

Merge-safe: this file is new and does not modify upstream openpilot logic.
"""

from europilot.rsa import speed_limit_from_can, RSA1_ADDR
from europilot.advisories import mandatory_speed, advisory_speed
from europilot.cruise import cruise_target_kph, roundabout_target_kph

RATE_HZ = 5.0
SERVICE = "euSpeedLimit"

# RSA is intermittent (~1 Hz, only when a sign is in view); hold the last
# reading this long so it can take part in fusion between frames.
RSA_HOLD_S = 8.0

# Fusion priority, highest authority/currency first. A legally-binding live
# matrix sign beats a sign the camera physically read now, which beats live
# matrix *advice*, which beats the static map, which beats the time-of-day
# default. Append new sources here.
PRIORITY = ("ndwMandatory", "rsaCamera", "ndwAdvisory", "osm", "timeOfDay")

# NL motorways carry a 100 km/h maximum from 06:00 to 19:00 (nationwide since
# 2020). Outside that window the limit is road-specific (100/120/130) and posted,
# so we don't guess it. The daytime 100 is the only safe inference, and only a
# weak fallback: any posted source outranks it.
DAY_START_H = 6
DAY_END_H = 19
MOTORWAY_DAY_KMH = 100


def motorway_day_limit(road_class: str, hour: int | None) -> int | None:
    """The implicit NL daytime motorway limit (km/h), or None when it doesn't apply.

    Fills the gap the server leaves open: an implicit 'NL:motorway' limit is
    time-dependent, so the tile carries no number. Fires only on a motorway,
    only 06:00-19:00, and only as fusion's lowest-priority fallback.
    """
    if road_class != "motorway" or hour is None:
        return None
    return MOTORWAY_DAY_KMH if DAY_START_H <= hour < DAY_END_H else None


def fuse_speed_limit(*, ndw_mandatory: int | None = None, rsa_camera: int | None = None,
                     ndw_advisory: int | None = None, osm: int | None = None,
                     time_of_day: int | None = None) -> tuple[int | None, str]:
    """Pick one advisory limit (km/h) + its source from the candidates.

    Returns ``(limit, source)`` where source is a euSpeedLimit.Source name, or
    ``(None, "none")`` when no source has a usable value.
    """
    candidates = {
        "ndwMandatory": ndw_mandatory,
        "rsaCamera": rsa_camera,
        "ndwAdvisory": ndw_advisory,
        "osm": osm,
        "timeOfDay": time_of_day,
    }
    for source in PRIORITY:
        value = candidates[source]
        if value is not None and value > 0:
            return value, source
    return None, "none"


def _nl_hour() -> int | None:
    """Current hour (0-23) in NL local time, or None if the clock/tz is unavailable."""
    try:
        import datetime
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Europe/Amsterdam")).hour
    except Exception:
        return None


def main():
    import time

    from cereal import messaging
    from openpilot.common.realtime import Ratekeeper
    from openpilot.common.swaglog import cloudlog

    from europilot.loopwatch import LoopWatch

    pm = messaging.PubMaster([SERVICE])
    sm = messaging.SubMaster(["can", "euNdwMatrixSigns", "euMapAdvisory", "carState"])
    rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)
    watch = LoopWatch("europilot_speedlimitd", budget_s=3.0 / RATE_HZ)

    rsa_limit: int | None = None
    rsa_seen = 0.0

    # Advisory-only: never let a fault take the process down (that soft-disables
    # openpilot). Guard the loop body and publish a fail-closed heartbeat.
    while True:
        watch.tick()
        try:
            sm.update(0)
            now = time.monotonic()

            # RSA from raw CAN, held briefly since it's intermittent.
            if sm.updated["can"]:
                for msg in sm["can"]:
                    if msg.address == RSA1_ADDR:
                        lim = speed_limit_from_can(bytes(msg.dat))
                        if lim is not None:
                            rsa_limit, rsa_seen = lim, now
            rsa = rsa_limit if (rsa_limit is not None and now - rsa_seen <= RSA_HOLD_S) else None

            # NDW from the matched matrix-sign message.
            signs = None
            if sm.valid["euNdwMatrixSigns"] and sm.recv_frame["euNdwMatrixSigns"] > 0:
                signs = sm["euNdwMatrixSigns"]

            # OSM posted limit + road class + next camera from the map advisory.
            osm = None
            road_class = ""
            cam_distance = None
            cam_limit = None
            rb_distance = None
            if sm.valid["euMapAdvisory"] and sm.recv_frame["euMapAdvisory"] > 0:
                adv = sm["euMapAdvisory"]
                if adv.valid:
                    road_class = adv.roadClass
                    if adv.speedLimit > 0:
                        osm = adv.speedLimit
                    if adv.cameraDistance >= 0:
                        cam_distance = adv.cameraDistance
                        cam_limit = adv.cameraLimit if adv.cameraLimit > 0 else None
                    if adv.roundaboutDistance >= 0:
                        rb_distance = adv.roundaboutDistance

            limit, source = fuse_speed_limit(
                ndw_mandatory=mandatory_speed(signs),
                rsa_camera=rsa,
                ndw_advisory=advisory_speed(signs),
                osm=osm,
                time_of_day=motorway_day_limit(road_class, _nl_hour()),
            )

            # The control-affecting output: ease cruise approaching a camera
            # (toward its limit) or a roundabout (toward a comfortable speed).
            # Published as two independent targets so each opt-in toggle in the
            # planner gates its own. -1 means no easing from that source this cycle.
            v_ego_kph = sm["carState"].vEgo * 3.6 if sm.valid["carState"] else 0.0
            cam_target = cruise_target_kph(
                camera_distance_m=cam_distance, camera_limit=cam_limit,
                fused_limit_kph=limit, v_ego_kph=v_ego_kph,
            )
            rb_target = roundabout_target_kph(
                roundabout_distance_m=rb_distance, v_ego_kph=v_ego_kph,
            )

            m = messaging.new_message(SERVICE)
            dat = m.euSpeedLimit
            dat.fetchMonoTime = int(now * 1e9)
            dat.valid = limit is not None
            dat.speedLimit = limit if limit is not None else -1
            dat.source = source
            dat.cruiseTarget = cam_target if cam_target is not None else -1
            dat.roundaboutTarget = rb_target if rb_target is not None else -1
            pm.send(SERVICE, m)
        except Exception:
            cloudlog.exception("europilot_speedlimitd iteration failed; publishing fail-closed")
            try:
                m = messaging.new_message(SERVICE)
                m.euSpeedLimit.valid = False
                m.euSpeedLimit.speedLimit = -1
                m.euSpeedLimit.source = "none"
                m.euSpeedLimit.cruiseTarget = -1
                m.euSpeedLimit.roundaboutTarget = -1
                pm.send(SERVICE, m)
            except Exception:
                cloudlog.exception("europilot_speedlimitd could not publish fail-closed heartbeat")

        rk.keep_time()


if __name__ == "__main__":
    main()
