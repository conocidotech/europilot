#!/usr/bin/env python3
"""Europilot data-gateway client.

Fetches EU driving context from app.europilot.eu and publishes it onto the
msgq bus for downstream services:

  - euNdwMatrixSigns   dynamic NDW matrix signs (speed / lane signalling)
  - euMapData          OSM-derived road context
  - euTrafficLightState C-ITS SPaT/MAP traffic-light data
  - euSpeedLimit       resolved advisory speed limit

Per the architecture docs the comma-four device talks *only* to
app.europilot.eu; every external source (NDW, RDW, OSM) is unlocked
server-side. Responses are Ed25519-signed (same scheme as the feature-flag
client) and verified here before use.

Everything published is ADVISORY / GUIDANCE ONLY. On any fetch or verify
failure the daemon still publishes each message with ``valid = False`` so
consumers can tell the data is stale and must never act on it.

Merge-safe: this file is new and does not modify upstream openpilot logic.
"""

import base64
import json
import time
import urllib.request

from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

GATEWAY_URL = "https://app.europilot.eu/gateway/v1/context"
# Same signing authority as the feature-flag client; the gateway server holds
# the matching private key.
PUBLIC_KEY_B64 = "Pw0CvtpvxfI3G6skI3LMtILJpfvZ/AyH19Vr4HvOUmU="

POLL_HZ = 2.0
REQUEST_TIMEOUT = 5.0
# A signed payload whose server timestamp is older than this (seconds) is
# treated as stale and rejected, so a cached/replayed response can't linger.
MAX_PAYLOAD_AGE = 15.0

SERVICES = ("euNdwMatrixSigns", "euMapData", "euTrafficLightState", "euSpeedLimit")

# Valid capnp enumerants. Gateway input is untrusted, so anything outside these
# sets is coerced to the safe default rather than raising.
_SIGN_KINDS = {"none", "speedLimit", "endSpeedLimit", "laneClosed", "laneOpen",
               "mergeLeft", "mergeRight", "hardShoulderOpen", "warning"}
_ROAD_CLASSES = {"unknown", "motorway", "trunk", "primary", "secondary",
                 "tertiary", "residential", "service"}
_FEATURE_KINDS = {"speedChange", "curve", "junction", "roundabout",
                  "stopSign", "trafficLight"}
_PHASES = {"unknown", "red", "amber", "green", "flashingAmber"}
_SOURCES = {"none", "osm", "ndwMatrix", "camera", "combined"}


def _enum(value, allowed: set, default: str) -> str:
    return value if value in allowed else default


