# Europilot data-gateway: device ↔ server

How the comma-four device gets EU road data. Per the binding architecture rule
(`architectuur-data-gateway`), the device talks to exactly one external host,
`app.europilot.eu`; it holds no NDW/RDW/OSM address and no third-party
credential.

## The shape: tiles out, matching on the device

The gateway serves **region snapshots** — sign geometry plus live aspects for
one padded tile — and the device matches them against its own pose locally.

It is worth being explicit about why, because the obvious alternative (the
server returns "the sign 250 m ahead in your lane") does not work:

* **Distance and lane depend on the pose**, which moves at 20 Hz+. A gateway
  poll is on the order of a minute. At 100 km/h the car covers ~28 m per
  second, so any server-computed distance is wrong by the time it lands.
* **The gateway cannot know our lane at all.** Lane assignment is a local,
  geometric question.

So: the gateway owns the NDW knowledge (where every sign is, what it currently
shows); the device owns the geometry (which gantry governs *us*). Matching is
pure and never touches the network — see `europilot/ndw/match.py`.

Tiles are 0.25°, the same grid as the OSM distribution decision, padded by the
matcher's search radius so a car near a tile edge still sees the gantry it is
about to pass.

## Endpoint

```
GET https://app.europilot.eu/ndw/region?tile_lat=<int>&tile_lon=<int>
```

Tile indices are `floor(lat / 0.25)` and `floor(lon / 0.25)`. Response:

```jsonc
{
  "tile_lat": 208, "tile_lon": 20, "tile_deg": 0.25,
  "bounds": {                    // the PADDED box the signs were taken from;
    "min_lat": 51.97, "min_lon": 4.96,   // the device needs it to know how far
    "max_lat": 52.28, "max_lon": 5.29    // outside the raw tile this still answers
  },
  "age_s": 12.4,                 // since the gateway's last good NDW refresh
  "signs": [                     // static geometry, from the NDW shapefile
    { "uuid": "...", "road": "A2", "carriageway": "R", "lane": 2,
      "km": 51.3, "wvk_id": "...", "bearing": 197.4, "lat": 52.09, "lon": 5.12 }
  ],
  "states": {                    // live aspect per sign uuid
    "...": { "aspect": "speedlimit", "speed": 70,
             "flashing": false, "red_ring": true, "ts_state": "..." }
  }
}
```

Signs without a live state are dropped. `GET /health` returns
`{"ok", "age_s", "consecutive_failures"}`, 503 when the snapshot is stale.

### red_ring is not cosmetic

A Dutch matrix sign shows a speed either **with a red ring — legally binding**
— or **without one — advice**. The pipeline keeps these apart end to end:
`Display.red_ring` → `Gantry.mandatory_speed` / `Gantry.advisory_speed` →
`euNdwMatrixSigns.mandatorySpeed` / `.advisorySpeed`. Never collapse them into
one number without saying which you mean. `target_speed` (the lower of the two)
exists for a controller that needs a single figure.

## On the bus

`europilotd` (`europilot/gateway.py`) polls the tile in the cold path, matches
in the hot path, and publishes the result as `euNdwMatrixSigns` (governing +
upcoming gantry). Consumers read it via `europilot/advisories.py` — they never
touch the wire format. With no fresh snapshot the daemon publishes
`valid = false`, and consumers fall back to whatever they would do without a
map. **All of it is advisory**: it may raise attention or nudge, never filter
perception or hard-limit control.

The remaining EU sources (OSM context, C-ITS SPaT/MAP traffic lights, a fused
speed limit) hold reserved cereal slots but are **not defined yet** — each is
tile-distributed and device-matched like NDW, so its bus schema falls out of
its matcher. Define each one together with its pipeline.

## Open: the gateway response is unauthenticated

**This needs a decision — see `europilot/NOTES-gateway-signing.md`.** The
region endpoint is served as plain JSON over TLS with no payload signature,
while the feature-flag client (`europilot/flags.py`) already verifies an
Ed25519 signature over its response with a pinned key. Same trust boundary, two
different answers.
