#!/usr/bin/env python3
"""europilot_heartbeatd -- makes this car visible on app.europilot.eu.

Posts a small heartbeat to the gateway's ``/api/devices/heartbeat`` so the
device shows up (online) on the europilot devices page and can be claimed to an
account. Runs both offroad and onroad on purpose -- a parked car should still be
visible, which is why this is ``always_run`` and not ``only_onroad`` like the
advisory daemons.

While onroad it additionally posts live *telemetry* (~1 Hz) to
``/api/devices/telemetry`` so the account holder can watch, on a bird's-eye map,
what the car perceives: own pose (lat/lon/heading/speed) and the vehicles it
detects. Note the hardware only senses *forward* (front radar + camera) -- there
is no side/rear detection -- so the objects are all ahead; the map marks the
sides/rear as blind rather than inventing data.

Read-only: it subscribes to existing msgq services and touches nothing openpilot
acts on. Every step is guarded -- a network, gateway, or msgq fault just skips
that tick; it never raises out and never blocks driving. Telemetry is entirely
best-effort and self-disables if the message layer is unavailable, so the
identity heartbeat stays rock-solid.

Merge-safe: new file; the only upstream edit is one process_config line.
"""

import json
import threading
import time
import urllib.request

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from europilot.loopwatch import LoopWatch
from europilot.osm.client import gateway_host

INTERVAL_S = 60.0       # server marks a device offline after 300s, so beat well under that
TIMEOUT_S = 10.0
TELEMETRY_INTERVAL_S = 1.0    # live-view refresh while onroad
TELEMETRY_TIMEOUT_S = 3.0     # short, so a slow gateway can't stall the 1 Hz loop
TELEMETRY_MAX_OBJECTS = 32    # cap leads/tracks per frame (bound payload size)
# Both GPS backends: comma four (Quectel) publishes gpsLocation via qcomgpsd,
# ublox devices publish gpsLocationExternal. The fork's advisory daemons use
# gpsLocation, so subscribe to both and take whichever has a fix.
GPS_SERVICES = ["gpsLocation", "gpsLocationExternal"]
TELEMETRY_SERVICES = [*GPS_SERVICES, "modelV2", "liveTracks", "carState", "euSpeedLimit"]
DEVICE_NAME = "Europilot"
NAME_FILE = "/data/europilot_device_name"   # optional: one line overrides the display name


def _as_str(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def _device_name() -> str:
    """Display name: an optional /data file, else the default.

    A plain file rather than a Param because openpilot's Params rejects any key
    not in its registry, and adding a key there would not be merge-safe.
    """
    try:
        with open(NAME_FILE) as f:
            name = f.read().strip()
            if name:
                return name
    except OSError:
        pass
    return DEVICE_NAME


def _software_version(params: Params) -> str:
    """Best-effort build version; empty string if it can't be determined."""
    try:
        from openpilot.system.version import get_version
        return get_version()
    except Exception:
        try:
            return _as_str(params.get("Version"))
        except Exception:
            return ""


def _car_model(params: Params) -> str:
    """Fingerprinted car, or empty offroad before CarParams is written."""
    try:
        from cereal import car
        raw = params.get("CarParamsPersistent") or params.get("CarParams")
        if raw:
            return car.CarParams.from_bytes(raw).carFingerprint or ""
    except Exception:
        pass
    return ""


def build_payload(params: Params) -> dict | None:
    """The heartbeat body, or None if we have no dongle id to key on yet."""
    dongle_id = _as_str(params.get("DongleId"))
    if not dongle_id:
        return None
    return {
        "dongle_id": dongle_id,
        "device_name": _device_name(),
        "car_model": _car_model(params),
        "software_version": _software_version(params),
    }


def post_heartbeat(host: str, payload: dict) -> None:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{host}/api/devices/heartbeat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        resp.read()


# --- live telemetry (onroad) -------------------------------------------------
#
# All object coordinates are in the openpilot device frame: x forward, y left,
# metres, relative to the car. The car only senses forward, so x >= 0 for real
# detections; the viewer treats the sides and rear as blind.

def _round(v, n=2):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, n) if f == f else None   # drop NaN


def build_telemetry(dongle_id: str, gps: dict | None,
                    leads: list[dict], tracks: list[dict],
                    easing: dict | None = None) -> dict | None:
    """Assemble a telemetry frame, or None without a usable GPS fix.

    Pure/testable: takes already-extracted primitives so it needs no msgq. Object
    lists are capped and NaNs dropped so the wire payload stays small and clean.
    `easing` (the binding advisory ease) is carried only when actually easing.
    """
    if not dongle_id or not gps:
        return None
    lat, lon = _round(gps.get("lat"), 6), _round(gps.get("lon"), 6)
    if lat is None or lon is None:
        return None

    def clean(objs):
        out = []
        for o in objs:
            if len(out) >= TELEMETRY_MAX_OBJECTS:   # cap the *valid* output
                break
            x, y = _round(o.get("x")), _round(o.get("y"))
            if x is None or y is None:
                continue
            item = {"x": x, "y": y}
            if _round(o.get("v")) is not None:
                item["v"] = _round(o.get("v"))
            if o.get("prob") is not None and _round(o.get("prob"), 3) is not None:
                item["prob"] = _round(o.get("prob"), 3)
            out.append(item)
        return out

    frame = {
        "dongle_id": dongle_id,
        "lat": lat,
        "lon": lon,
        "bearing": _round(gps.get("bearing"), 1),
        "speed_mps": _round(gps.get("speed"), 2),
        "leads": clean(leads),
        "tracks": clean(tracks),
    }
    if easing:
        frame["easing"] = easing
    return frame


