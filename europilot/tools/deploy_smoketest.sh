#!/usr/bin/env bash
#
# europilot_deploy_smoketest.sh — deploy the europilot branch to a comma four
# over SSH and smoke-test it, WITHOUT flashing an image.
#
# Runs ON the comma four (SSH in first). It: guards that the car is offroad,
# records the current state for rollback, fetches + checks out the branch,
# pauses the auto-updater for the session, builds with scons (the real larch64
# build — the definitive cereal/capnp compile check), then runs the three
# europilot daemons offroad for a few seconds and confirms they publish
# euNdwMatrixSigns / euSpeedLimit / euMapAdvisory on the msgq bus.
#
# It validates BUILD + START + PUBLISH only. It does NOT validate values (RSA
# CAN codes, real limits) — that needs the car onroad with CAN. Advisory-only
# software, but still: park the car, do this offroad, keep a hand on the wheel
# when you eventually go onroad.
#
# Usage (on the device):
#   FORK_REMOTE="https://<token>@github.com/conocidotech/europilot.git" \
#   BRANCH=europilot-ndw-ci \
#   CONFIRM=yes \
#   bash europilot_deploy_smoketest.sh
#
# Nothing happens past preflight unless CONFIRM=yes. Re-run is safe.

set -euo pipefail

OP_DIR="${OP_DIR:-/data/openpilot}"
BRANCH="${BRANCH:-europilot-ndw-ci}"
FORK_REMOTE="${FORK_REMOTE:-}"       # git URL to the fork (with auth), required to fetch
REMOTE_NAME="${REMOTE_NAME:-europilot}"
SMOKE_SECONDS="${SMOKE_SECONDS:-12}"
CONFIRM="${CONFIRM:-no}"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '   \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '   \033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31mABORT:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Phase 0 — preflight & safety
# ---------------------------------------------------------------------------
log "Phase 0: preflight & safety"

[ -d "$OP_DIR/.git" ] || die "no openpilot git checkout at $OP_DIR (is this a comma device?)"
cd "$OP_DIR"
ok "openpilot checkout: $OP_DIR"
uname -m | grep -q 'aarch64' && ok "arch: aarch64 (device)" || warn "arch is not aarch64 — is this really the comma four?"

# Refuse to touch anything while the car is onroad / ignition on.
onroad="$(python3 - <<'PY' 2>/dev/null || echo unknown
from openpilot.common.params import Params
p = Params()
print("onroad" if p.get_bool("IsOnroad") else "offroad")
PY
)"
case "$onroad" in
  offroad) ok "car is OFFROAD" ;;
  onroad)  die "car is ONROAD — park it and turn the ignition off before deploying" ;;
  *)       warn "could not read onroad state; make ABSOLUTELY sure the car is parked and off" ;;
esac

# Record current state for rollback.
PREV_REF="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo DETACHED)"
PREV_SHA="$(git rev-parse HEAD)"
ok "current openpilot: $PREV_REF @ ${PREV_SHA:0:10}"
printf '%s\n' "$PREV_REF $PREV_SHA" > /tmp/europilot_rollback.txt
ok "rollback point saved to /tmp/europilot_rollback.txt"

if [ -n "$(git status --porcelain)" ]; then
  warn "working tree has local modifications:"
  git status --porcelain | sed 's/^/       /'
  die "refusing to clobber local changes — commit/stash them on the device first"
fi
ok "working tree clean"

if [ "$CONFIRM" != "yes" ]; then
  log "PREFLIGHT ONLY (CONFIRM is not 'yes')."
  cat <<EOF

   This run stopped before making any changes. When you're ready, re-run with:

     FORK_REMOTE="https://<token>@github.com/conocidotech/europilot.git" \\
     BRANCH=$BRANCH CONFIRM=yes bash $0

   It will then: fetch $BRANCH, pause the updater, scons build, and smoke-test.
EOF
  exit 0
fi

[ -n "$FORK_REMOTE" ] || die "set FORK_REMOTE to the fork's git URL (with auth) to fetch $BRANCH"

# ---------------------------------------------------------------------------
# Phase 1 — pause the auto-updater for this session
# ---------------------------------------------------------------------------
log "Phase 1: pause the auto-updater (so it can't overwrite the branch)"
# Best-effort: stop the running updater and set the disable param. Reversible.
python3 - <<'PY' 2>/dev/null || true
from openpilot.common.params import Params
Params().put_bool("DisableUpdates", True)
print("   set Params DisableUpdates=True")
PY
if pgrep -f 'system.updated|selfdrive.updated|updated.py' >/dev/null 2>&1; then
  pkill -f 'system.updated|selfdrive.updated|updated.py' 2>/dev/null || true
  ok "stopped the running updater process"
else
  ok "no updater process running"
fi
warn "re-enable it later with: Params().put_bool('DisableUpdates', False)  (and reboot)"

