"""Signed tile manifests: the auth and versioning core of tile delivery.

The device must (a) trust that tiles come from app.europilot.eu and weren't
tampered with, and (b) avoid re-downloading tiles it already has. One signed
manifest per 2-degree group does both:

  - The gateway lists every tile in a group with the sha256 of its served bytes,
    and signs the whole list with Ed25519 -- the same trust boundary flags.py
    already establishes (server signs, device verifies a pinned key).
  - The device verifies the signature once, then trusts every hash in the list.
    It fetches only the tiles whose hash differs from its cache (conditional
    delivery) and checks each download's sha256 against the signed hash, so a
    single signature authenticates the whole region without per-tile crypto.

Pure and I/O-free: the gateway half (build/sign) and the device half
(verify/diff) both live here so the wire contract is defined and tested in one
place. delivery.py serves it over HTTP; the device client (EUROPILOT-15)
consumes it.
"""

import base64
import hashlib
import json
from dataclasses import dataclass

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

FORMAT_VERSION = 1


@dataclass(frozen=True)
class TileRef:
    tile_lat: int
    tile_lon: int
    content_hash: str   # sha256 hex of the served tile bytes
    size: int           # byte length, so the device can budget a sync

    def as_dict(self) -> dict:
        return {"tileLat": self.tile_lat, "tileLon": self.tile_lon,
                "contentHash": self.content_hash, "size": self.size}


def content_hash(tile_bytes: bytes) -> str:
    """The integrity hash the manifest carries -- sha256 of the exact bytes."""
    return hashlib.sha256(tile_bytes).hexdigest()


def build_manifest(group_lat: int, group_lon: int, refs: list[TileRef],
                   *, generated_at_unix_s: int = 0) -> dict:
    """Unsigned manifest for one group. Tiles sorted so the form is canonical."""
    tiles = sorted(refs, key=lambda r: (r.tile_lat, r.tile_lon))
    return {
        "formatVersion": FORMAT_VERSION,
        "groupLat": group_lat,
        "groupLon": group_lon,
        "generatedAtUnixS": generated_at_unix_s,
        "tiles": [t.as_dict() for t in tiles],
    }


def _canonical_bytes(manifest: dict) -> bytes:
    """Deterministic bytes of everything except the signature -- what we sign."""
    payload = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def sign_manifest(manifest: dict, signing_key_b64: str) -> dict:
    """Return the manifest with an Ed25519 signature over its canonical bytes."""
    sk = SigningKey(base64.b64decode(signing_key_b64))
    sig = sk.sign(_canonical_bytes(manifest)).signature
    return {**manifest, "signature": base64.b64encode(sig).decode()}


def verify_manifest(manifest: dict, verify_key_b64: str) -> bool:
    """Device side: is this manifest authentically from the pinned key?"""
    sig_b64 = manifest.get("signature")
    if not sig_b64:
        return False
    try:
        vk = VerifyKey(base64.b64decode(verify_key_b64))
        vk.verify(_canonical_bytes(manifest), base64.b64decode(sig_b64))
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True


def manifest_etag(signed_manifest: dict) -> str:
    """Conditional-fetch token for the manifest itself (changes iff it changes)."""
    canonical = json.dumps(signed_manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def tiles_to_fetch(local_hashes: dict[tuple[int, int], str], manifest: dict) -> list[TileRef]:
    """Which tiles the device is missing or has an outdated hash for.

    Caller must verify_manifest first -- diffing an unauthenticated manifest
    would let an attacker steer which tiles get replaced.
    """
    out = []
    for t in manifest.get("tiles", []):
        key = (t["tileLat"], t["tileLon"])
        if local_hashes.get(key) != t["contentHash"]:
            out.append(TileRef(t["tileLat"], t["tileLon"], t["contentHash"], t["size"]))
    return out
