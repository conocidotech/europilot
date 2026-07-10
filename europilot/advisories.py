#!/usr/bin/env python3
"""Read-side helper for the Europilot gateway advisories.

Consumers (UI, longitudinal nudges, ...) use ``GatewayAdvisories`` to read the
messages published by ``europilotd`` (see europilot/gateway.py) without having
to know the wire details. It wraps SubMaster and exposes staleness-aware
accessors that return ``None`` when data is missing or stale, so callers never
act on outdated gateway data.

Everything here is ADVISORY / GUIDANCE ONLY. Per the architecture docs, map
and sign data may raise attention or nudge, but must never filter perception
or hard-limit control.

The selection logic lives in module-level pure functions (readable and unit
tested); the class is a thin SubMaster wrapper. Both operate on capnp readers
via attribute access, so the pure functions work equally on test doubles.

Merge-safe: this file is new and does not modify upstream openpilot logic.
"""

SERVICES = ["euNdwMatrixSigns", "euMapData", "euTrafficLightState", "euSpeedLimit"]

_STOP_PHASES = ("red", "amber", "flashingAmber")


def resolve_speed_limit(speed_limit) -> int | None:
    """Resolved advisory speed limit (km/h), or None if unknown.

    ``speed_limit`` is a euSpeedLimit reader (``.valid``, ``.speedLimit``).
    """
    if speed_limit is None or not speed_limit.valid:
        return None
    return speed_limit.speedLimit if speed_limit.speedLimit > 0 else None


def relevant_matrix_speed(signs, ego_lane: int) -> int | None:
    """Nearest matrix-sign speed limit (km/h) that applies to ``ego_lane``.

    A sign applies when its ``laneIndex`` is the ego lane or -1 (all lanes).
    Returns None when no applicable speed sign is present.
    """
    best = None
    for s in signs:
        if s.kind != "speedLimit" or s.speedLimit <= 0:
            continue
        if s.laneIndex not in (-1, ego_lane):
            continue
        if best is None or s.distance < best.distance:
            best = s
    return best.speedLimit if best is not None else None


def advised_speed_limit(speed_limit, signs, ego_lane: int) -> int | None:
    """Single advisory speed limit for the driver.

    A live matrix sign (dynamic, road-authority) overrides the resolved/OSM
    value when present, since it reflects current road conditions.
    """
    matrix = relevant_matrix_speed(signs, ego_lane)
    if matrix is not None:
        return matrix
    return resolve_speed_limit(speed_limit)


def nearest_stop_signal(intersections) -> dict | None:
    """Nearest intersection ahead showing a stop phase (red/amber).

    Returns ``{"intersectionId", "distance", "timeToChange"}`` for the closest
    such intersection, or None. ``timeToChange`` is the soonest known phase
    change among its stop movements, or -1 when unknown.
    """
    best = None
    for i in intersections:
        stop_movements = [m for m in i.movements if m.phase in _STOP_PHASES]
        if not stop_movements:
            continue
        if best is None or i.distance < best["distance"]:
            ttcs = [m.timeToChange for m in stop_movements if m.timeToChange >= 0]
            best = {
                "intersectionId": i.intersectionId,
                "distance": i.distance,
                "timeToChange": min(ttcs) if ttcs else -1.0,
            }
    return best


def upcoming_speed_change(map_data) -> dict | None:
    """Nearest upcoming OSM speed change ahead (guidance nudge only).

    ``map_data`` is a euMapData reader. Returns ``{"speedLimit", "distance"}``
    for the closest ``speedChange`` feature with a known limit, or None.
    """
    if map_data is None or not map_data.valid:
        return None
    best = None
    for f in map_data.upcoming:
        if f.kind != "speedChange" or f.speedLimit <= 0:
            continue
        if best is None or f.distance < best["distance"]:
            best = {"speedLimit": f.speedLimit, "distance": f.distance}
    return best


class GatewayAdvisories:
    """Thin, staleness-aware SubMaster wrapper over the gateway messages."""

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

    def _reader(self, service: str):
        """Return the reader for ``service`` only if it is currently valid."""
        sm = self._ensure()
        if not sm.valid[service] or sm.recv_frame[service] == 0:
            return None
        return sm[service]

    def speed_limit(self, ego_lane: int = 0) -> int | None:
        sl = self._reader("euSpeedLimit")
        ms = self._reader("euNdwMatrixSigns")
        signs = ms.signs if ms is not None else []
        return advised_speed_limit(sl, signs, ego_lane)

    def stop_signal(self) -> dict | None:
        tl = self._reader("euTrafficLightState")
        return nearest_stop_signal(tl.intersections) if tl is not None else None

    def next_speed_change(self) -> dict | None:
        return upcoming_speed_change(self._reader("euMapData"))
