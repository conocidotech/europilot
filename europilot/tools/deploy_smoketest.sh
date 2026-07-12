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

# openpilot's Python deps live in a uv-managed venv, so every python/scons call
# below goes through `uv run`, not the system python3 (which lacks capnp/zmq).
# Route uv's cache + temp onto /data: the system partitions (/ and /home) on a
# comma four are small and often nearly full, which makes uv fail with ENOSPC.
export UV_CACHE_DIR="${UV_CACHE_DIR:-/data/uv_cache}"
export TMPDIR="${TMPDIR:-/data/tmp}"
mkdir -p "$UV_CACHE_DIR" "$TMPDIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Phase 0 — preflight & safety
# ---------------------------------------------------------------------------
log "Phase 0: preflight & safety"

[ -d "$OP_DIR/.git" ] || die "no openpilot git checkout at $OP_DIR (is this a comma device?)"
cd "$OP_DIR"
ok "openpilot checkout: $OP_DIR"
uname -m | grep -q 'aarch64' && ok "arch: aarch64 (device)" || warn "arch is not aarch64 — is this really the comma four?"

# Refuse to touch anything while the car is onroad / ignition on.
onroad="$(uv run python - <<'PY' 2>/dev/null || echo unknown
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
uv run python - <<'PY' 2>/dev/null || true
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
NEW_SHA="$(git rev-parse HEAD)"
ok "checked out $BRANCH @ ${NEW_SHA:0:10}"

# Submodules: a comma RELEASE (the device's default) bakes submodules into the
# tree as plain dirs. Switching to this (master-based) branch leaves those dirs
# in place, and `git submodule update` then refuses to clone into them. Clear any
# submodule path that isn't already a live submodule, then init/update.
log "Phase 2b: sync submodules (release bakes them; master needs live clones)"
git submodule sync --recursive >/dev/null 2>&1 || true
git config --file .gitmodules --get-regexp path 2>/dev/null | awk '{print $2}' | while read -r sp; do
  if [ ! -e "$sp/.git" ]; then rm -rf "$sp" && ok "cleared baked submodule dir: $sp"; fi
done
if git submodule update --init --recursive; then
  ok "submodules initialized"
else
  die "submodule update FAILED — see above. Rollback: git checkout -f release-mici"
fi

# Model .onnx are git-LFS (comma's LFS). Best-effort pull so a modeld rebuild has
# real files; non-fatal because our cereal-only change usually doesn't rebuild
# modeld (the shipped model stays up to date).
if command -v git-lfs >/dev/null 2>&1; then
  log "Phase 2c: git lfs pull (best-effort; models)"
  git lfs pull 2>&1 | tail -2 || warn "git lfs pull failed — continuing (modeld may not need it)"
fi

# ---------------------------------------------------------------------------
# Phase 3 — build (the real native cereal/capnp compile)
# ---------------------------------------------------------------------------
log "Phase 3: scons build (this recompiles cereal with the new schema; can take a while)"
if uv run scons -j"$(nproc)"; then
  ok "scons build SUCCEEDED — cereal + affected code compiled natively"
else
  die "scons build FAILED — see output above. Roll back with: git checkout $PREV_REF && uv run scons -j\$(nproc)"
fi

# ---------------------------------------------------------------------------
# Phase 4 — smoke test: do the daemons start and publish?
# ---------------------------------------------------------------------------
log "Phase 4: smoke test — run the daemons offroad and watch the bus (${SMOKE_SECONDS}s)"
# Manager does NOT run these offroad (only_onroad), so running them standalone
# here does not conflict with anything.
declare -a PIDS=()
for mod in europilot.gateway europilot.speed_limit europilot.osm.daemon; do
  uv run python -m "$mod" >"/tmp/${mod//./_}.log" 2>&1 &
  PIDS+=($!)
  ok "started $mod (pid $!)"
done
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT
sleep 2   # let them come up

