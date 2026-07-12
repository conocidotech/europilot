"""Read ways, tagged nodes and relations from an .osm XML extract.

Production ingest is osmium over the canonical .osm.pbf (tags-filter -> extract
--bbox -> add-locations-to-ways, then daily apply-changes); that front-end is a
later slice and needs the osmium toolchain. This module reads the equivalent
.osm XML, which is the same data model, so the derivation core and its tests
don't depend on the PBF toolchain.

The spatial joins need more than slice 1 did: node tags (traffic_calming,
highway=crossing/traffic_signals) and relations (landuse=residential is often a
multipolygon). parse_full() returns all of it; parse() stays a thin wrapper for
the tile-derivation core.
"""

from dataclasses import dataclass, field
from xml.etree import ElementTree


@dataclass(frozen=True)
class Way:
    id: int
    tags: dict
    node_ids: list


@dataclass(frozen=True)
class Member:
    type: str   # "way" | "node" | "relation"
    ref: int
    role: str   # "outer" | "inner" | ...


@dataclass(frozen=True)
class Relation:
    id: int
    tags: dict
    members: list


@dataclass
class OsmData:
    nodes: dict = field(default_factory=dict)        # id -> (lat, lon)
    node_tags: dict = field(default_factory=dict)    # id -> tags (tagged nodes only)
    ways: list = field(default_factory=list)         # list[Way]
    relations: list = field(default_factory=list)    # list[Relation]

    def coords(self, node_ids: list) -> list:
        return [self.nodes[n] for n in node_ids if n in self.nodes]


def parse_full(xml_bytes: bytes) -> OsmData:
    root = ElementTree.fromstring(xml_bytes)
    data = OsmData()

    for n in root.iter("node"):
        nid = int(n.attrib["id"])
        data.nodes[nid] = (float(n.attrib["lat"]), float(n.attrib["lon"]))
        tags = {t.attrib["k"]: t.attrib["v"] for t in n.findall("tag")}
        if tags:
            data.node_tags[nid] = tags

    for w in root.iter("way"):
        data.ways.append(Way(
            id=int(w.attrib["id"]),
            tags={t.attrib["k"]: t.attrib["v"] for t in w.findall("tag")},
            node_ids=[int(nd.attrib["ref"]) for nd in w.findall("nd")],
        ))

    for r in root.iter("relation"):
        data.relations.append(Relation(
            id=int(r.attrib["id"]),
            tags={t.attrib["k"]: t.attrib["v"] for t in r.findall("tag")},
            members=[Member(m.attrib["type"], int(m.attrib["ref"]), m.attrib.get("role", ""))
                     for m in r.findall("member")],
        ))

    return data


def parse(xml_bytes: bytes) -> tuple[dict[int, tuple[float, float]], list[Way]]:
    """(nodes, ways) -- the subset the tile-derivation core needs."""
    data = parse_full(xml_bytes)
    return data.nodes, data.ways
