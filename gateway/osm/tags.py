"""Turn a way's OSM tags into the derived attributes a tile carries.

Slice 1 covers the tag-based attributes -- road class, speed limit, oneway,
lanes -- which is what feeds the device's speed-limit fusion. The attributes
that need a spatial join (landuse=residential membership, parallel cycleways,
residential_score) are a later slice; they can't be read off a single way's
tags.

All pure functions. "Unknown" is always None, never a guessed default: per the
design rule, a missing tag never silently becomes a value.
"""

# Drivable highway classes we keep. Cycleways/footways and non-road values are
# dropped here (a cycleway is relevant only as geometry next to a road, which is
# the spatial-join slice). Link roads map to their parent class.
ROAD_CLASSES = {
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "living_street", "service",
}
_LINK_PARENT = {
    "motorway_link": "motorway", "trunk_link": "trunk", "primary_link": "primary",
    "secondary_link": "secondary", "tertiary_link": "tertiary",
}

# NL implicit limits by legal road category (maxspeed=NL:urban etc.).
_NL_IMPLICIT = {"urban": 50, "rural": 80}


def road_class(tags: dict) -> str | None:
    """Canonical road class, or None if this way isn't a kept drivable road."""
    hw = tags.get("highway")
    if hw in ROAD_CLASSES:
        return hw
    return _LINK_PARENT.get(hw)


def _zone_number(value: str) -> int | None:
    """Pull the km/h number out of an NL zone value like 'NL:zone30' / 'NL:30'."""
    tail = value.rsplit(":", 1)[-1]           # 'NL:zone30' -> 'zone30'
    digits = "".join(ch for ch in tail if ch.isdigit())
    return int(digits) if digits else None


def maxspeed(tags: dict) -> int | None:
    """Speed limit in km/h, or None if not determinable from tags.

    Handles a plain number, NL zone tags (zone:maxspeed / maxspeed:type /
    maxspeed=NL:zoneNN), and the NL implicit urban/rural categories. NL:motorway
    is deliberately left unknown -- its implicit limit is time/condition
    dependent and the live matrix sign governs it anyway.
    """
    raw = str(tags.get("maxspeed", "")).strip()
    if raw.isdigit():
        return int(raw)

    for key in ("zone:maxspeed", "maxspeed:type", "maxspeed"):
        val = str(tags.get(key, "")).strip().lower()
        if not val:
            continue
        # zone:maxspeed is always a zone limit, whatever its value shape
        # (NL:30, 30, NL:zone30); other keys only when the value says "zone".
        if key == "zone:maxspeed" or "zone" in val:
            n = _zone_number(val)
            if n is not None:
                return n
            continue
        category = val.rsplit(":", 1)[-1]
        if category in _NL_IMPLICIT:
            return _NL_IMPLICIT[category]
    return None


def oneway(tags: dict) -> bool:
    """True when traffic runs one way. Motorways are implicitly oneway in OSM."""
    val = str(tags.get("oneway", "")).strip().lower()
    if val in ("yes", "true", "1", "-1"):
        return True
    if val in ("no", "false", "0"):
        return False
    return tags.get("highway") in ("motorway", "motorway_link")


def lanes(tags: dict) -> int | None:
    raw = str(tags.get("lanes", "")).strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def derive_way(tags: dict) -> dict | None:
    """All tag-derived attributes for a way, or None if it isn't a kept road."""
    cls = road_class(tags)
    if cls is None:
        return None
    return {
        "roadClass": cls,
        "maxspeed": maxspeed(tags),      # km/h or None
        "oneway": oneway(tags),
        "lanes": lanes(tags),            # int or None
        "name": tags.get("name") or None,
    }
