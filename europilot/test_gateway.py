"""Tests for europilotd's Gantry -> capnp fill.

The fetch/match half is europilot.ndw's (covered by its own tests); what is new
here is writing a matched Gantry onto the bus message. A fake builder stands in
for the capnp struct so this runs without a compiled cereal -- it records what
was written, which is exactly what we want to assert.
"""

from europilot.gateway import fill_gantry
from europilot.ndw.types import Display, Gantry


class FakeBuilder:
    """Records attribute writes and list inits like a capnp struct builder."""

    def __init__(self):
        object.__setattr__(self, "_written", {})

    def __setattr__(self, name, value):
        self._written[name] = value

    def init(self, name, count):
        lst = [0] * count
        self._written[name] = lst
        return lst

    def __getitem__(self, name):
        return self._written[name]

    def __contains__(self, name):
        return name in self._written


def display(aspect="speedlimit", speed=None, red_ring=False, flashing=False):
    return Display(uuid="u", aspect=aspect, speed=speed, flashing=flashing,
                   red_ring=red_ring, ts_state="")


def gantry(lanes, distance_m=120.0, road="A2", carriageway="R"):
    return Gantry(road=road, carriageway=carriageway, km=1.0, wvk_id="w",
                  distance_m=distance_m, lanes=lanes)


class TestFillGantry:
    def test_miss_marks_invalid(self):
        b = FakeBuilder()
        fill_gantry(b, None)
        assert b["valid"] is False
        # nothing else should be written for a miss
        assert "targetSpeed" not in b

    def test_mandatory_and_advisory_stay_apart(self):
        # lane 1 shows a red-ringed 70 (binding), lane 2 an un-ringed 90 (advice)
        g = gantry({
            1: display(speed=70, red_ring=True),
            2: display(speed=90, red_ring=False),
        })
        b = FakeBuilder()
        fill_gantry(b, g)
        assert b["valid"] is True
        assert b["mandatorySpeed"] == 70
        assert b["advisorySpeed"] == 90
        assert b["targetSpeed"] == 70   # lower of the two

    def test_absent_speeds_become_minus_one(self):
        g = gantry({1: display(aspect="lane_closed", speed=None)})
        b = FakeBuilder()
        fill_gantry(b, g)
        assert b["mandatorySpeed"] == -1
        assert b["advisorySpeed"] == -1
        assert b["targetSpeed"] == -1

    def test_closed_lanes_and_flashing(self):
        g = gantry({
            1: display(aspect="lane_closed"),
            2: display(speed=50, red_ring=True, flashing=True),
            3: display(aspect="merge_left"),
        })
        b = FakeBuilder()
        fill_gantry(b, g)
        assert b["closedLanes"] == [1, 3]
        assert b["flashing"] is True

    def test_no_closed_lanes_writes_empty_list(self):
        b = FakeBuilder()
        fill_gantry(b, gantry({1: display(speed=100, red_ring=True)}))
        assert b["closedLanes"] == []
        assert b["flashing"] is False

    def test_geometry_and_identity_carried_through(self):
        b = FakeBuilder()
        fill_gantry(b, gantry({1: display(speed=100, red_ring=True)},
                              distance_m=-45.0, road="A12", carriageway="L"))
        assert b["distance"] == -45.0   # negative: already passed
        assert b["road"] == "A12"
        assert b["carriageway"] == "L"
