#!/usr/bin/env bash
# One-shot host bootstrap for the Europilot gateway (app.europilot.eu).
#
# Idempotent: safe to re-run after edits. Run as root from inside the repo
# checkout that will live at /opt/europilot:
#     sudo deploy/gateway/bootstrap.sh
#
# It creates the service user, a minimal Python venv (pynacl + pycapnp only --
# NOT the full openpilot stack), the data dirs, installs osmium-tool and Caddy,
# and drops the systemd units + Caddyfile in place. It does NOT start anything or
# touch your signing key; the README's "First light" section does that, in order.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="${APP_DIR}/.venv-gateway"
DATA_DIR=/var/lib/europilot
ETC_DIR=/etc/europilot
USER=europilot

if [[ $EUID -ne 0 ]]; then echo "run as root (sudo)"; exit 1; fi
if [[ "$APP_DIR" != "/opt/europilot" ]]; then
  echo "warning: repo is at $APP_DIR, not /opt/europilot -- the systemd units"
  echo "         hardcode /opt/europilot. Symlink it or adjust the units."
fi

echo "== packages =="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip osmium-tool curl ca-certificates debian-keyring \
  debian-archive-keyring apt-transport-https

if ! command -v caddy >/dev/null 2>&1; then
  echo "== caddy =="
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y caddy
fi

echo "== service user =="
id -u "$USER" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$USER"

echo "== data + config dirs =="
mkdir -p "$DATA_DIR"/{ndw,osm/work,flags} "$ETC_DIR"
chown -R "$USER:$USER" "$DATA_DIR"
chmod 750 "$ETC_DIR"

echo "== minimal gateway venv =="
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet pynacl "pycapnp==2.1.0"
chown -R "$USER:$USER" "$VENV"

echo "== env file =="
if [[ ! -f "$ETC_DIR/gateway.env" ]]; then
  install -m 600 "$APP_DIR/deploy/gateway/gateway.env.example" "$ETC_DIR/gateway.env"
  echo "   wrote $ETC_DIR/gateway.env -- EDIT IT: set EUROPILOT_OSM_SIGNING_KEY"
else
  echo "   $ETC_DIR/gateway.env exists, left untouched"
fi

echo "== systemd units =="
install -m 644 "$APP_DIR"/deploy/gateway/systemd/*.service /etc/systemd/system/
install -m 644 "$APP_DIR"/deploy/gateway/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload

echo "== caddy config =="
install -m 644 "$APP_DIR/deploy/gateway/Caddyfile" /etc/caddy/Caddyfile

cat <<EOF

bootstrap done. Next (see deploy/gateway/README.md "First light"):
  1. DNS: point app.europilot.eu at this host.
  2. Key:  $VENV/bin/python $APP_DIR/deploy/gateway/gen_signing_key.py pubkey \\
             --seed "\$EUROPILOT_OSM_SIGNING_KEY"   # must print MATCH
           then set the seed in $ETC_DIR/gateway.env
  3. NDW:  place the real shapefile at $DATA_DIR/ndw/msi_shp.zip
  4. Start: systemctl enable --now caddy europilot-ndw
           systemctl start europilot-osm-build   # builds first tile set (~minutes)
           systemctl enable --now europilot-osm europilot-osm-build.timer
EOF