def _read_gps(sm) -> dict | None:
    """First GPS service with a fix, as {lat,lon,bearing,speed}, or None.

    Tries gpsLocation (qcom/Quectel) then gpsLocationExternal (ublox): the device
    may publish either, so hardcoding one would blank the map on the other.
    """
    for svc in GPS_SERVICES:
        try:
            if not sm.valid.get(svc, False):
                continue
            g = sm[svc]
            if not getattr(g, "hasFix", False):
                continue
            return {"lat": g.latitude, "lon": g.longitude,
                    "bearing": g.bearingDeg, "speed": g.speed}
        except Exception:
            continue
    return None


def collect_telemetry(dongle_id: str, sm) -> dict | None:
    """Extract a telemetry frame from a live SubMaster (device-only).

    Guards every field access -- a missing or not-yet-valid service just yields
    fewer objects or None, never an exception.
    """
    gps = _read_gps(sm)

    leads = []
    try:
        for ld in sm["modelV2"].leadsV3[:TELEMETRY_MAX_OBJECTS]:
            if ld.x and ld.y:
                leads.append({"x": ld.x[0], "y": ld.y[0],
                              "v": (ld.v[0] if ld.v else None), "prob": ld.prob})
    except Exception:
        leads = []

    tracks = []
    try:
        for p in sm["liveTracks"].points[:TELEMETRY_MAX_OBJECTS]:
            tracks.append({"x": p.dRel, "y": p.yRel, "v": p.vRel})
    except Exception:
        tracks = []

    return build_telemetry(dongle_id, gps, leads, tracks, _read_easing(sm))


def _read_easing(sm) -> dict | None:
    """The binding advisory ease from euSpeedLimit, or None when nothing eases."""
    try:
        if not sm.valid.get("euSpeedLimit", False):
            return None
        sl = sm["euSpeedLimit"]
        reason = str(sl.easingReason)
        if reason == "none" or sl.easingTarget <= 0:
            return None
        out = {"reason": reason, "target_kph": int(sl.easingTarget)}
        if sl.easingDistance >= 1.0:
            out["distance_m"] = _round(sl.easingDistance, 0)
        return out
    except Exception:
        return None


def post_telemetry(host: str, payload: dict) -> None:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{host}/api/devices/telemetry", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=TELEMETRY_TIMEOUT_S) as resp:
        resp.read()


def _make_submaster():
    """A SubMaster for telemetry, or None if the msgq layer is unavailable.

    Telemetry is optional: if this fails (e.g. a partial build) the daemon still
    sends identity heartbeats -- the car stays visible, just without the live map.
    """
    try:
        from cereal import messaging
        return messaging.SubMaster(TELEMETRY_SERVICES)
    except Exception:
        cloudlog.exception("europilot_heartbeatd: telemetry disabled (no msgq)")
        return None


class _Poster(threading.Thread):
    """Does the HTTP posts off the main loop.

    A blocking network call every second (a slow/metered cell can take seconds)
    has no business on a loop that also drains msgq -- it would add scheduling
    jitter. The main loop just hands over the latest payload; this thread posts
    it. Only the newest telemetry frame is kept: a stale live-view frame is
    worthless, so we never build a backlog.
    """

    def __init__(self, host: str):
        super().__init__(daemon=True)
        self._host = host
        self._cv = threading.Condition()
        self._heartbeat: dict | None = None
        self._telemetry: dict | None = None

    def submit_heartbeat(self, payload: dict) -> None:
        with self._cv:
            self._heartbeat = payload
            self._cv.notify()

    def submit_telemetry(self, frame: dict) -> None:
        with self._cv:
            self._telemetry = frame   # keep only the latest
            self._cv.notify()

    def run(self) -> None:
        while True:
            with self._cv:
                while self._heartbeat is None and self._telemetry is None:
                    self._cv.wait()
                hb, self._heartbeat = self._heartbeat, None
                tel, self._telemetry = self._telemetry, None
            if hb is not None:
                try:
                    post_heartbeat(self._host, hb)
                except Exception:
                    cloudlog.exception("europilot_heartbeatd heartbeat post failed")
            if tel is not None:
                try:
                    post_telemetry(self._host, tel)
                except Exception:
                    cloudlog.exception("europilot_heartbeatd telemetry post failed")


def main() -> None:
    params = Params()
    host = gateway_host()
    sm = _make_submaster()
    poster = _Poster(host)
    poster.start()
    watch = LoopWatch("heartbeatd", budget_s=2.0)   # loop period is 1s; flag real stalls
    cloudlog.info("europilot_heartbeatd starting (host=%s, telemetry=%s)", host, sm is not None)

    last_heartbeat = 0.0
    while True:
        watch.tick()
        now = time.monotonic()

        # identity heartbeat, at its own slow cadence (posted off-thread)
        if now - last_heartbeat >= INTERVAL_S:
            try:
                payload = build_payload(params)
                if payload is None:
                    cloudlog.info("europilot_heartbeatd: no DongleId yet, skipping beat")
                else:
                    poster.submit_heartbeat(payload)
            except Exception:
                cloudlog.exception("europilot_heartbeatd beat build failed; will retry")
            last_heartbeat = now

        # live telemetry while onroad -- best-effort, never fatal, posted off-thread
        if sm is not None:
            try:
                sm.update(0)
                if params.get_bool("IsOnroad"):
                    dongle_id = _as_str(params.get("DongleId"))
                    frame = collect_telemetry(dongle_id, sm)
                    if frame is not None:
                        poster.submit_telemetry(frame)
            except Exception:
                cloudlog.exception("europilot_heartbeatd telemetry tick failed; will retry")

        time.sleep(TELEMETRY_INTERVAL_S)


if __name__ == "__main__":
    main()
