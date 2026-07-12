"""Device-side value types for the OSM consumer.

Road is one road from a decoded tile, in device-friendly units (lat/lon degrees,
None for unknown rather than the wire's 0/enum sentinels). Advisory is what the
matcher hands the rest of the stack for the current pose.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Road:
    id: int
    road_class: str
    maxspeed: int | None        # km/h, None if unknown
    oneway: bool
    lanes: int | None
    name: str
    points: list[tuple[float, float]]   # (lat, lon) degrees, in order
    in_residential: bool
    calming_per_km: float
    crossing_per_km: float
    cycleway_left: str          # "unknown" | "absent" | "present"
    cycleway_right: str
    cyclestreet: bool
    residential_score: float
    comfort_speed: int | None   # advisory km/h, None to defer to posted


@dataclass(frozen=True)
class Advisory:
    """The matched-road hint for one pose. valid=False means no usable match."""
    valid: bool
    road_id: int | None = None
    road_class: str = ""
    name: str = ""
    speed_limit: int | None = None     # OSM posted limit -> euSpeedLimit osm source
    comfort_speed: int | None = None
    in_residential: bool = False
    cycleway_left: str = "unknown"
    cycleway_right: str = "unknown"
    cyclestreet: bool = False
    distance_m: float | None = None    # lateral distance to the matched road

    @staticmethod
    def none() -> "Advisory":
        return Advisory(valid=False)
