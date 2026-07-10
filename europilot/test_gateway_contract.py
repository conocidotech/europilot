"""Executable contract test for europilot/GATEWAY_API.md.

Runs the exact example payload from the API doc through the real device-side
verifier and normalizers, asserting the device interprets it as documented.
Keeps the server contract and the device code from drifting.
"""

import base64
import json
from nacl.signing import SigningKey

from europilot import gateway as g
from europilot.gateway import (
    GatewayClient,
    normalize_matrix_signs,
    normalize_map_data,
    normalize_traffic_lights,
    normalize_speed_limit,
)

# The example envelope from GATEWAY_API.md (minus signature/public_key, which
# the server adds after signing).
EXAMPLE_PAYLOAD = {
    "timestamp": 1700000000.0,
    "ndw_matrix_signs": {
        "signs": [
            {"lane_index": -1, "kind": "speedLimit", "speed_limit": 100,
             "distance": 250.0, "lat": 52.09, "lon": 5.12},
        ],
    },
    "map_data": {
        "current_road": {"speed_limit": 80, "road_class": "secondary",
                         "name": "N201", "one_way": False},
        "upcoming": [
            {"kind": "speedChange", "distance": 300.0, "speed_limit": 50, "curvature": 0.0},
        ],
    },
    "traffic_lights": {
        "intersections": [
            {"id": 42, "distance": 60.0,
             "movements": [{"signal_group": 1, "phase": "green", "time_to_change": 4.5}]},
        ],
    },
    "speed_limit": {"value": 100, "source": "combined", "confidence": 0.9},
}


def sign_like_server(payload: dict, sk: SigningKey) -> bytes:
    """Reproduce the documented signing scheme."""
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = sk.sign(payload_bytes).signature
    return json.dumps({
        **payload,
        "signature": base64.b64encode(sig).decode(),
        "public_key": base64.b64encode(sk.verify_key.encode()).decode(),
    }).encode()


def make_client() -> tuple[SigningKey, GatewayClient]:
    sk = SigningKey.generate()
    client = GatewayClient(public_key_b64=base64.b64encode(sk.verify_key.encode()).decode())
    return sk, client


class TestGatewayContract:
    def test_documented_example_verifies(self):
        sk, client = make_client()
        raw = sign_like_server(EXAMPLE_PAYLOAD, sk)
        data = client._verify_and_parse(raw)
        assert data is not None, "documented signing scheme must verify"
        # payload survives round-trip intact (minus signature/public_key)
        assert data["speed_limit"]["value"] == 100
        assert client._fresh(data, now=EXAMPLE_PAYLOAD["timestamp"] + 5)

    def test_documented_example_normalizes_as_specified(self):
        sk, client = make_client()
        raw = sign_like_server(EXAMPLE_PAYLOAD, sk)
        data = client._verify_and_parse(raw)

        signs = normalize_matrix_signs(data["ndw_matrix_signs"])["signs"]
        assert signs[0] == {"laneIndex": -1, "kind": "speedLimit", "speedLimit": 100,
                            "distance": 250.0, "latitude": 52.09, "longitude": 5.12}

        md = normalize_map_data(data["map_data"])
        assert md["currentRoad"] == {"speedLimit": 80, "roadClass": "secondary",
                                     "name": "N201", "oneWay": False}
        assert md["upcoming"][0] == {"kind": "speedChange", "distance": 300.0,
                                     "speedLimit": 50, "curvature": 0.0}

        tl = normalize_traffic_lights(data["traffic_lights"])["intersections"][0]
        assert tl["intersectionId"] == 42
        assert tl["movements"][0] == {"signalGroupId": 1, "phase": "green",
                                      "timeToChange": 4.5}

        assert normalize_speed_limit(data["speed_limit"]) == \
            {"speedLimit": 100, "source": "combined", "confidence": 0.9}

    def test_enum_tables_match_doc(self):
        # the enum sets the doc advertises must be exactly what the code allows
        assert g._SIGN_KINDS == {"none", "speedLimit", "endSpeedLimit", "laneClosed",
                                 "laneOpen", "mergeLeft", "mergeRight",
                                 "hardShoulderOpen", "warning"}
        assert g._ROAD_CLASSES == {"unknown", "motorway", "trunk", "primary",
                                   "secondary", "tertiary", "residential", "service"}
        assert g._FEATURE_KINDS == {"speedChange", "curve", "junction", "roundabout",
                                    "stopSign", "trafficLight"}
        assert g._PHASES == {"unknown", "red", "amber", "green", "flashingAmber"}
        assert g._SOURCES == {"none", "osm", "ndwMatrix", "camera", "combined"}
