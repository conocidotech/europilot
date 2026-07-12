"""Device-side OSM consumer: sync signed tiles, match pose, emit advisories.

The other half of the OSM pipeline. gateway/osm/ builds and serves signed
Cap'n Proto tiles; this package runs on the car: it syncs the tiles for the
current region (verifying the gateway's Ed25519 signature), reads them, and
matches the ego pose to the road it's on to produce advisory hints -- the OSM
speed limit that feeds euSpeedLimit fusion, plus residential/comfort/cycleway
context. Advisory only: a nudge, never a filter, never authoritative for control.
"""
