"""Value types shared by the device overlay and the gateway.

Pure data, no I/O. The device never learns an NDW address; it only ever sees
these objects, handed to it by app.europilot.eu.
"""

from dataclasses import dataclass

# Aspects that close or divert the lane the sign hangs above.
BLOCKING = frozenset({"lane_closed", "lane_closed_ahead", "merge_left", "merge_right"})

# Generous upper bound on a plausible km/h reading. The gateway feed is not
# signed yet, so a speed here is untrusted input on its way into a fixed-width
# capnp field; anything above this is treated as no reading.
SPEED_MAX_KMH = 255


def coerce_speed(value) -> int | None:
    """A trustworthy km/h speed from raw feed JSON, or None.

    A string, a float that won't parse, or an out-of-range number becomes None
    (no reading) rather than flowing on to crash the daemon at capnp assignment
    or to assert a bogus limit. bool is an int subclass but is never a speed.
    """
    if isinstance(value, bool):
        return None
    try:
        s = int(value)
    except (TypeError, ValueError):
        return None
    return s if 0 < s <= SPEED_MAX_KMH else None


@dataclass(frozen=True)
class Sign:
    uuid: str
    road: str
    carriageway: str
    lane: int
    km: float
    wvk_id: str
    bearing: float
    lat: float
    lon: float

    @property
    def gantry_key(self) -> tuple[str, str, float]:
        return (self.road, self.carriageway, round(self.km, 3))

    @classmethod
    def from_json(cls, d: dict) -> "Sign":
        return cls(
            uuid=d["uuid"],
            road=d["road"],
            carriageway=d["carriageway"],
            lane=int(d["lane"]),
            km=float(d["km"]),
            wvk_id=d.get("wvk_id", ""),
            bearing=float(d["bearing"]) % 360.0,
            lat=float(d["lat"]),
            lon=float(d["lon"]),
        )

    def to_json(self) -> dict:
        return {
            "uuid": self.uuid,
            "road": self.road,
            "carriageway": self.carriageway,
            "lane": self.lane,
            "km": self.km,
            "wvk_id": self.wvk_id,
            "bearing": self.bearing,
            "lat": self.lat,
            "lon": self.lon,
        }


@dataclass(frozen=True)
class Display:
    uuid: str
    aspect: str
    speed: int | None
    flashing: bool
    red_ring: bool
    ts_state: str

    @property
    def blocks_lane(self) -> bool:
        return self.aspect in BLOCKING

    @classmethod
    def from_json(cls, uuid: str, d: dict) -> "Display":
        return cls(
            uuid=uuid,
            aspect=d["aspect"],
            speed=coerce_speed(d.get("speed")),
            flashing=bool(d.get("flashing", False)),
            red_ring=bool(d.get("red_ring", False)),
            ts_state=d.get("ts_state", ""),
        )

    def to_json(self) -> dict:
        return {
            "aspect": self.aspect,
            "speed": self.speed,
            "flashing": self.flashing,
            "red_ring": self.red_ring,
            "ts_state": self.ts_state,
        }


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
