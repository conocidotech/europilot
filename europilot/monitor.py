#!/usr/bin/env python3
"""Live monitor for the Europilot NDW matrix-sign advisories.

A read-only consumer of GatewayAdvisories, for on-device validation of the
gateway pipeline: run it while ``europilotd`` is up and watch the governing
gantry, the next one ahead, and any closed lanes tick by. Touches no control
path.

Usage (on device / in the sim):
    python -m europilot.monitor

Merge-safe: this file is new and does not modify upstream openpilot logic.
"""

from europilot.advisories import GatewayAdvisories

MONITOR_HZ = 2.0


def format_summary(mandatory, advisory, upcoming, lanes, flashing) -> str:
    """Render one status line from the plain values the accessors return."""
    if mandatory is not None:
        speed = f"{mandatory} km/h (mandatory)"
    elif advisory is not None:
        speed = f"{advisory} km/h (advisory)"
    else:
        speed = "--"
    parts = [f"now={speed}"]

    if upcoming is not None:
        limit, distance = upcoming
        parts.append(f"next={limit}km/h in {distance:.0f}m")
    else:
        parts.append("next=--")

    if lanes:
        parts.append("closed=" + ",".join(str(x) for x in lanes))
    if flashing:
        parts.append("FLASHING")

    return " | ".join(parts)


def main():
    from openpilot.common.realtime import Ratekeeper

    adv = GatewayAdvisories()
    rk = Ratekeeper(MONITOR_HZ, print_delay_threshold=None)
    while True:
        adv.update()
        print(format_summary(adv.mandatory_speed(), adv.advisory_speed(),
                             adv.upcoming_target_speed(), adv.closed_lanes(),
                             adv.is_flashing()), flush=True)
        rk.keep_time()


if __name__ == "__main__":
    main()
