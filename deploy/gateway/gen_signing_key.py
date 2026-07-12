#!/usr/bin/env python3
"""Generate or inspect the gateway's Ed25519 signing identity.

The gateway signs NDW snapshots, OSM manifests and feature flags with one
private key; every device pins the matching public half (currently
`ArIHIJnQDJwuxgBTv1gLpLpYn53RQNPWvF6pBvQ09Gw=` in europilot/{ndw,osm}/client.py
and europilot/flags.py). This tool never prints a private key you didn't ask
for and never touches the environment or disk:

    gen                 -- mint a fresh keypair; print the seed (secret) + pubkey
    pubkey --seed B64   -- derive the public half of an existing seed, so you can
                           confirm the key in gateway.env matches the pinned one

Deploy flow: run `gen` once, put the seed in /etc/europilot/gateway.env as
EUROPILOT_OSM_SIGNING_KEY (chmod 600), and if its pubkey differs from the pinned
value, update the three client constants and reflash the device. Never commit
the seed.
"""

import argparse
import base64
import sys

from nacl.signing import SigningKey

PINNED_PUBKEY_B64 = "ArIHIJnQDJwuxgBTv1gLpLpYn53RQNPWvF6pBvQ09Gw="


def _pubkey_b64(seed_b64: str) -> str:
    sk = SigningKey(base64.b64decode(seed_b64))
    return base64.b64encode(bytes(sk.verify_key)).decode()


def _gen() -> int:
    sk = SigningKey.generate()
    seed_b64 = base64.b64encode(bytes(sk)).decode()
    pub_b64 = base64.b64encode(bytes(sk.verify_key)).decode()
    print("# SECRET -- put in /etc/europilot/gateway.env (chmod 600), never commit:")
    print(f"EUROPILOT_OSM_SIGNING_KEY={seed_b64}")
    print()
    print("# public half -- pin this in europilot/{ndw,osm}/client.py + flags.py:")
    print(pub_b64)
    return 0


def _pubkey(seed_b64: str) -> int:
    pub = _pubkey_b64(seed_b64)
    print(pub)
    if pub == PINNED_PUBKEY_B64:
        print("MATCH: this seed serves the pinned device pubkey", file=sys.stderr)
        return 0
    print(f"MISMATCH: device pins {PINNED_PUBKEY_B64}", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen", help="generate a fresh keypair")
    k = sub.add_parser("pubkey", help="derive+check the pubkey of an existing seed")
    k.add_argument("--seed", required=True, help="base64 Ed25519 seed")
    a = p.parse_args(argv)
    return _gen() if a.cmd == "gen" else _pubkey(a.seed)


if __name__ == "__main__":
    raise SystemExit(main())