uv run python - "$SMOKE_SECONDS" <<'PY'
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
# Phase 4b — control + UI integration health (the new, riskiest code)
# ---------------------------------------------------------------------------
log "Phase 4b: control + UI integration — does the new upstream code load & init?"
# Phase 4 only ran the europilot daemons. The riskier changes live in plannerd,
# the longitudinal planner, the Params registry and the UI overlay. A fault there
# fails a SAFETY-CRITICAL process (plannerd) -> processNotRunning -> disengage, so
# validate they load and construct here, offroad, rather than while driving.
uv run python - <<'PY'
import importlib, sys
fail = []

# 1) the opt-in easing param is registered (params_keys.h rebuilt) and OFF.
try:
    from openpilot.common.params import Params
    if Params().get_bool("EuropilotCameraEasing"):
        fail.append("EuropilotCameraEasing is ON -- must be OFF by default for a fresh deploy")
    else:
        print("   \033[1;32m✓\033[0m Params 'EuropilotCameraEasing' registered and OFF")
except Exception as e:
    fail.append(f"Params key not registered (did params rebuild?): {e!r}")

# 2) euSpeedLimit resolves as a service (plannerd now subscribes to it).
try:
    from cereal import messaging
    messaging.SubMaster(["euSpeedLimit"])
    print("   \033[1;32m✓\033[0m euSpeedLimit service resolves (plannerd subscribe)")
except Exception as e:
    fail.append(f"euSpeedLimit service missing: {e!r}")

# 3) the longitudinal planner constructs -- exercises the Params read + init on
#    the safety-critical control process (a crash here would fail plannerd).
try:
    from cereal import car
    from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
    LongitudinalPlanner(car.CarParams.new_message())
    print("   \033[1;32m✓\033[0m LongitudinalPlanner constructs with the easing code path")
except Exception as e:
    fail.append(f"LongitudinalPlanner init failed: {e!r}")

# 4) the onroad HUD overlay module imports (the augmented_road_view hook).
try:
    importlib.import_module("openpilot.selfdrive.ui.onroad.europilot_hud")
    print("   \033[1;32m✓\033[0m onroad HUD module imports")
except Exception as e:
    fail.append(f"europilot_hud import failed: {e!r}")

if fail:
    print("\n   \033[1;31mControl/UI integration problems:\033[0m")
    for f in fail:
        print("     -", f)
    sys.exit(4)
print("\n   control + UI integration OK; easing is OFF by default")
PY
CONTROL_RC=$?

# ---------------------------------------------------------------------------
# Phase 5 — summary
# ---------------------------------------------------------------------------
log "Summary"
if [ "$SMOKE_RC" -eq 0 ] && [ "$CONTROL_RC" -eq 0 ]; then
  ok "BUILD + PUBLISH + control/UI integration all green for ${BRANCH} @ ${NEW_SHA:0:10}"
else
  [ "$SMOKE_RC" -ne 0 ] && warn "one or more daemons did not publish — check /tmp/europilot_*.log"
  [ "$CONTROL_RC" -ne 0 ] && warn "control/UI integration check FAILED — see Phase 4b above (do NOT go onroad)"
fi
cat <<EOF

   Next (still required before trusting it on the road):
     - onroad with the car: confirm RSA CAN bus + TSGN codes, and that euSpeedLimit
       shows sane values from real signs (watch it live by re-running this script's
       Phase-4 SubMaster loop while onroad)
     - deploy the OSM gateway + set EUROPILOT_OSM_SIGNING_KEY, then confirm euMapAdvisory
       goes valid with real tiles
     - keep the camera easing OFF for the first onroad drives; enable it only after
       the advisories look sane, on a quiet road, supervised:
         uv run python -c "from openpilot.common.params import Params; Params().put_bool('EuropilotCameraEasing', True)"  # then reboot
     - supervised on-road, hand ready to take over

   Security: FORK_REMOTE (with its token) is now stored in $OP_DIR/.git/config.
   If you used a short-lived token it will expire; otherwise scrub it:
     git remote remove $REMOTE_NAME

   Rollback (restore the device to how it was):
     cd $OP_DIR && git checkout $PREV_REF && uv run scons -j\$(nproc)
     uv run python -c "from openpilot.common.params import Params; Params().put_bool('DisableUpdates', False)"
     sudo reboot

EOF
if [ "$SMOKE_RC" -eq 0 ] && [ "$CONTROL_RC" -eq 0 ]; then
  exit 0
else
  exit 1
fi
