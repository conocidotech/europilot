# Europilot data-gateway API contract

The server side of the pipeline. The comma-four device talks **only** to
`app.europilot.eu`; this documents the single endpoint the device
(`europilotd`, see `europilot/gateway.py`) polls, and the exact JSON it
expects. Everything here is **advisory / guidance only** — the device never
uses it to filter perception or hard-limit control.

This contract is executable: `europilot/test_gateway_contract.py` runs the
example payload below through the real device-side verifier and normalizers,
so the spec and the code can't drift.

## Endpoint

```
GET https://app.europilot.eu/gateway/v1/context?lat=<lat>&lon=<lon>
Accept: application/json
```

`lat`/`lon` are the device's current position (6 decimals) and may be omitted
when no GPS fix is available; the server should then return whatever global
context it can (typically an empty payload). Polled at **2 Hz**, 5 s timeout.

## Response envelope

A single JSON object, **Ed25519-signed**. Signature scheme (identical to the
feature-flag client):

1. Build the payload object **without** `signature`/`public_key`.
2. Serialize it canonically: `json.dumps(payload, separators=(",", ":"), sort_keys=True)`.
3. Sign those UTF-8 bytes with the gateway's Ed25519 private key.
4. Add `signature` (base64) and, optionally, `public_key` (base64, informational only).

The device verifies against a **pinned** public key — `public_key` in the body
is never trusted for verification. A payload whose `timestamp` differs from the
device clock by more than **15 s** is rejected as stale (replay protection).

> The gateway needs its **own** Ed25519 keypair — it must not reuse the
> feature-flag key. (Both currently share a placeholder public key in the fork;
> replace before any real deployment.)

### Fields

```jsonc
{
  "timestamp": 1700000000.0,          // unix seconds (server clock)

  "ndw_matrix_signs": {
    "signs": [
      { "lane_index": -1,             // 0 = rightmost lane; -1 = all lanes
        "kind": "speedLimit",         // see enum table
        "speed_limit": 100,           // km/h; -1 if not a speed sign
        "distance": 250.0,            // meters ahead
        "lat": 52.09, "lon": 5.12 }
    ]
  },

  "map_data": {                       // OSM-derived, guidance nudge only
    "current_road": {
      "speed_limit": 80,              // km/h; -1 unknown
      "road_class": "secondary",      // see enum table
      "name": "N201",
      "one_way": false
    },
    "upcoming": [
      { "kind": "speedChange",        // see enum table
        "distance": 300.0,            // meters ahead
        "speed_limit": 50,            // km/h for a speed change; -1 n/a
        "curvature": 0.0 }            // 1/m, signed; 0 n/a
    ]
  },

  "traffic_lights": {                 // C-ITS SPaT/MAP, advisory only
    "intersections": [
      { "id": 42,
        "distance": 60.0,             // meters to stop line
        "movements": [
          { "signal_group": 1,
            "phase": "green",         // see enum table
            "time_to_change": 4.5 }   // seconds; -1 unknown
        ] }
    ]
  },

  "speed_limit": {                    // resolved advisory value
    "value": 100,                     // km/h; -1 unknown
    "source": "combined",             // see enum table
    "confidence": 0.9                 // 0..1
  },

  "signature": "<base64 ed25519>",
  "public_key": "<base64, informational>"
}
```

Any top-level data section may be omitted or `null`; the device then publishes
that message with `valid = true` but empty (no signs / intersections / etc.).
On a failed fetch or signature check the device publishes every message with
`valid = false`.

### Enum values

The device **coerces** any value outside these sets to the safe default shown,
so an unknown enum degrades gracefully rather than dropping the message.

| Field | Allowed values | Default on unknown |
|---|---|---|
| `ndw_matrix_signs.signs[].kind` | `none`, `speedLimit`, `endSpeedLimit`, `laneClosed`, `laneOpen`, `mergeLeft`, `mergeRight`, `hardShoulderOpen`, `warning` | `none` |
| `map_data.current_road.road_class` | `unknown`, `motorway`, `trunk`, `primary`, `secondary`, `tertiary`, `residential`, `service` | `unknown` |
| `map_data.upcoming[].kind` | `speedChange`, `curve`, `junction`, `roundabout`, `stopSign`, `trafficLight` | `speedChange` |
| `traffic_lights.intersections[].movements[].phase` | `unknown`, `red`, `amber`, `green`, `flashingAmber` | `unknown` |
| `speed_limit.source` | `none`, `osm`, `ndwMatrix`, `camera`, `combined` | `none` |

### Numeric coercion

- `lane_index` clamped to `[-1, 15]`.
- `signal_group` clamped to `[0, 255]`.
- `confidence` clamped to `[0, 1]`.
- Unparseable integers → `-1`; unparseable floats → `0.0`.
- `NaN`/`inf` floats are dropped to `0.0` (advisory floats must be finite).
