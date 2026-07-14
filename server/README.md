# Europilot server (app.europilot.eu)

The FastAPI app behind **app.europilot.eu**: feature flags, device registration +
heartbeat, rides, and the live telemetry bird's-eye view. Served by uvicorn on
loopback `:8100`, fronted by nginx (TLS). Sibling to the signed data gateways
(`deploy/gateway/`, OSM/NDW).

Until now this app was edited in place on the host and lived in no repo; it is
tracked here so it goes through the same review/version-control flow as the rest
of europilot.

## Endpoints (selected)

- `GET  /flags` — signed feature flags (device pins the pubkey).
- `POST /api/devices/heartbeat` — device liveness → `/devices` (unauth, by design).
- `POST /api/devices/telemetry` — onroad ~1 Hz pose + detected objects, stored per
  device; only accepted for a already-known device. `GET /api/devices/{id}/telemetry`
  (owner/admin) feeds the bird's-eye at `GET /devices/{id}/live`.
- `POST /api/rides`, `/rides`, `/dashboard`, `/admin/*`.

## Secrets (NOT in git — provisioned on the host under `keys/`)

- `keys/private.key` — Ed25519 signing seed (`Pw0Cvtpv…`; signs flags/OSM/NDW).
- `keys/admin.token` — admin bearer token; also the session `SECRET_KEY`.
- `keys/azure_sso.json` — Azure SSO client config.
- `flags.db` — SQLite: users (emails, password hashes), devices, rides, telemetry.

`main.py` reads all of these from files at `APP_DIR` — there are no inline secrets.

## Deploy

Currently deployed at `/opt/europilot-api` (systemd `europilot-api.service`:
`uvicorn main:app --host 127.0.0.1 --port 8100 --workers 2`). Bring changes in via
this repo, then sync the checkout on the host. `keys/` and `flags.db` stay on the
host and are never overwritten by a deploy.

## Run locally

```sh
python -m venv venv && ./venv/bin/pip install fastapi uvicorn httpx itsdangerous \
  pynacl starlette jinja2 python-multipart
# provide keys/admin.token + keys/private.key, then:
./venv/bin/uvicorn main:app --host 127.0.0.1 --port 8100
```
