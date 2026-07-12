"""Device-side client for matrix sign hints.

Per the binding architecture rule (`architectuur-data-gateway`), the device talks
to exactly one external host: app.europilot.eu. This module contains no NDW
address and no third-party credential. The gateway fetches NDW, normalises it,
and hands us a region snapshot.

Network lives in the cold path only. `poll()` refreshes a snapshot on a
background thread when the vehicle enters a new tile or the snapshot ages out.
`match()` runs against the in-memory snapshot and never blocks.

Fails closed: with no fresh snapshot, `match()` returns None and the caller falls
back to whatever it would have done without a map.
"""

import json
import math
import os
import threading
import time
import urllib.request

from europilot.ndw.match import SEARCH_RADIUS_M, GantryIndex
from europilot.ndw.types import Display, Match, Sign
from gateway.ndw.sign import verify_snapshot

# The gateway's snapshot-signing public key (Ed25519). The gateway signs every
# feed with one identity, so this is the same pinned key the tile client uses;
# the device verifies each snapshot against it and trusts no unsigned feed.
# Rotating it strands any device still pinned to the old key -- change it only in
# a deliberate key rotation, in step with the gateway.
SNAPSHOT_PUBKEY_B64 = "Pw0CvtpvxfI3G6skI3LMtILJpfvZ/AyH19Vr4HvOUmU="

# Same tile size as the OSM decision doc (mapd's AREA_BOX_DEGREES).
TILE_DEG = 0.25
EARTH_RADIUS_M = 6_371_000.0

REFRESH_INTERVAL_S = 60.0
STALE_AFTER_S = 300.0
REQUEST_TIMEOUT_S = 10.0


def gateway_host() -> str:
    return os.getenv("EUROPILOT_API_HOST", "https://app.europilot.eu").rstrip("/")


def tile_of(lat: float, lon: float) -> tuple[int, int]:
    return (int(math.floor(lat / TILE_DEG)), int(math.floor(lon / TILE_DEG)))


class _Snapshot:
    """Signs for one padded tile, plus the box they were taken from."""

    __slots__ = ("tile", "bounds", "index", "states", "fetched_at")

    def __init__(self, tile, bounds, index, states, fetched_at):
        self.tile = tile
        self.bounds = bounds
        self.index = index
        self.states = states
        self.fetched_at = fetched_at

    def covers(self, lat: float, lon: float) -> bool:
        """True when every sign that could match this pose is in this snapshot.

        The gateway pads each tile, so a snapshot answers correctly beyond its raw
        tile. Requiring an exact tile match would throw that padding away and blind
        the car at every tile boundary — which is exactly where a gantry can sit.
        """
        margin_lat = math.degrees(SEARCH_RADIUS_M / EARTH_RADIUS_M)
        margin_lon = margin_lat / max(math.cos(math.radians(lat)), 1e-6)
        return (
            self.bounds["min_lat"] + margin_lat <= lat <= self.bounds["max_lat"] - margin_lat
            and self.bounds["min_lon"] + margin_lon <= lon <= self.bounds["max_lon"] - margin_lon
        )


class MatrixSignClient:
    """Keeps one region snapshot in memory and matches poses against it."""

    def __init__(self, host: str | None = None, pubkey_b64: str = SNAPSHOT_PUBKEY_B64):
        self._host = host or gateway_host()
        self._pubkey = pubkey_b64
        self._lock = threading.Lock()
        self._snapshot: _Snapshot | None = None
        self._refreshing = False

    # --- cold path -------------------------------------------------------

    def _fetch(self, tile: tuple[int, int]) -> _Snapshot | None:
        url = f"{self._host}/ndw/region?tile_lat={tile[0]}&tile_lon={tile[1]}"
        try:
            with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_S) as resp:
                payload = json.loads(resp.read())
        except Exception:
            return None

        if not verify_snapshot(payload, self._pubkey):
            return None   # unsigned or tampered -- do not trust; fall back

        try:
            signs = [Sign.from_json(s) for s in payload["signs"]]
            states = {u: Display.from_json(u, d) for u, d in payload["states"].items()}
            bounds = {k: float(payload["bounds"][k]) for k in
                      ("min_lat", "min_lon", "max_lat", "max_lon")}
        except (KeyError, TypeError, ValueError):
            return None

        return _Snapshot(tile, bounds, GantryIndex(signs), states, time.monotonic())

    def _refresh_async(self, tile: tuple[int, int]) -> None:
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True

        def run():
            snapshot = self._fetch(tile)
            with self._lock:
                if snapshot is not None:
                    self._snapshot = snapshot
                self._refreshing = False

        threading.Thread(target=run, daemon=True).start()

    def poll(self, lat: float, lon: float) -> None:
        """Call at a low rate. Triggers a background refresh when needed."""
        tile = tile_of(lat, lon)
        with self._lock:
            snapshot = self._snapshot
        stale = snapshot is None or (time.monotonic() - snapshot.fetched_at) > REFRESH_INTERVAL_S
        if stale or snapshot.tile != tile or not snapshot.covers(lat, lon):
            self._refresh_async(tile)

    # --- hot path --------------------------------------------------------

    def age_s(self) -> float | None:
        with self._lock:
            snapshot = self._snapshot
        return None if snapshot is None else time.monotonic() - snapshot.fetched_at

    def match(self, lat: float, lon: float, heading: float) -> Match | None:
        """Never blocks, never hits the network. None means: no usable hint."""
        with self._lock:
            snapshot = self._snapshot
        if snapshot is None:
            return None
        if time.monotonic() - snapshot.fetched_at > STALE_AFTER_S:
            return None
        if not snapshot.covers(lat, lon):
            return None
        return snapshot.index.match(lat, lon, heading, snapshot.states)
