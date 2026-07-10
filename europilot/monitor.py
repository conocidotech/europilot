#!/usr/bin/env python3
"""Live monitor for the Europilot gateway advisories.

A read-only consumer of GatewayAdvisories, meant for on-device validation of
the gateway pipeline: run it while ``europilotd`` is up and watch the resolved
advisory speed limit, the nearest stop signal, and the next OSM speed change
tick by. It touches no control path.

Usage (on device / in the sim):
    python -m europilot.monitor

Merge-safe: this file is new and does not modify upstream openpilot logic.
"""

from europilot.advisories import GatewayAdvisories

MONITOR_HZ = 2.0


def format_summary(speed_limit, stop_signal, speed_change) -> str:
    """Render one status line from the plain values the accessors return."""
    parts = [f"limit={speed_limit} km/h" if speed_limit else "limit=--"]

    if stop_signal:
        ttc = stop_signal["timeToChange"]
        ttc_str = f"{ttc:.0f}s" if ttc >= 0 else "?"
        parts.append(f"stop@{stop_signal['distance']:.0f}m(ttc {ttc_str})")
    else:
        parts.append("stop=--")

    if speed_change:
        parts.append(f"next {speed_change['speedLimit']}km/h in {speed_change['distance']:.0f}m")
    else:
        parts.append("next=--")

    return " | ".join(parts)


def main(ego_lane: int = 0):
    from openpilot.common.realtime import Ratekeeper

    adv = GatewayAdvisories()
    rk = Ratekeeper(MONITOR_HZ, print_delay_threshold=None)
    while True:
        adv.update()
        print(format_summary(adv.speed_limit(ego_lane), adv.stop_signal(), adv.next_speed_change()),
              flush=True)
        rk.keep_time()


if __name__ == "__main__":
    main()
