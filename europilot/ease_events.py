"""Turn the per-cycle advisory-easing signal into discrete ease EVENTS.

The observability layer publishes, every cycle, the ease that is currently
binding cruise (euSpeedLimit.easing*). For a per-ride history we don't want that
raw stream -- we want the moments something *happened*: an ease starting, or the
binding reason changing. This tracker does that reduction.

Pure and testable: no I/O and no clock of its own (the caller passes the wall
time). The heartbeat daemon feeds it the live signal + pose and persists /
uploads whatever events come out.
"""


class EaseEventTracker:
    def __init__(self, min_gap_s: float = 5.0):
        # Debounce: never emit two events closer than this, so a flapping signal
        # (e.g. a camera target blinking on the edge of range) yields one event.
        self._min_gap_s = min_gap_s
        self._reason = "none"
        self._last_t: float | None = None

    def update(self, *, t: float, reason: str, target_kph: int,
               lat: float | None, lon: float | None, v_kph: float) -> dict | None:
        """Feed one cycle; return an ease event dict when one just began, else None.

        An event is a transition into an active ease, or a change of the binding
        reason. Ongoing easing (same reason) and no-ease cycles return None.
        """
        active = reason != "none" and (target_kph or 0) > 0
        prev = self._reason
        self._reason = reason if active else "none"

        if not active or reason == prev:
            return None
        # A new / changed active reason -> candidate event, subject to debounce.
        if self._last_t is not None and (t - self._last_t) < self._min_gap_s:
            return None
        self._last_t = t
        return {
            "t": round(t, 1),
            "lat": round(lat, 6) if lat is not None else None,
            "lon": round(lon, 6) if lon is not None else None,
            "reason": reason,
            "target_kph": int(target_kph),
            "v_kph": round(v_kph, 1),
        }
