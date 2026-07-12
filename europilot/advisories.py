#!/usr/bin/env python3
"""Read-side helper for the Europilot NDW matrix-sign advisories.

Consumers (UI, longitudinal nudges) use ``GatewayAdvisories`` to read what
``europilotd`` publishes without touching wire details. Accessors return
``None`` when there is no valid, fresh match, so callers never act on stale
gateway data.

Two things this deliberately does NOT do:

  * It does not collapse the mandatory (red-ringed, legally binding) speed and
    the advisory (no red ring) speed into one number. Callers ask for the one
    they mean. ``target_speed`` -- the lower of the two -- exists for a
    controller that wants a single figure, but the distinction stays available.

  * It does not filter or gate anything. Matrix-sign data is guidance: it may
    raise attention or nudge, never override perception or hard-limit control.

The selection logic lives in module-level pure functions (unit tested); the
class is a thin SubMaster wrapper. Both read via attribute access, so the pure
functions work equally on capnp readers and test doubles.

Merge-safe: this file is new and does not modify upstream openpilot logic.
"""

SERVICES = ["euNdwMatrixSigns", "euSpeedLimit"]


def _gantry(signs, which: str):
    """The named gantry reader, or None when it did not match."""
    if signs is None or not signs.valid:
        return None
    g = getattr(signs, which)
    return g if g.valid else None


def governing_gantry(signs):
    """The gantry we last passed -- the one governing us now."""
    return _gantry(signs, "governing")


def upcoming_gantry(signs):
    """The next gantry ahead, so we can slow down early."""
    return _gantry(signs, "upcoming")


def mandatory_speed(signs) -> int | None:
    """Legally binding (red-ringed) speed from the governing gantry, km/h."""
    g = governing_gantry(signs)
    if g is None or g.mandatorySpeed <= 0:
        return None
    return g.mandatorySpeed


def advisory_speed(signs) -> int | None:
    """Advised (no red ring) speed from the governing gantry, km/h."""
    g = governing_gantry(signs)
    if g is None or g.advisorySpeed <= 0:
        return None
    return g.advisorySpeed


def target_speed(signs) -> int | None:
    """Lowest speed the governing gantry shows either way, km/h.

    What a controller would aim for. Prefer mandatory_speed/advisory_speed when
    the distinction matters (it usually does).
    """
    g = governing_gantry(signs)
    if g is None or g.targetSpeed <= 0:
        return None
    return g.targetSpeed


def upcoming_target_speed(signs) -> tuple[int, float] | None:
    """``(km/h, meters)`` for the next gantry ahead, or None.

    Lets a consumer start easing off before the gantry rather than at it.
    """
    g = upcoming_gantry(signs)
    if g is None or g.targetSpeed <= 0:
        return None
    return (g.targetSpeed, g.distance)


def closed_lanes(signs) -> list[int]:
    """Lane indices closed or diverted at the governing gantry."""
    g = governing_gantry(signs)
    return list(g.closedLanes) if g is not None else []


def is_flashing(signs) -> bool:
    """Governing gantry is flashing (incident warning)."""
    g = governing_gantry(signs)
    return bool(g.flashing) if g is not None else False


class GatewayAdvisories:
    """Thin, validity-aware SubMaster wrapper over the gateway messages."""

    def __init__(self, sm=None):
        # sm may be injected (tests); otherwise built lazily so importing this
        # module doesn't require a compiled cereal.
        self._sm = sm

    def _ensure(self):
        if self._sm is None:
            from cereal import messaging
            self._sm = messaging.SubMaster(SERVICES)
        return self._sm

    def update(self) -> None:
        self._ensure().update(0)

    def _valid(self, service: str):
        sm = self._ensure()
        if not sm.valid[service] or sm.recv_frame[service] == 0:
            return None
        return sm[service]

    def _signs(self):
        return self._valid("euNdwMatrixSigns")

    def mandatory_speed(self) -> int | None:
        return mandatory_speed(self._signs())

    def advisory_speed(self) -> int | None:
        return advisory_speed(self._signs())

    def target_speed(self) -> int | None:
        return target_speed(self._signs())

    def upcoming_target_speed(self) -> tuple[int, float] | None:
        return upcoming_target_speed(self._signs())

    def closed_lanes(self) -> list[int]:
        return closed_lanes(self._signs())

    def is_flashing(self) -> bool:
        return is_flashing(self._signs())

    def camera_speed_limit(self) -> int | None:
        """Advisory speed limit (km/h) from the car's RSA camera, or None."""
        sl = self._valid("euSpeedLimit")
        if sl is None or not sl.valid or sl.speedLimit <= 0:
            return None
        return sl.speedLimit
