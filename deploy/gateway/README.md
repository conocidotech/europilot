# Europilot gateway deploy (`app.europilot.eu`)

The device talks to exactly one external host, `app.europilot.eu`. This host
serves three signed things over HTTPS and holds every third-party address the car
must never see:

| Path | Served by | Contents |
|------|-----------|----------|
| `/ndw/region` | `gateway/ndw/service.py` | signed NDW matrix-sign region snapshots (live) |
| `/osm/manifest`, `/osm/tile` | `osm_pipeline.py serve` | signed OSM tiles: speed limits, residential, cameras |
| `/flags` | static file via Caddy | signed feature flags (optional; 404 = all off, safe) |
| `/health` | NDW service | liveness + feed freshness |

Everything is **Ed25519-signed with one key**; the device pins the public half
(`ArIHIJnQDJwuxgBTv1gLpLpYn53RQNPWvF6pBvQ09Gw=`) and trusts nothing unsigned, so
a bug or a TLS middlebox can't feed the car a wrong (even "legally binding")
speed. Until the gateway is live the device's OSM/NDW/camera advisories are all
`valid=false` and the HUD stays blank — this is what turns the lights on.

## What runs where

```
                        ┌─────────── app.europilot.eu host ───────────┐
  device ── HTTPS ─────▶│  Caddy :443 (auto-TLS)                       │
                        │    /ndw/*  → 127.0.0.1:8080  europilot-ndw   │
                        │    /osm/*  → 127.0.0.1:8081  europilot-osm   │
                        │    /flags  → static signed file             │
                        │                                             │
                        │  europilot-osm-build.timer (daily):         │
                        │    curl NL PBF → osm_pipeline build → SIGHUP │
                        └─────────────────────────────────────────────┘
```

Runtime is deliberately tiny — `pynacl` + `pycapnp` in a standalone venv
(`.venv-gateway`), plus `osmium-tool` for ingest. **No openpilot build, no scons,
no GPU.** NDW is pure stdlib + `pynacl`; OSM adds `pycapnp` for the wire format.

## Files here

- `bootstrap.sh` — idempotent host setup (user, venv, dirs, osmium, Caddy, units).
- `gen_signing_key.py` — mint / verify the signing key against the pinned pubkey.
- `osm_pipeline.py` — `build` (PBF → signed tile dir) and `serve` (the M6 glue
  that `gateway/osm/` deliberately leaves out of the merge-safe library).
- `Caddyfile`, `gateway.env.example`, `systemd/*` — host config.

## First light (in order)

Prereqs you supply: a Linux host, DNS for `app.europilot.eu` pointing at it, the
signing seed (the private half of the pinned key), and the real NDW MSI shapefile.

```sh
# 0. On the host, as the app checkout that will live at /opt/europilot:
git clone <fork> /opt/europilot && cd /opt/europilot
git checkout europilot-deploy          # the master-based, built branch

# 1. Bootstrap (installs everything, starts nothing):
sudo deploy/gateway/bootstrap.sh

# 2. DNS: point app.europilot.eu A/AAAA at this host (needed for TLS issuance).

# 3. Key: set the seed in /etc/europilot/gateway.env, then PROVE it matches:
sudo -e /etc/europilot/gateway.env      # set EUROPILOT_OSM_SIGNING_KEY=...
.venv-gateway/bin/python deploy/gateway/gen_signing_key.py \
    pubkey --seed "$(grep -oP '(?<=EUROPILOT_OSM_SIGNING_KEY=).*' /etc/europilot/gateway.env)"
#   → must print "MATCH". If MISMATCH, either use the right seed, or mint a new
#     key (`gen`), pin its pubkey in europilot/{ndw,osm}/client.py + flags.py, and
#     reflash the device.

# 4. NDW shapefile: put the real MSI sign geometry here:
sudo cp msi_shp.zip /var/lib/europilot/ndw/msi_shp.zip

# 5. Bring it up — NDW + TLS first (fastest visible win):
sudo systemctl enable --now caddy europilot-ndw
curl -s https://app.europilot.eu/health         # {"ok": true, "age_s": ...}

# 6. OSM — build the first tile set (downloads ~1.5 GB NL PBF, ~minutes), serve:
sudo systemctl start europilot-osm-build         # oneshot build
sudo systemctl enable --now europilot-osm europilot-osm-build.timer
```

## Point the device at it

The device already defaults to `https://app.europilot.eu`. Nothing to set unless
you're testing against another host, where on the car:

```sh
# override host (staging), leave unset for production:
echo -n https://staging.example.eu > /data/params/d/EuropilotApiHost   # if wired
```

Then drive a motorway with matrix signs: `euNdwMatrixSigns` goes `valid=true` and
the HUD shows the live limit. Once OSM tiles are live, `euMapAdvisory` fills in
static limits + camera distance, and the opt-in camera easing
(`EuropilotCameraEasing=1`, supervised) has data to act on.

## Validation without the host

`osm_pipeline.py` was smoke-tested end-to-end against the repo fixtures: build →
serve → the device client verifies the manifest signature and each tile's hash;
the NDW service signs a 427-sign region snapshot the device client verifies. So
the glue is proven; what the host adds is real data + TLS + DNS.

## Operational notes

- **Signing key is never on disk in the repo or in git.** It lives only in
  `/etc/europilot/gateway.env` (chmod 600, root:root). Same key signs NDW, OSM
  and flags unless you set `EUROPILOT_NDW_SIGNING_KEY`.
- **Zero-downtime tile refresh.** `build` writes a new tile dir atomically and the
  build unit sends `SIGHUP`; `serve` reloads in place, so device syncs never see a
  half-written set. Tile hashes are stable across rebuilds of identical data
  (`generated_at` = the PBF's mtime), so an unchanged extract costs the device no
  re-download.
- **Fail-safe by design.** Feed stale → `/health` 503 and snapshots carry `age_s`
  so the device falls back to the car's own sign recognition. `/flags` 404 → all
  flags off. Bad rebuild → `serve` keeps the last good tiles.
- **Scope.** Ships all of NL. To validate on a smaller area first, filter the PBF
  with `osmium extract -b <bbox>` before `build --pbf`.
- **Attribution.** Tiles carry `© OpenStreetMap contributors, ODbL 1.0`; NDW MSI
  data is CC-BY. Keep both visible once third-party devices sync.

## Known follow-ups (not blockers)

- **Diff-based refresh (M6 tail).** The build re-derives the whole NL extract
  daily. `gateway/osm/replication.py` + `ingest.apply_diffs` can roll the PBF
  forward with daily `.osc.gz` diffs instead — cheaper, wire it when daily full
  builds become a cost.
- **NDW shapefile sourcing.** The production MSI sign-geometry shapefile must come
  from NDW open data; the repo only ships a test fixture.
- **`/flags` publishing.** A tiny signer for feature flags (reuse `gen_signing_key`
  + `sign_snapshot` style) if/when flags are used; 404 is safe until then.
