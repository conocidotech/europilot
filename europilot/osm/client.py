"""Device-side sync + match client for OSM tiles.

Per the binding architecture rule, the device talks to exactly one host:
app.europilot.eu. The cold path (`sync`/`poll`) fetches the signed manifest for
the current 2-degree group, verifies the gateway's Ed25519 signature against a
pinned key, and downloads only the tiles whose content hash changed -- checking
each download's sha256 against the signed manifest. The hot path (`match`) runs
against the in-memory roads and never blocks or hits the network.

Fails closed at every step: an unverifiable manifest, a hash mismatch, or a
missing tile leaves the old data in place (or none), and `match` returns
Advisory.none() so the caller falls back to its other sources.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request

from europilot.osm.match import match as match_pose
from europilot.osm.tile import decode_tile
from europilot.osm.types import Advisory
from gateway.osm.grid import group_of, tile_of
from gateway.osm.manifest import content_hash, manifest_etag, tiles_to_fetch, verify_manifest

# The gateway's tile-signing public key (Ed25519), separate from flags.py's key.
# Its private half lives only on the gateway as EUROPILOT_OSM_SIGNING_KEY; the
# device verifies every manifest against this pinned public half and trusts no
# unsigned feed. Rotating it here strands any device still pinned to the old key,
# so change it only in a deliberate key rotation.
OSM_TILE_PUBKEY_B64 = "ArIHIJnQDJwuxgBTv1gLpLpYn53RQNPWvF6pBvQ09Gw="

REFRESH_INTERVAL_S = 300.0
REQUEST_TIMEOUT_S = 10.0


def gateway_host() -> str:
    return os.getenv("EUROPILOT_API_HOST", "https://app.europilot.eu").rstrip("/")


def _http_get(url: str, headers: dict) -> tuple[int, bytes, str]:
    """(status, body, etag). status 0 on transport failure; 304 carries no body."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            return resp.status, resp.read(), (resp.headers.get("ETag") or "").strip('"')
    except urllib.error.HTTPError as e:
        return e.code, b"", (e.headers.get("ETag") or "").strip('"')
    except Exception:
        return 0, b"", ""


class OsmTileClient:
    def __init__(self, host: str | None = None, pubkey_b64: str = OSM_TILE_PUBKEY_B64,
                 transport=None):
        self._host = host or gateway_host()
        self._pubkey = pubkey_b64
        self._get = transport or _http_get
        self._lock = threading.Lock()
        self._roads: dict[tuple[int, int], list] = {}
        self._hash: dict[tuple[int, int], str] = {}
        self._group_etag: dict[tuple[int, int], str] = {}
        self._synced_at: dict[tuple[int, int], float] = {}
        self._refreshing = False

    # --- cold path -------------------------------------------------------

    def _local_group_hashes(self, group: tuple[int, int]) -> dict[tuple[int, int], str]:
        with self._lock:
            return {t: h for t, h in self._hash.items() if group_of(*t) == group}

    def sync(self, lat: float, lon: float) -> bool:
        """Fetch and verify the current group's manifest, pull changed tiles.

        Returns True if the local cache is up to date afterwards (including a 304
        no-change), False if the sync could not be trusted or reached.
        """
        group = group_of(*tile_of(lat, lon))
        headers = {}
        with self._lock:
            etag = self._group_etag.get(group)
        if etag:
            headers["If-None-Match"] = f'"{etag}"'

        status, body, _ = self._get(
            f"{self._host}/osm/manifest?group_lat={group[0]}&group_lon={group[1]}", headers)
        if status == 304:
            self._mark_synced(group)
            return True
        if status != 200 or not body:
            return False

        try:
            manifest = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return False
        if not verify_manifest(manifest, self._pubkey):
            return False   # unsigned or tampered -- do not trust

        wanted = tiles_to_fetch(self._local_group_hashes(group), manifest)
        for ref in wanted:
            self._fetch_tile(ref)

        self._prune(group, manifest)
        with self._lock:
            self._group_etag[group] = manifest_etag(manifest)
        self._mark_synced(group)
        return True

    def _fetch_tile(self, ref) -> None:
        status, body, _ = self._get(
            f"{self._host}/osm/tile?tile_lat={ref.tile_lat}&tile_lon={ref.tile_lon}", {})
        if status != 200 or not body:
            return
        if content_hash(body) != ref.content_hash:
            return   # integrity check against the signed manifest failed
        try:
            roads = decode_tile(body)
        except Exception:
            return
        with self._lock:
            self._roads[(ref.tile_lat, ref.tile_lon)] = roads
            self._hash[(ref.tile_lat, ref.tile_lon)] = ref.content_hash

    def _prune(self, group: tuple[int, int], manifest: dict) -> None:
        """Drop cached tiles that left the group's manifest."""
        keep = {(t["tileLat"], t["tileLon"]) for t in manifest.get("tiles", [])}
        with self._lock:
            for t in [t for t in self._hash if group_of(*t) == group and t not in keep]:
                self._roads.pop(t, None)
                self._hash.pop(t, None)

    def _mark_synced(self, group: tuple[int, int]) -> None:
        with self._lock:
            self._synced_at[group] = time.monotonic()

    def _group_fresh(self, group: tuple[int, int]) -> bool:
        """Whether this group was synced recently enough to skip a refresh.

        Freshness is per-group, not global: crossing a 2-degree group boundary
        must trigger a sync for the new group even though the old one was synced a
        moment ago. (The single global timestamp this replaces left the new group
        unsynced -- and the OSM advisory silently dead -- for up to
        REFRESH_INTERVAL_S after every boundary crossing.)
        """
        ts = self._synced_at.get(group)
        return ts is not None and (time.monotonic() - ts) < REFRESH_INTERVAL_S

    def poll(self, lat: float, lon: float) -> None:
        """Low-rate cold-path trigger: refresh in the background when stale."""
        group = group_of(*tile_of(lat, lon))
        with self._lock:
            if self._refreshing or self._group_fresh(group):
                return
            self._refreshing = True

        def run():
            try:
                self.sync(lat, lon)
            finally:
                with self._lock:
                    self._refreshing = False

        threading.Thread(target=run, daemon=True).start()

    # --- hot path --------------------------------------------------------

    def match(self, lat: float, lon: float, heading: float | None = None) -> Advisory:
        """Advisory for the current pose. Never blocks; Advisory.none() if no data."""
        tile = tile_of(lat, lon)
        with self._lock:
            roads = self._roads.get(tile)
        if not roads:
            return Advisory.none()
        return match_pose((lat, lon), heading, roads)
