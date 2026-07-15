"""Pick a usable vehicle pose from the device's GPS receivers.

The comma four has two GNSS sources -- the Quectel modem (gpsLocation) and the
external ublox (gpsLocationExternal). Either can hold the fix while the other has
none (a real drive saw the ublox stuck with no fix and a bogus 2015 epoch), so
the map matchers must never bind to one source, and must never match on a NO-FIX
position -- its lat/lon is stale/garbage and would match the wrong road. This
centralises that: try both sources, require a real fix, reject a fix too crude
for road matching, and return None so the caller fails closed.

Pure (no cereal, no openpilot import) so it is trivially testable; the daemons
feed it their live SubMaster.
"""

from dataclasses import dataclass

# Preference order: the external ublox is usually the better receiver, so use it
# when it has a fix; otherwise fall back to the Quectel modem.
GPS_SERVICES = ("gpsLocationExternal", "gpsLocation")

# Reject a "fix" cruder than this -- at road-matching scale a pose 100 m off
# picks the wrong road. 0 / absent accuracy means "not reported" -> not rejected.
MAX_HACC_M = 50.0


@dataclass(frozen=True)
class Pose:
    lat: float
    lon: float
    bearing: float
    source: str
    hacc: float


def read_pose(sm) -> Pose | None:
    """Best available fixed pose across the GPS sources, or None (fail closed)."""
    for svc in GPS_SERVICES:
        try:
            if not sm.valid[svc] or sm.recv_frame[svc] <= 0:
                continue
        except (KeyError, TypeError):
            continue   # this caller didn't subscribe the source
        g = sm[svc]
        if not getattr(g, "hasFix", False):
            continue
        hacc = float(getattr(g, "horizontalAccuracy", 0.0) or 0.0)
        if hacc and hacc > MAX_HACC_M:
            continue   # a fix too crude to trust for road matching
        return Pose(lat=g.latitude, lon=g.longitude, bearing=g.bearingDeg,
                    source=svc, hacc=hacc)
    return None


class GpsHealth:
    """Detects GPS fix/source transitions, returning a log line on change.

    Pure: the daemon decides how to log it. Emits on fix acquired / lost / source
    switch, so a no-fix drive (or a receiver flapping) is visible in the logs
    without spamming a line every cycle.
    """

    def __init__(self) -> None:
        self._state: tuple[bool, str | None] | None = None

    def transition(self, pose: Pose | None) -> str | None:
        state = (pose is not None, pose.source if pose else None)
        if state == self._state:
            return None
        self._state = state
        if pose is None:
            return "GPS: no usable fix from any source"
        return f"GPS: fix from {pose.source} (hacc={pose.hacc:.1f}m)"
