#!/usr/bin/env python3
"""Decode the car's Road Sign Assist speed limit from raw camera CAN.

Toyota/Lexus TSS2 cars read speed-limit signs with their own front camera (RSA,
Road Sign Assist) and broadcast the result on the camera CAN bus. This module
is the pure decoder; europilot/speed_limit.py reads the CAN and fuses this with
the other sources.

Why we parse the CAN raw instead of via a DBC: comma's production Toyota DBCs
don't include the RSA message at all (the signals live only in the reference
DBC), so there is no CANParser schema to lean on. The two messages are
byte-aligned, so a raw read is simple and needs no DBC. Decoding semantics match
the reverse-engineering in sunnypilot/opendbc#289.

Merge-safe: this file is new and does not modify upstream openpilot logic.
"""

# Camera-CAN messages carrying Road Sign Assist. In comma's reference DBC these
# are FCM1S10 / FCM1S11 (the FCM = front camera module); sunnypilot calls them
# RSA1 / RSA2. Both messages are 8 bytes.
RSA1_ADDR = 0x489
RSA2_ADDR = 0x48A

# Traffic-sign-type codes for a speed-limit sign (signal TSGN1). From
# sunnypilot/opendbc#289's TRAFFIC_SIGNAL_MAP:
TSGN_SPEED_LIMIT_KPH = 1    # round speed-limit sign, value in km/h
TSGN_SPEED_LIMIT_MPH = 36   # round speed-limit sign, value in mph

# Plausibility bounds on the raw sign value, matching the reference logic.
MAX_KPH = 200
MAX_MPH = 120

MPH_TO_KPH = 1.609344


def parse_rsa1(data: bytes) -> tuple[int, int] | None:
    """(tsgn1, spdval1) from an RSA1 payload, or None if it's malformed.

    Byte-aligned per the DBC: TSGN1 is bit 7|8@0+ (byte 0), SPDVAL1 is bit
    23|8@0+ (byte 2).
    """
    if data is None or len(data) < 8:
        return None
    return data[0], data[2]


def resolve_speed_limit(tsgn1: int, spdval1: int) -> int | None:
    """Advisory speed limit in km/h from the RSA1 sign type + value, or None.

    A km/h speed-limit sign (tsgn1 == 1) carries the limit directly; an mph sign
    (tsgn1 == 36) is converted. Any other sign type, or an out-of-range value,
    means "no speed limit shown".
    """
    if tsgn1 == TSGN_SPEED_LIMIT_KPH and 0 < spdval1 <= MAX_KPH:
        return spdval1
    if tsgn1 == TSGN_SPEED_LIMIT_MPH and 0 < spdval1 <= MAX_MPH:
        return round(spdval1 * MPH_TO_KPH)
    return None


def speed_limit_from_can(data: bytes) -> int | None:
    """Full RSA1 payload -> advisory km/h speed limit, or None."""
    parsed = parse_rsa1(data)
    if parsed is None:
        return None
    return resolve_speed_limit(*parsed)
