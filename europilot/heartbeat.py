#!/usr/bin/env python3
"""europilot_heartbeatd -- makes this car visible on app.europilot.eu.

Posts a small heartbeat to the gateway's ``/api/devices/heartbeat`` so the
device shows up (online) on the europilot devices page and can be claimed to an
account. Runs both offroad and onroad on purpose -- a parked car should still be
visible, which is why this is ``always_run`` and not ``only_onroad`` like the
advisory daemons.

Telemetry only: it reads a few identity params and touches nothing openpilot
acts on. The loop is fully guarded -- a network or gateway fault just means the
next beat retries; it never raises out and never blocks driving.

Merge-safe: new file; the only upstream edit is one process_config line.
"""

import json
import time
import urllib.request

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from europilot.osm.client import gateway_host

INTERVAL_S = 60.0       # server marks a device offline after 300s, so beat well under that
TIMEOUT_S = 10.0
DEVICE_NAME = "Europilot"


def _as_str(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


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
        "device_name": _as_str(params.get("EuropilotDeviceName")) or DEVICE_NAME,
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


def main() -> None:
    params = Params()
    host = gateway_host()
    cloudlog.info("europilot_heartbeatd starting (host=%s)", host)
    while True:
        try:
            payload = build_payload(params)
            if payload is None:
                cloudlog.info("europilot_heartbeatd: no DongleId yet, skipping beat")
            else:
                post_heartbeat(host, payload)
        except Exception:
            cloudlog.exception("europilot_heartbeatd beat failed; will retry")
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
