# Note: the NDW gateway response is unauthenticated

**Status:** open question for the team. Not a blocker for the current NDW work;
worth settling before the gateway carries anything a controller acts on.

## What I noticed

Two things cross the same trust boundary (`app.europilot.eu` → device), with
two different answers:

| | transport | integrity |
|---|---|---|
| `europilot/flags.py` (feature flags, kill switch) | HTTPS | **Ed25519 signature over the payload, verified against a key pinned in the source** |
| `europilot/ndw/client.py` (matrix-sign region snapshots) | HTTPS | none — the JSON is trusted as received |

The architecture makes `app.europilot.eu` the *sole* external host precisely so
that there is one place to secure. Right now that place is secured for flags but
not for road data.

## Why it might matter

TLS authenticates the *host* and protects the *channel*. It does not sign the
*payload*, so it does not survive anything that terminates TLS: a
misconfigured/compromised CDN or reverse proxy in front of the gateway, a
corporate MITM box on a tethered connection, or a device with an attacker-added
CA. In those cases a forged region snapshot is indistinguishable from a real
one.

What a forged snapshot buys an attacker is bounded but real: matrix-sign data
is advisory and the matcher fails closed, so it cannot *directly* command the
car. But it can:

* show the driver a wrong speed (including a wrong **mandatory**, red-ringed
  one — the legally binding kind);
* mark lanes closed that are open, or open that are closed;
* suppress a real gantry by omitting it (the device cannot tell "no signs here"
  from "signs withheld").

And the moment a longitudinal nudge consumes `targetSpeed`, the blast radius
grows.

## The cheap fix

We already have the mechanism, the key-handling pattern, and a working verifier
in `flags.py`. Signing the region payload is roughly:

* gateway: sign the canonical JSON (`separators=(",", ":")`, `sort_keys=True`)
  with an Ed25519 private key, attach `signature`;
* device: verify in `MatrixSignClient._fetch()` against a pinned public key,
  and treat a bad signature exactly like a failed fetch (fail closed — the
  code already does the right thing on `None`).

Two things to settle:

1. **Its own keypair.** The gateway must not reuse the feature-flag key. (Both
   currently carry the same placeholder public key in the fork — that needs
   replacing before any real deployment regardless of this decision.)
2. **Replay.** A signature alone does not stop a stale snapshot being replayed.
   The payload already carries `age_s`, but that is *self-reported*. If we sign,
   sign a server timestamp too and bound the skew device-side (`flags.py` has no
   answer for this either — worth fixing in both).

## What I'd like from the team

A decision, not necessarily this design:

* **Sign it** — consistent with flags, cheap, and the pattern exists; or
* **Consciously accept TLS-only** for advisory road data, and write that down
  in the architecture docs so the next person doesn't re-litigate it — but then
  revisit before anything on the control path consumes it.

Either is defensible. The current state — two answers, neither written down —
is the one that isn't.
