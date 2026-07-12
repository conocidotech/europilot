"""Pure value logic for the onroad Europilot HUD.

The raylib renderer (selfdrive/ui/onroad/europilot_hud.py) reads the advisory
messages and draws; this module holds the "what to show" logic so it is testable
without raylib or a running msgq. Advisory-only: when nothing is trustworthy the
helpers return None and the HUD draws nothing.
"""

# euSpeedLimit.source enum name -> short driver-facing trust badge.
_SOURCE_BADGE = {
    "ndwMandatory": "NDW",
    "ndwAdvisory": "NDW",
    "rsaCamera": "CAM",
    "osm": "KAART",
    "timeOfDay": "DAG",
}

# Above this a shown "limit" is almost certainly a bad read; better to show
# nothing than a wrong number (NL tops out at 130).
MAX_PLAUSIBLE_KMH = 130


def sign_number(valid: bool, speed_limit) -> str | None:
    """The number for the speed roundel, or None to draw nothing."""
    if not valid or not isinstance(speed_limit, int) or isinstance(speed_limit, bool):
        return None
    if not 0 < speed_limit <= MAX_PLAUSIBLE_KMH:
        return None
    return str(speed_limit)


def source_badge(source: str) -> str | None:
    """A short badge for where the shown limit came from, or None if unknown."""
    return _SOURCE_BADGE.get(source)


def context_chip(valid: bool, in_residential: bool, cyclestreet: bool) -> str | None:
    """The distinctive NL context pill (fietsstraat / woonwijk), or None.

    Cyclestreet wins: on a fietsstraat the car is the guest, which is the more
    load-bearing thing to surface.
    """
    if not valid:
        return None
    if cyclestreet:
        return "FIETSSTRAAT"
    if in_residential:
        return "WOONWIJK"
    return None
