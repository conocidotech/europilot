#!/usr/bin/env python3
"""europilot_osmd -- publish the OSM road advisory for the current pose.

Subscribes to the ego GPS, keeps the signed tiles for the current region synced
(europilot.osm.client, cold path), matches the pose to a road (hot path), and
publishes euMapAdvisory. speedLimit there feeds euSpeedLimit fusion's osm source.

Advisory only, and fails closed: no GPS or no fresh tile yields valid=False, and
the fusion simply falls back to its other sources. Merge-safe: new file, no
change to upstream openpilot logic.
"""

from europilot.gps import GpsHealth, read_pose
from europilot.osm.client import OsmTileClient
from europilot.osm.types import Advisory

RATE_HZ = 4.0
SERVICE = "euMapAdvisory"

_PRESENCE = ("unknown", "absent", "present")
_SPEED_MAX_KMH = 255


def _clamp_kmh(v) -> int:
    """A km/h value for the wire, or the -1 'unknown' sentinel.

    Bounds anything out of a plausible range to the sentinel so a stray value
    can neither crash the daemon at capnp assignment nor assert a bogus limit.
    """
    if isinstance(v, int) and not isinstance(v, bool) and 0 < v <= _SPEED_MAX_KMH:
        return v
    return -1


def advisory_to_fields(a: Advisory) -> dict:
    """Advisory -> euMapAdvisory scalar fields (None -> -1, presence -> enum name).

    Pure, so the wire mapping is tested without building cereal. fetchMonoTime is
    stamped by the daemon loop, not here.
    """
    return {
        "valid": a.valid,
        "roadClass": a.road_class or "",
        "name": a.name or "",
        "speedLimit": _clamp_kmh(a.speed_limit),
        "comfortSpeed": _clamp_kmh(a.comfort_speed),
        "inResidential": a.in_residential,
        "cyclewayLeft": a.cycleway_left if a.cycleway_left in _PRESENCE else "unknown",
        "cyclewayRight": a.cycleway_right if a.cycleway_right in _PRESENCE else "unknown",
        "cyclestreet": a.cyclestreet,
        "distance": a.distance_m if a.distance_m is not None else -1.0,
        "cameraDistance": a.camera_distance_m if a.camera_distance_m is not None else -1.0,
        "cameraLimit": _clamp_kmh(a.camera_limit),
        "cameraKind": a.camera_kind or "",
        "sectionLimit": _clamp_kmh(a.section_limit),
        "roundaboutDistance": a.roundabout_distance_m if a.roundabout_distance_m is not None else -1.0,
        "roundaboutKind": a.roundabout_kind or "",
        "roundaboutRadiusM": a.roundabout_radius_m or 0,
        "curveDistance": a.curve_distance_m if a.curve_distance_m is not None else -1.0,
        "curveRadiusM": a.curve_radius_m or 0,
        "signDistance": a.sign_distance_m if a.sign_distance_m is not None else -1.0,
        "signKind": a.sign_kind or "",
    }


def main():
    import time

    from cereal import messaging
    from openpilot.common.realtime import Ratekeeper
    from openpilot.common.swaglog import cloudlog

    from europilot.loopwatch import LoopWatch

    client = OsmTileClient()
    pm = messaging.PubMaster([SERVICE])
    sm = messaging.SubMaster(["gpsLocation", "gpsLocationExternal"])
    rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)
    watch = LoopWatch("europilot_osmd", budget_s=3.0 / RATE_HZ)
    gps_health = GpsHealth()

    # Advisory-only: a crash here would soft-disable openpilot, so guard the
    # whole body and degrade to a fail-closed heartbeat instead.
    while True:
        watch.tick()
        try:
            sm.update(0)

            advisory = Advisory.none()
            pose = read_pose(sm)   # best fixed pose across both GNSS sources, or None
            note = gps_health.transition(pose)
            if note:
                (cloudlog.info if pose else cloudlog.warning)(note)
            if pose is not None:
                client.poll(pose.lat, pose.lon)   # cold path, non-blocking
                advisory = client.match(pose.lat, pose.lon, pose.bearing)

            m = messaging.new_message(SERVICE)
            dat = m.euMapAdvisory
            dat.fetchMonoTime = int(time.monotonic() * 1e9)
            for field, value in advisory_to_fields(advisory).items():
                setattr(dat, field, value)
            pm.send(SERVICE, m)
        except Exception:
            cloudlog.exception("europilot_osmd iteration failed; publishing fail-closed")
            try:
                m = messaging.new_message(SERVICE)
                for field, value in advisory_to_fields(Advisory.none()).items():
                    setattr(m.euMapAdvisory, field, value)
                pm.send(SERVICE, m)
            except Exception:
                cloudlog.exception("europilot_osmd could not publish fail-closed heartbeat")

        rk.keep_time()


if __name__ == "__main__":
    main()
