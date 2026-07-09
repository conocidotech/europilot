"""Match a vehicle pose to the matrix gantry that governs it.

A matrix sign faces oncoming traffic, so its bearing is the direction of travel
of the lane it hangs above. Given a pose we keep signs whose bearing agrees with
our heading, project them onto the heading axis, and split them into the gantry
we last passed (which governs us now) and the next one ahead (which we can use
to slow down early).
"""

import math
from collections import defaultdict
from dataclasses import dataclass

from .feed import Display
from .static_index import Sign

EARTH_RADIUS_M = 6_371_000.0

CELL_DEG = 0.02
SEARCH_RADIUS_M = 2000.0
MAX_BEARING_DELTA_DEG = 40.0
MAX_CROSS_TRACK_M = 25.0


@dataclass(frozen=True)
class Gantry:
    road: str
    carriageway: str
    km: float
    wvk_id: str
    distance_m: float
    lanes: dict[int, Display]

    def _speeds(self, red_ring: bool) -> list[int]:
        return sorted(
            d.speed
            for d in self.lanes.values()
            if d.speed is not None and not d.blocks_lane and d.red_ring is red_ring
        )

    @property
    def mandatory_speed(self) -> int | None:
        """Lowest red-ringed speed: legally binding."""
        speeds = self._speeds(red_ring=True)
        return speeds[0] if speeds else None

    @property
    def advisory_speed(self) -> int | None:
        """Lowest speed shown without a red ring: advice, not a limit."""
        speeds = self._speeds(red_ring=False)
        return speeds[0] if speeds else None

    @property
    def target_speed(self) -> int | None:
        """What a controller should aim for: the lowest speed shown either way."""
        speeds = [s for s in (self.mandatory_speed, self.advisory_speed) if s is not None]
        return min(speeds) if speeds else None

    @property
    def flashing(self) -> bool:
        return any(d.flashing for d in self.lanes.values())

    @property
    def closed_lanes(self) -> list[int]:
        return sorted(lane for lane, d in self.lanes.items() if d.blocks_lane)


@dataclass(frozen=True)
class Match:
    governing: Gantry | None
    upcoming: Gantry | None


def _bearing_delta(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


class GantryIndex:
    """Grid index over signs, keyed by coarse lat/lon cell."""

    def __init__(self, signs: list[Sign]):
        self._cells: dict[tuple[int, int], list[Sign]] = defaultdict(list)
        for sign in signs:
            self._cells[self._cell(sign.lat, sign.lon)].append(sign)

    @staticmethod
    def _cell(lat: float, lon: float) -> tuple[int, int]:
        return (int(math.floor(lat / CELL_DEG)), int(math.floor(lon / CELL_DEG)))

    def _nearby(self, lat: float, lon: float) -> list[Sign]:
        lat_span = SEARCH_RADIUS_M / (EARTH_RADIUS_M * math.pi / 180.0)
        lon_span = lat_span / max(math.cos(math.radians(lat)), 1e-6)
        lo = self._cell(lat - lat_span, lon - lon_span)
        hi = self._cell(lat + lat_span, lon + lon_span)

        out = []
        for i in range(lo[0], hi[0] + 1):
            for j in range(lo[1], hi[1] + 1):
                out.extend(self._cells.get((i, j), ()))
        return out

    def match(self, lat: float, lon: float, heading: float, states: dict[str, Display]) -> Match:
        cos_lat = math.cos(math.radians(lat))
        heading_rad = math.radians(heading)
        # East/north unit vector pointing where the vehicle is going.
        h_east, h_north = math.sin(heading_rad), math.cos(heading_rad)

        gantries: dict[tuple, dict] = {}
        for sign in self._nearby(lat, lon):
            if _bearing_delta(sign.bearing, heading) > MAX_BEARING_DELTA_DEG:
                continue

            d_north = math.radians(sign.lat - lat) * EARTH_RADIUS_M
            d_east = math.radians(sign.lon - lon) * EARTH_RADIUS_M * cos_lat

            along = d_east * h_east + d_north * h_north
            cross = -d_east * h_north + d_north * h_east
            if abs(along) > SEARCH_RADIUS_M or abs(cross) > MAX_CROSS_TRACK_M:
                continue

            display = states.get(sign.uuid)
            if display is None:
                continue

            entry = gantries.setdefault(
                sign.gantry_key,
                {"sign": sign, "along": along, "lanes": {}},
            )
            entry["lanes"][sign.lane] = display
            if abs(along) < abs(entry["along"]):
                entry["along"] = along

        def build(entry: dict) -> Gantry:
            sign: Sign = entry["sign"]
            return Gantry(
                road=sign.road,
                carriageway=sign.carriageway,
                km=sign.km,
                wvk_id=sign.wvk_id,
                distance_m=round(entry["along"], 1),
                lanes=entry["lanes"],
            )

        behind = [e for e in gantries.values() if e["along"] <= 0.0]
        ahead = [e for e in gantries.values() if e["along"] > 0.0]

        governing = build(max(behind, key=lambda e: e["along"])) if behind else None
        upcoming = build(min(ahead, key=lambda e: e["along"])) if ahead else None
        return Match(governing=governing, upcoming=upcoming)
