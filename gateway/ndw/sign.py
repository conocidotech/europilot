"""Ed25519 signatures for NDW region snapshots.

Same trust boundary as the OSM tiles (gateway/osm/manifest.py) and the feature
flags: the gateway signs, the device verifies against a pinned public key and
trusts no unsigned feed. Until this, the region snapshot was plain JSON over
HTTPS -- a gateway bug or a TLS-terminating middlebox could feed the car a wrong
matrix-sign speed (including a "legally binding" one) with nothing to catch it.

The gateway signs every feed with one identity. The signed payload carries a
`kind` tag so a signature is bound to the feed it was made for and can never be
replayed as another feed's payload.

Pure and I/O-free (bar the env helper): the gateway half (sign) and the device
half (verify) both live here so the wire contract is defined and tested in one
place.
"""

import base64
import json
import os

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

SNAPSHOT_KIND = "ndwRegion"

# The gateway uses one signing identity for every feed, so the NDW key defaults
# to the same one the OSM tiles use; a dedicated key can override it. Either way
# the device pins the matching public half.
SIGNING_KEY_ENV = "EUROPILOT_NDW_SIGNING_KEY"
FALLBACK_SIGNING_KEY_ENV = "EUROPILOT_OSM_SIGNING_KEY"


def _canonical_bytes(payload: dict) -> bytes:
    """Deterministic bytes of everything except the signature -- what we sign."""
    body = {k: v for k, v in payload.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def sign_snapshot(payload: dict, signing_key_b64: str) -> dict:
    """Return the snapshot with `kind` stamped and an Ed25519 signature attached."""
    stamped = {**payload, "kind": SNAPSHOT_KIND}
    sk = SigningKey(base64.b64decode(signing_key_b64))
    sig = sk.sign(_canonical_bytes(stamped)).signature
    return {**stamped, "signature": base64.b64encode(sig).decode()}


def verify_snapshot(payload, verify_key_b64: str) -> bool:
    """Device side: an authentic region snapshot from the pinned key? Fails closed.

    A non-object body, a missing/wrong `kind`, a missing or bad signature -- all
    return False rather than raise, so an unsigned or tampered feed is simply not
    trusted and the caller falls back to the car's own sign recognition.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("kind") != SNAPSHOT_KIND:
        return False
    sig_b64 = payload.get("signature")
    if not sig_b64:
        return False
    try:
        vk = VerifyKey(base64.b64decode(verify_key_b64))
        vk.verify(_canonical_bytes(payload), base64.b64decode(sig_b64))
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True


def signing_key_from_env() -> str:
    """The gateway's base64 Ed25519 seed, validated so a bad key fails at startup."""
    key = os.environ.get(SIGNING_KEY_ENV) or os.environ.get(FALLBACK_SIGNING_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"neither {SIGNING_KEY_ENV} nor {FALLBACK_SIGNING_KEY_ENV} is set -- the "
            + "gateway needs its Ed25519 signing key to sign region snapshots "
            + "(pin its public half in the device client; do not commit the private key)"
        )
    try:
        SigningKey(base64.b64decode(key))   # fail fast on a malformed key
    except (ValueError, TypeError) as e:
        raise RuntimeError(f"{SIGNING_KEY_ENV} is not a valid base64 Ed25519 seed") from e
    return key
