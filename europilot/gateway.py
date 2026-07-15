#!/usr/bin/env python3
"""europilotd -- publishes matched NDW matrix-sign state onto the msgq bus.

The device talks only to app.europilot.eu, which serves *region snapshots* of
matrix-sign geometry and live aspects, one tile at a time. Matching a snapshot
to the car is done here, on the device: which gantry governs us and how far the
next one is depend on the precise pose, which changes far faster than the
gateway poll -- and the gateway cannot know our lane at all.

So this daemon is a thin integrator:

    MatrixSignClient.poll()   cold path: refresh the tile snapshot in the
                              background when we move tiles or it ages out
    MatrixSignClient.match()  hot path: pure geometry against the in-memory
                              snapshot, never blocks, never hits the network
    -> euNdwMatrixSigns       the matched result, published for consumers

Fails closed: with no fresh snapshot, match() returns None and we publish
valid=False so nothing downstream acts on stale data.

Everything published is ADVISORY. The mandatory (red-ringed, legally binding)
and advisory speeds are kept apart all the way to the consumer -- see
europilot/ndw/types.py.

Merge-safe: this file is new and does not modify upstream openpilot logic.
"""

from europilot.ndw.client import MatrixSignClient
from europilot.ndw.types import Gantry

RATE_HZ = 4.0
SERVICE = "euNdwMatrixSigns"


def int8_lanes(lanes) -> list[int]:
    """Keep only lane indices a capnp Int8 closedLanes field can hold.

    Lane values reach here from the (unsigned) gateway feed; a stray out-of-range
    index would raise on assignment and take the daemon down, so drop it instead.
    """
    return [n for n in lanes if isinstance(n, int) and not isinstance(n, bool) and -128 <= n <= 127]


def fill_gantry(dat, gantry: Gantry | None) -> None:
    """Write a matched Gantry (or a miss) into a capnp Gantry builder."""
    if gantry is None:
        dat.valid = False
        return

    dat.valid = True
    dat.distance = gantry.distance_m
    dat.mandatorySpeed = gantry.mandatory_speed if gantry.mandatory_speed is not None else -1
    dat.advisorySpeed = gantry.advisory_speed if gantry.advisory_speed is not None else -1
    dat.targetSpeed = gantry.target_speed if gantry.target_speed is not None else -1
    dat.flashing = gantry.flashing
    dat.road = gantry.road
    dat.carriageway = gantry.carriageway

    closed = int8_lanes(gantry.closed_lanes)
    lanes = dat.init("closedLanes", len(closed))
    for i, lane in enumerate(closed):
        lanes[i] = lane


def main():
    # Imported here so the helpers above stay importable without a compiled
    # cereal (unit tests, dev machines).
    import time

    from cereal import messaging
    from openpilot.common.realtime import Ratekeeper
    from openpilot.common.swaglog import cloudlog

    from europilot.loopwatch import LoopWatch

    pm = messaging.PubMaster([SERVICE])
    sm = messaging.SubMaster(["gpsLocation"])
    client = MatrixSignClient()
    rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)
    watch = LoopWatch("europilotd", budget_s=3.0 / RATE_HZ)

    # An advisory daemon must never take the process down: a crash here is a
    # monitored-process fault that soft-disables openpilot. So the whole loop
    # body is guarded -- any fault publishes a fail-closed heartbeat and we go
    # on, degrading to "no advisory" instead of handing back control.
    while True:
        watch.tick()
        try:
            sm.update(0)

            match = None
            age = None
            if sm.valid["gpsLocation"] and sm.recv_frame["gpsLocation"] > 0:
                gps = sm["gpsLocation"]
                client.poll(gps.latitude, gps.longitude)  # cold path, non-blocking
                match = client.match(gps.latitude, gps.longitude, gps.bearingDeg)
                age = client.age_s()

            msg = messaging.new_message(SERVICE)
            dat = msg.euNdwMatrixSigns
            dat.fetchMonoTime = int(time.monotonic() * 1e9)
            dat.valid = match is not None
            dat.snapshotAge = -1.0 if age is None else age
            fill_gantry(dat.governing, match.governing if match else None)
            fill_gantry(dat.upcoming, match.upcoming if match else None)
            pm.send(SERVICE, msg)
        except Exception:
            cloudlog.exception("europilotd iteration failed; publishing fail-closed")
            try:
                bad = messaging.new_message(SERVICE)
                bad.euNdwMatrixSigns.valid = False
                fill_gantry(bad.euNdwMatrixSigns.governing, None)
                fill_gantry(bad.euNdwMatrixSigns.upcoming, None)
                pm.send(SERVICE, bad)
            except Exception:
                cloudlog.exception("europilotd could not publish fail-closed heartbeat")

        rk.keep_time()


if __name__ == "__main__":
    main()
