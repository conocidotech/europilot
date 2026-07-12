#!/usr/bin/env bash
# Refresh the static NDW MSI shapefile from NDW open data.
#
# The live sign *states* are polled inside europilot-ndw.service every 60s, but
# the sign *locations* (the shapefile) are read once at start. NDW republishes
# the shapefile as gantries are added/removed, so this pulls the latest, checks
# it actually parses into a plausible sign set, swaps it in atomically, and only
# then restarts the service. A truncated or broken download never replaces a
# working file, and the service keeps serving the old set until the restart.
set -euo pipefail

URL="https://opendata.ndw.nu/ndw_msi_shapefiles_latest.zip"
DEST="${EUROPILOT_NDW_SHAPEFILE:-/var/lib/europilot/ndw/msi_shp.zip}"
GWDIR="/opt/europilot-gateway"
VENV="${GWDIR}/.venv-gateway/bin/python"
MIN_SIGNS="${EUROPILOT_NDW_MIN_SIGNS:-1000}"

tmp="$(mktemp "${DEST}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

curl -fSL --max-time 180 -o "$tmp" "$URL"

# Validate: the new file must parse and yield a sane number of signs. Guards
# against an HTML error page, an empty zip, or a schema change breaking parsing.
n="$(cd "$GWDIR" && "$VENV" - "$tmp" <<'PY'
import sys
from gateway.ndw.static_index import load_signs
print(len(load_signs(sys.argv[1])))
PY
)"
if [ "$n" -lt "$MIN_SIGNS" ]; then
    echo "refusing refresh: only $n signs parsed (< $MIN_SIGNS)" >&2
    exit 1
fi

chown europilot:europilot "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$DEST"
trap - EXIT

echo "NDW shapefile refreshed: $n signs -> $DEST"
systemctl restart europilot-ndw.service
