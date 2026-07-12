"""Drive the osmium toolchain: canonical PBF + daily diffs -> derivation-ready OSM.

The production front-end for the tile pipeline. osmium-tool (a C++ binary, far
faster than pyosmium for bulk passes) does the heavy lifting; this module builds
the exact commands and runs them in order:

  1. tags-filter  -- drop everything but the roads, cycleways, residential areas
     and the tagged nodes the derivations use, so the working file is small and
     reference-complete (way geometry and relation members kept).
  2. cat -> .osm   -- export to the XML `osm_source.parse_full` reads. The
     derivation core stays format-agnostic; only this step knows about osmium.
  3. apply-changes -- roll the source PBF forward with fetched `.osc.gz` diffs
     (see replication.py for which diffs and in what order) before re-filtering.

Everything that touches the disk goes through an injectable `run` callable, so
the command *plan* is testable without the binary or a 1 GB extract present.
`have_osmium()` gates the real runs; the derivation half needs neither.
"""

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from gateway.osm.osm_source import parse_full
from gateway.osm.tiles import build_tiles_full

# osmium tags-filter expressions. Roads are kept broadly (all `highway` ways);
# derive_way() drops the classes we don't serve, and highway=cycleway is needed
# as parallel geometry for the cycleway spatial join. Nodes are kept only where
# tagged (crossings, calming); geometry-only nodes ride along as way references.
# landuse=residential comes as both closed ways and multipolygon relations.
TAG_FILTER: tuple[str, ...] = (
    "w/highway",
    "n/highway=crossing,traffic_signals",
    "n/highway=speed_camera",
    "n/traffic_calming",
    "nw/landuse=residential",
    "r/landuse=residential",
    "r/type=enforcement",
)

Runner = Callable[[Sequence[str]], None]


class OsmiumNotFound(RuntimeError):
    """osmium-tool isn't on PATH; the ingest passes can't run."""


def have_osmium() -> bool:
    return shutil.which("osmium") is not None


def require_osmium() -> None:
    if not have_osmium():
        raise OsmiumNotFound(
            "osmium-tool not found on PATH -- install osmium-tool to run PBF ingest "
            + "(the derivation core works on .osm XML without it)"
        )


def _run(cmd: Sequence[str]) -> None:
    subprocess.run(list(cmd), check=True)


# --- command construction (pure) --------------------------------------------

def tags_filter_cmd(src: Path, dst: Path, expressions: Sequence[str] = TAG_FILTER) -> list[str]:
    return ["osmium", "tags-filter", "--overwrite", "-o", str(dst), str(src), *expressions]


def to_xml_cmd(src: Path, dst: Path) -> list[str]:
    return ["osmium", "cat", "--overwrite", "-o", str(dst), str(src)]


def apply_changes_cmd(src: Path, changes: Sequence[Path], dst: Path) -> list[str]:
    """Apply one or more `.osc(.gz)` diffs to a PBF; osmium orders them itself."""
    return ["osmium", "apply-changes", "--overwrite", "-o", str(dst), str(src),
            *[str(c) for c in changes]]


# --- orchestration ----------------------------------------------------------

@dataclass(frozen=True)
class IngestPaths:
    source_pbf: Path   # canonical extract we hold and roll forward
    work_dir: Path

    @property
    def filtered_pbf(self) -> Path:
        return self.work_dir / "filtered.osm.pbf"

    @property
    def updated_pbf(self) -> Path:
        return self.work_dir / "updated.osm.pbf"

    @property
    def export_osm(self) -> Path:
        return self.work_dir / "extract.osm"


def refresh_extract(paths: IngestPaths, run: Runner = _run) -> Path:
    """Filter the source PBF and export the .osm XML the derivation core reads."""
    run(tags_filter_cmd(paths.source_pbf, paths.filtered_pbf))
    run(to_xml_cmd(paths.filtered_pbf, paths.export_osm))
    return paths.export_osm


def apply_diffs(paths: IngestPaths, diff_files: Sequence[Path], run: Runner = _run) -> Path:
    """Roll the source PBF forward with fetched diffs; a no-op if there are none.

    Returns the PBF to derive from -- the updated file when diffs applied, else
    the untouched source.
    """
    if not diff_files:
        return paths.source_pbf
    run(apply_changes_cmd(paths.source_pbf, diff_files, paths.updated_pbf))
    return paths.updated_pbf


def build_tiles_from_osm(export_osm: Path) -> dict:
    """Parse an exported .osm and run the full spatial derivation into tiles.

    The seam between ingest and derivation: everything upstream is osmium, and
    everything from here (parse_full, build_tiles_full) is pure Python that a
    test can exercise on a fixture without the toolchain.
    """
    return build_tiles_full(parse_full(Path(export_osm).read_bytes()))
