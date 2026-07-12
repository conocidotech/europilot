#!/usr/bin/env python3
"""europilot_osmd -- publish the OSM road advisory for the current pose.

Subscribes to the ego GPS, keeps the signed tiles for the current region synced
(europilot.osm.client, cold path), matches the pose to a road (hot path), and
publishes euMapAdvisory. speedLimit there feeds euSpeedLimit fusion's osm source.

Advisory only, and fails closed: no GPS or no fresh tile yields valid=False, and
the fusion simply falls back to its other sources. Merge-safe: new file, no
change to upstream openpilot logic.
"""

from europilot.osm.client import OsmTileClient
from europilot.osm.types import Advisory

RATE_HZ = 4.0
SERVICE = "euMapAdvisory"

_PRESENCE = ("unknown", "absent", "present")


def advisory_to_fields(a: Advisory) -> dict:
    """Advisory -> euMapAdvisory scalar fields (None -> -1, presence -> enum name).

    Pure, so the wire mapping is tested without building cereal. fetchMonoTime is
    stamped by the daemon loop, not here.
    """
    return {
        "valid": a.valid,
        "roadClass": a.road_class or "",
        "name": a.name or "",
        "speedLimit": a.speed_limit if a.speed_limit is not None else -1,
        "comfortSpeed": a.comfort_speed if a.comfort_speed is not None else -1,
        "inResidential": a.in_residential,
        "cyclewayLeft": a.cycleway_left if a.cycleway_left in _PRESENCE else "unknown",
        "cyclewayRight": a.cycleway_right if a.cycleway_right in _PRESENCE else "unknown",
        "cyclestreet": a.cyclestreet,
        "distance": a.distance_m if a.distance_m is not None else -1.0,
    }


def main():
    import time

    from cereal import messaging
    from openpilot.common.realtime import Ratekeeper

    client = OsmTileClient()
    pm = messaging.PubMaster([SERVICE])
    sm = messaging.SubMaster(["gpsLocation"])
    rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)

    while True:
        sm.update(0)

        advisory = Advisory.none()
        if sm.valid["gpsLocation"] and sm.recv_frame["gpsLocation"] > 0:
            gps = sm["gpsLocation"]
            client.poll(gps.latitude, gps.longitude)   # cold path, non-blocking
            advisory = client.match(gps.latitude, gps.longitude, gps.bearingDeg)

        m = messaging.new_message(SERVICE)
        dat = m.euMapAdvisory
        dat.fetchMonoTime = int(time.monotonic() * 1e9)
        for field, value in advisory_to_fields(advisory).items():
            setattr(dat, field, value)
        pm.send(SERVICE, m)

        rk.keep_time()


if __name__ == "__main__":
    main()