def _int(value, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    # capnp floats can't hold NaN/inf meaningfully for advisory data
    return v if v == v and abs(v) != float("inf") else default


# --- normalizers: untrusted JSON sub-payload -> clean, typed dict ------------
# These are pure and fully unit-testable without capnp/msgq.

def normalize_matrix_signs(d: dict) -> dict:
    signs = []
    for s in (d or {}).get("signs", []) or []:
        signs.append({
            "laneIndex": max(-1, min(15, _int(s.get("lane_index"), -1))),
            "kind": _enum(s.get("kind"), _SIGN_KINDS, "none"),
            "speedLimit": _int(s.get("speed_limit"), -1),
            "distance": _float(s.get("distance")),
            "latitude": _float(s.get("lat")),
            "longitude": _float(s.get("lon")),
        })
    return {"signs": signs}


def normalize_map_data(d: dict) -> dict:
    d = d or {}
    road = d.get("current_road") or {}
    upcoming = []
    for f in d.get("upcoming", []) or []:
        upcoming.append({
            "kind": _enum(f.get("kind"), _FEATURE_KINDS, "speedChange"),
            "distance": _float(f.get("distance")),
            "speedLimit": _int(f.get("speed_limit"), -1),
            "curvature": _float(f.get("curvature")),
        })
    return {
        "currentRoad": {
            "speedLimit": _int(road.get("speed_limit"), -1),
            "roadClass": _enum(road.get("road_class"), _ROAD_CLASSES, "unknown"),
            "name": str(road.get("name", ""))[:256],
            "oneWay": bool(road.get("one_way", False)),
        },
        "upcoming": upcoming,
    }


def normalize_traffic_lights(d: dict) -> dict:
    intersections = []
    for i in (d or {}).get("intersections", []) or []:
        movements = []
        for m in i.get("movements", []) or []:
            movements.append({
                "signalGroupId": max(0, min(255, _int(m.get("signal_group"), 0))),
                "phase": _enum(m.get("phase"), _PHASES, "unknown"),
                "timeToChange": _float(m.get("time_to_change"), -1.0),
            })
        intersections.append({
            "intersectionId": max(0, _int(i.get("id"), 0)),
            "distance": _float(i.get("distance")),
            "movements": movements,
        })
    return {"intersections": intersections}


def normalize_speed_limit(d: dict) -> dict:
    d = d or {}
    return {
        "speedLimit": _int(d.get("value"), -1),
        "source": _enum(d.get("source"), _SOURCES, "none"),
        "confidence": max(0.0, min(1.0, _float(d.get("confidence")))),
    }


class GatewayClient:
    """Fetches and verifies signed gateway context. No msgq dependency."""

    def __init__(self, url: str = GATEWAY_URL, public_key_b64: str = PUBLIC_KEY_B64):
        self.url = url
        self._verify_key = VerifyKey(base64.b64decode(public_key_b64))

    def _verify_and_parse(self, raw: bytes) -> dict | None:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            signature = base64.b64decode(data.pop("signature", ""))
        except Exception:
            return None
        data.pop("public_key", None)
        payload_bytes = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
        try:
            self._verify_key.verify(payload_bytes, signature)
        except (BadSignatureError, Exception):
            return None
        return data

    def _fresh(self, data: dict, now: float | None = None) -> bool:
        ts = data.get("timestamp")
        if ts is None:
            return True  # server did not stamp it; freshness enforced elsewhere
        now = time.time() if now is None else now
        return abs(now - _float(ts, 0.0)) <= MAX_PAYLOAD_AGE

    def fetch(self, position: tuple[float, float] | None = None) -> dict | None:
        """Return the verified, fresh payload dict, or None on any failure.

        ``position`` is an optional (lat, lon); when given it scopes the
        server query to the device's location.
        """
        url = self.url
        if position is not None:
            lat, lon = position
            url = f"{self.url}?lat={lat:.6f}&lon={lon:.6f}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read()
        except Exception:
            return None
        data = self._verify_and_parse(raw)
        if data is None or not self._fresh(data):
            return None
        return data


# --- capnp fill helpers: normalized dict -> Event union field ----------------
# Kept out of GatewayClient so the client stays msgq/capnp-free and testable.

def _fill_matrix_signs(dat, norm: dict) -> None:
    signs = norm["signs"]
    lst = dat.init("signs", len(signs))
    for i, s in enumerate(signs):
        lst[i].laneIndex = s["laneIndex"]
        lst[i].kind = s["kind"]
        lst[i].speedLimit = s["speedLimit"]
        lst[i].distance = s["distance"]
        lst[i].latitude = s["latitude"]
        lst[i].longitude = s["longitude"]


def _fill_map_data(dat, norm: dict) -> None:
    road = norm["currentRoad"]
    dat.currentRoad.speedLimit = road["speedLimit"]
    dat.currentRoad.roadClass = road["roadClass"]
    dat.currentRoad.name = road["name"]
    dat.currentRoad.oneWay = road["oneWay"]
    up = dat.init("upcoming", len(norm["upcoming"]))
    for i, f in enumerate(norm["upcoming"]):
        up[i].kind = f["kind"]
        up[i].distance = f["distance"]
        up[i].speedLimit = f["speedLimit"]
        up[i].curvature = f["curvature"]


def _fill_traffic_lights(dat, norm: dict) -> None:
    inter = dat.init("intersections", len(norm["intersections"]))
    for i, x in enumerate(norm["intersections"]):
        inter[i].intersectionId = x["intersectionId"]
        inter[i].distance = x["distance"]
        mv = inter[i].init("movements", len(x["movements"]))
        for j, m in enumerate(x["movements"]):
            mv[j].signalGroupId = m["signalGroupId"]
            mv[j].phase = m["phase"]
            mv[j].timeToChange = m["timeToChange"]


def _fill_speed_limit(dat, norm: dict) -> None:
    dat.speedLimit = norm["speedLimit"]
    dat.source = norm["source"]
    dat.confidence = norm["confidence"]


def main():
    # Imported here so the client/normalizers above stay importable without a
    # compiled cereal (e.g. in unit tests and on a dev machine).
    from cereal import messaging
    from openpilot.common.realtime import Ratekeeper

    pm = messaging.PubMaster(list(SERVICES))
    sm = messaging.SubMaster(["gpsLocation"])
    client = GatewayClient()
    rk = Ratekeeper(POLL_HZ, print_delay_threshold=None)

    while True:
        sm.update(0)
        position = None
        if sm.valid["gpsLocation"] and sm.recv_frame["gpsLocation"] > 0:
            gps = sm["gpsLocation"]
            position = (gps.latitude, gps.longitude)

        payload = client.fetch(position)
        mono = int(time.monotonic() * 1e9)

        # euNdwMatrixSigns
        msg = messaging.new_message("euNdwMatrixSigns")
        dat = msg.euNdwMatrixSigns
        dat.fetchMonoTime = mono
        dat.valid = payload is not None
        if payload is not None:
            _fill_matrix_signs(dat, normalize_matrix_signs(payload.get("ndw_matrix_signs")))
        pm.send("euNdwMatrixSigns", msg)

        # euMapData
        msg = messaging.new_message("euMapData")
        dat = msg.euMapData
        dat.fetchMonoTime = mono
        dat.valid = payload is not None
        if payload is not None:
            _fill_map_data(dat, normalize_map_data(payload.get("map_data")))
        pm.send("euMapData", msg)

        # euTrafficLightState
        msg = messaging.new_message("euTrafficLightState")
        dat = msg.euTrafficLightState
        dat.fetchMonoTime = mono
        dat.valid = payload is not None
        if payload is not None:
            _fill_traffic_lights(dat, normalize_traffic_lights(payload.get("traffic_lights")))
        pm.send("euTrafficLightState", msg)

        # euSpeedLimit
        msg = messaging.new_message("euSpeedLimit")
        dat = msg.euSpeedLimit
        dat.fetchMonoTime = mono
        dat.valid = payload is not None
        if payload is not None:
            _fill_speed_limit(dat, normalize_speed_limit(payload.get("speed_limit")))
        pm.send("euSpeedLimit", msg)

        rk.keep_time()


if __name__ == "__main__":
    main()