# ---------------------------------------------------------------------------
# Phase 2 — fetch & checkout the branch
# ---------------------------------------------------------------------------
log "Phase 2: fetch & checkout $BRANCH"
if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  git remote set-url "$REMOTE_NAME" "$FORK_REMOTE"
else
  git remote add "$REMOTE_NAME" "$FORK_REMOTE"
fi
ok "remote '$REMOTE_NAME' -> (fork)"
git fetch --depth=1 "$REMOTE_NAME" "$BRANCH"
git checkout -B "$BRANCH" "$REMOTE_NAME/$BRANCH"
git submodule update --init --recursive
NEW_SHA="$(git rev-parse HEAD)"
ok "checked out $BRANCH @ ${NEW_SHA:0:10}"

# ---------------------------------------------------------------------------
# Phase 3 — build (the real native cereal/capnp compile)
# ---------------------------------------------------------------------------
log "Phase 3: scons build (this recompiles cereal with the new schema; can take a while)"
if scons -j"$(nproc)"; then
  ok "scons build SUCCEEDED — cereal + affected code compiled natively"
else
  die "scons build FAILED — see output above. Roll back with: git checkout $PREV_REF && scons -j\$(nproc)"
fi

# ---------------------------------------------------------------------------
# Phase 4 — smoke test: do the daemons start and publish?
# ---------------------------------------------------------------------------
log "Phase 4: smoke test — run the daemons offroad and watch the bus (${SMOKE_SECONDS}s)"
# Manager does NOT run these offroad (only_onroad), so running them standalone
# here does not conflict with anything.
declare -a PIDS=()
for mod in europilot.gateway europilot.speed_limit europilot.osm.daemon; do
  python3 -m "$mod" >"/tmp/${mod//./_}.log" 2>&1 &
  PIDS+=($!)
  ok "started $mod (pid $!)"
done
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT
sleep 2   # let them come up

python3 - "$SMOKE_SECONDS" <<'PY'
import sys, time
from cereal import messaging
window = int(sys.argv[1])
services = ["euNdwMatrixSigns", "euSpeedLimit", "euMapAdvisory"]
sm = messaging.SubMaster(services)
seen = {s: 0 for s in services}
last = {}
t0 = time.monotonic()
while time.monotonic() - t0 < window:
    sm.update(100)
    for s in services:
        if sm.updated[s]:
            seen[s] += 1
            last[s] = sm[s]
print("\n   --- published in %ds ---" % window)
ok = True
for s in services:
    if seen[s] > 0:
        m = last[s]
        extra = ""
        if s == "euSpeedLimit":   extra = f"  speedLimit={m.speedLimit} source={m.source} valid={m.valid}"
        if s == "euMapAdvisory":  extra = f"  valid={m.valid} speedLimit={m.speedLimit} roadClass='{m.roadClass}'"
        if s == "euNdwMatrixSigns": extra = f"  valid={m.valid}"
        print(f"   \033[1;32m✓\033[0m {s}: {seen[s]} msgs{extra}")
    else:
        print(f"   \033[1;31m✗\033[0m {s}: NO messages")
        ok = False
print("\n   NOTE: valid=false / source=none is EXPECTED offroad (no CAN, no GPS fix,")
print("         and the OSM gateway may not serve tiles yet). This test checks that")
print("         the daemons START and PUBLISH — not the values.")
sys.exit(0 if ok else 3)
PY
SMOKE_RC=$?
cleanup; trap - EXIT

# ---------------------------------------------------------------------------
# Phase 5 — summary
# ---------------------------------------------------------------------------
log "Summary"
if [ "$SMOKE_RC" -eq 0 ]; then
  ok "BUILD + START + PUBLISH all green for ${BRANCH} @ ${NEW_SHA:0:10}"
else
  warn "one or more daemons did not publish — check /tmp/europilot_*.log"
fi
cat <<EOF

   Next (still required before trusting it on the road):
     - onroad with the car: confirm RSA CAN bus + TSGN codes, and that euSpeedLimit
       shows sane values from real signs (watch it live by re-running this script's
       Phase-4 SubMaster loop while onroad)
     - deploy the OSM gateway + set EUROPILOT_OSM_SIGNING_KEY, then confirm euMapAdvisory
       goes valid with real tiles
     - supervised on-road, hand ready to take over

   Security: FORK_REMOTE (with its token) is now stored in $OP_DIR/.git/config.
   If you used a short-lived token it will expire; otherwise scrub it:
     git remote remove $REMOTE_NAME

   Rollback (restore the device to how it was):
     cd $OP_DIR && git checkout $PREV_REF && scons -j\$(nproc)
     python3 -c "from openpilot.common.params import Params; Params().put_bool('DisableUpdates', False)"
     sudo reboot

EOF
exit "$SMOKE_RC"
