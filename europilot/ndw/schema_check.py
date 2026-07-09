"""Canary against the live NDW feed.

The unit tests run against a checked-in snapshot, so they keep passing even if
NDW changes the feed tomorrow. The parser would then silently return nothing and
the car would quietly lose its dynamic speed limit. This script fetches the real
feed and fails loudly on drift. Run it on a schedule, not on pull requests.
"""

import collections
import gzip
import io
import sys
import urllib.request
import zipfile

from europilot.ndw.feed import MSI_URL, parse
from europilot.ndw.static_index import _read_dbf

SHAPEFILE_URL = "https://opendata.ndw.nu/ndw_msi_shapefiles_latest.zip"

KNOWN_ASPECTS = {
    "blank",
    "speedlimit",
    "lane_closed",
    "lane_open",
    "lane_closed_ahead",
    "restriction_end",
    "merge_left",
    "merge_right",
}
PLAUSIBLE_SPEEDS = {30, 50, 70, 80, 90, 100, 120, 130}
REQUIRED_DBF_FIELDS = {"uuid", "road", "lane", "km", "wegvak", "bearing"}

MIN_SIGNS = 15_000
MIN_DISPLAYS = 15_000


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def check_feed(failures: list[str]) -> None:
    states = parse(gzip.decompress(_get(MSI_URL)))
    print(f"feed: {len(states)} display events")

    if len(states) < MIN_DISPLAYS:
        failures.append(f"only {len(states)} display events, expected >= {MIN_DISPLAYS}")

    aspects = collections.Counter(d.aspect for d in states.values())
    print("      aspects: " + ", ".join(f"{a}={n}" for a, n in aspects.most_common()))

    unknown = set(aspects) - KNOWN_ASPECTS
    if unknown:
        failures.append(f"unknown display aspects, parser needs updating: {sorted(unknown)}")

    speeds = [d.speed for d in states.values() if d.aspect == "speedlimit"]
    if not speeds:
        failures.append("no speedlimit aspects at all — feed shape may have changed")
    if any(s is None for s in speeds):
        failures.append("speedlimit aspect without a parsable value")

    odd = sorted({s for s in speeds if s is not None} - PLAUSIBLE_SPEEDS)
    if odd:
        failures.append(f"implausible speed values: {odd}")

    ringed = sum(1 for d in states.values() if d.aspect == "speedlimit" and d.red_ring)
    print(f"      speedlimits: {len(speeds)} of which {ringed} red-ringed (mandatory)")
    if speeds and ringed == 0:
        failures.append("no red_ring speedlimits — the mandatory/advisory split may have changed")


def check_shapefile(failures: list[str]) -> None:
    with zipfile.ZipFile(io.BytesIO(_get(SHAPEFILE_URL))) as z:
        rows = _read_dbf(z.read("MSI/shapes.dbf"))
    print(f"shapefile: {len(rows)} signs")

    if len(rows) < MIN_SIGNS:
        failures.append(f"only {len(rows)} signs, expected >= {MIN_SIGNS}")

    fields = set(rows[0]) if rows else set()
    # The dbf format truncates names to 10 chars, so carriageway lands as carriagew0.
    missing = REQUIRED_DBF_FIELDS - fields
    if missing:
        failures.append(f"shapefile is missing fields {sorted(missing)}, found {sorted(fields)}")


def main() -> int:
    failures: list[str] = []
    for check in (check_feed, check_shapefile):
        try:
            check(failures)
        except Exception as e:
            failures.append(f"{check.__name__} raised {type(e).__name__}: {e}")

    print()
    if failures:
        print("NDW SCHEMA DRIFT DETECTED")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("NDW feed and shapefile match what the parser expects.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
