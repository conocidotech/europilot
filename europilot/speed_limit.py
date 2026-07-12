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

RATE_HZ = 5.0
SERVICE = "euSpeedLimit"

# RSA is intermittent (~1 Hz, only when a sign is in view); hold the last
# reading this long so it can take part in fusion between frames.
RSA_HOLD_S = 8.0

# Fusion priority, highest authority/currency first. A legally-binding live
# matrix sign beats a sign the camera physically read now, which beats live
# matrix *advice*, which beats the static map. Append new sources here.
PRIORITY = ("ndwMandatory", "rsaCamera", "ndwAdvisory", "osm")


def fuse_speed_limit(*, ndw_mandatory: int | None = None, rsa_camera: int | None = None,
                     ndw_advisory: int | None = None, osm: int | None = None) -> tuple[int | None, str]:
    """Pick one advisory limit (km/h) + its source from the candidates.

    Returns ``(limit, source)`` where source is a euSpeedLimit.Source name, or
    ``(None, "none")`` when no source has a usable value.
    """
    candidates = {
        "ndwMandatory": ndw_mandatory,
        "rsaCamera": rsa_camera,
        "ndwAdvisory": ndw_advisory,
        "osm": osm,
    }
    for source in PRIORITY:
        value = candidates[source]
        if value is not None and value > 0:
            return value, source
    return None, "none"


def main():
    import time

    from cereal import messaging
    from openpilot.common.realtime import Ratekeeper

    pm = messaging.PubMaster([SERVICE])
    sm = messaging.SubMaster(["can", "euNdwMatrixSigns"])
    rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)

    rsa_limit: int | None = None
    rsa_seen = 0.0

    while True:
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

        # OSM plugs in here once EUROPILOT-35 lands.
        limit, source = fuse_speed_limit(
            ndw_mandatory=mandatory_speed(signs),
            rsa_camera=rsa,
            ndw_advisory=advisory_speed(signs),
            osm=None,
        )

        m = messaging.new_message(SERVICE)
        dat = m.euSpeedLimit
        dat.fetchMonoTime = int(now * 1e9)
        dat.valid = limit is not None
        dat.speedLimit = limit if limit is not None else -1
        dat.source = source
        pm.send(SERVICE, m)

        rk.keep_time()


if __name__ == "__main__":
    main()
