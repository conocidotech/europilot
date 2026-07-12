"""Live matrix sign states from the NDW Matrixsignaalinformatie feed.

The feed is a snapshot: every sign appears as a location event (static, dated
whenever the sign was surveyed) and a display event (its current aspect). We
only care about the display events; locations come from the shapefile, which
also carries bearing and NWB wegvak.
"""

import gzip
import urllib.request
from xml.etree import ElementTree

from europilot.ndw.types import Display

MSI_URL = "https://opendata.ndw.nu/Matrixsignaalinformatie.xml.gz"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse(xml_bytes: bytes) -> dict[str, Display]:
    root = ElementTree.fromstring(xml_bytes)
    latest: dict[str, Display] = {}

    for event in root.iter():
        if _local(event.tag) != "event":
            continue

        uuid = ts_state = None
        display_el = None
        for child in event:
            name = _local(child.tag)
            if name == "sign_id":
                uuid = "".join(g.text or "" for g in child if _local(g.tag) == "uuid")
            elif name == "ts_state":
                ts_state = child.text
            elif name == "display":
                display_el = child

        if not uuid or display_el is None:
            continue

        aspect_el = next(iter(display_el), None)
        if aspect_el is None:
            continue

        aspect = _local(aspect_el.tag)
        speed = None
        if aspect == "speedlimit" and (aspect_el.text or "").strip():
            try:
                speed = int(aspect_el.text.strip())
            except ValueError:
                pass

        display = Display(
            uuid=uuid,
            aspect=aspect,
            speed=speed,
            flashing=aspect_el.get("flashing") == "true",
            red_ring=aspect_el.get("red_ring") == "true",
            ts_state=ts_state or "",
        )

        prior = latest.get(uuid)
        if prior is None or display.ts_state >= prior.ts_state:
            latest[uuid] = display

    return latest


def fetch(url: str = MSI_URL, timeout: float = 30.0) -> dict[str, Display]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return parse(gzip.decompress(resp.read()))


def load(path: str) -> dict[str, Display]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as fh:
        return parse(fh.read())
