"""Read ways + node coordinates from an .osm XML extract.

Production ingest is osmium over the canonical .osm.pbf (tags-filter -> extract
--bbox -> add-locations-to-ways, then daily apply-changes); that front-end is a
later slice and needs the osmium toolchain. This module reads the equivalent
.osm XML, which is the same data model, so the tile-derivation core and its
tests don't depend on the PBF toolchain.
"""

from dataclasses import dataclass
from xml.etree import ElementTree


@dataclass(frozen=True)
class Way:
    id: int
    tags: dict
    node_ids: list


def parse(xml_bytes: bytes) -> tuple[dict[int, tuple[float, float]], list[Way]]:
    """(nodes, ways) where nodes maps node id -> (lat, lon)."""
    root = ElementTree.fromstring(xml_bytes)

    nodes: dict[int, tuple[float, float]] = {}
    for n in root.iter("node"):
        nodes[int(n.attrib["id"])] = (float(n.attrib["lat"]), float(n.attrib["lon"]))

    ways: list[Way] = []
    for w in root.iter("way"):
        tags = {t.attrib["k"]: t.attrib["v"] for t in w.findall("tag")}
        node_ids = [int(nd.attrib["ref"]) for nd in w.findall("nd")]
        ways.append(Way(id=int(w.attrib["id"]), tags=tags, node_ids=node_ids))

    return nodes, ways
