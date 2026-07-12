"""Tests for the osmium PBF/diff ingest front-end.

The osmium binary and a real PBF aren't needed: replication maths is pure, and
the command orchestration runs against a fake runner that records the plan.
"""

from pathlib import Path

import pytest

from gateway.osm import ingest
from gateway.osm.ingest import IngestPaths
from gateway.osm.replication import (
    diff_url, format_state, parse_state, pending, sequence_path, state_url,
)

RESIDENTIAL = Path(__file__).parent / "test_data" / "residential.osm"


class TestSequencePath:
    def test_padding_and_grouping(self):
        assert sequence_path(4523) == "000/004/523"
        assert sequence_path(0) == "000/000/000"
        assert sequence_path(999_999_999) == "999/999/999"

    def test_beyond_nine_digits(self):
        assert sequence_path(1_234_567_890) == "001/234/567/890"

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            sequence_path(-1)

    def test_urls(self):
        base = "https://osm.example/nl-latest/"   # trailing slash tolerated
        assert diff_url(base, 4523) == "https://osm.example/nl-latest/000/004/523.osc.gz"
        assert state_url("https://osm.example/nl-latest") == "https://osm.example/nl-latest/state.txt"


class TestStateFile:
    SAMPLE = (
        "#Sun Jul 12 00:00:00 UTC 2026\n"
        + "sequenceNumber=4523\n"
        + "timestamp=2026-07-12T00\\:00\\:00Z\n"
    )

    def test_parse_ignores_comments_and_unescapes(self):
        state = parse_state(self.SAMPLE)
        assert state.sequence == 4523
        assert state.timestamp == "2026-07-12T00:00:00Z"

    def test_round_trip(self):
        state = parse_state(self.SAMPLE)
        assert parse_state(format_state(state)) == state

    def test_missing_sequence_rejected(self):
        with pytest.raises(ValueError):
            parse_state("timestamp=2026-01-01T00:00:00Z\n")


class TestPending:
    def test_gap_is_oldest_first(self):
        assert pending(4520, 4523) == [4521, 4522, 4523]

    def test_up_to_date_or_ahead(self):
        assert pending(4523, 4523) == []
        assert pending(4523, 4520) == []

    def test_batch_cap_resumes_next_run(self):
        assert pending(4520, 4530, max_batch=3) == [4521, 4522, 4523]


class FakeRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))


class TestCommandConstruction:
    def test_tags_filter_keeps_source_and_all_expressions(self):
        cmd = ingest.tags_filter_cmd(Path("/src.osm.pbf"), Path("/out.osm.pbf"))
        assert cmd[:2] == ["osmium", "tags-filter"]
        assert "--overwrite" in cmd
        assert cmd[cmd.index("-o") + 1] == "/out.osm.pbf"
        assert "/src.osm.pbf" in cmd
        for expr in ingest.TAG_FILTER:
            assert expr in cmd

    def test_apply_changes_lists_every_diff(self):
        diffs = [Path("/d/a.osc.gz"), Path("/d/b.osc.gz")]
        cmd = ingest.apply_changes_cmd(Path("/src.pbf"), diffs, Path("/out.pbf"))
        assert cmd[:2] == ["osmium", "apply-changes"]
        assert cmd[-2:] == ["/d/a.osc.gz", "/d/b.osc.gz"]


class TestOrchestration:
    def _paths(self):
        return IngestPaths(source_pbf=Path("/data/nl.osm.pbf"), work_dir=Path("/work"))

    def test_refresh_runs_filter_then_export(self):
        runner = FakeRunner()
        out = ingest.refresh_extract(self._paths(), run=runner)
        assert out == Path("/work/extract.osm")
        assert [c[1] for c in runner.calls] == ["tags-filter", "cat"]
        # export reads what the filter wrote
        assert runner.calls[0][runner.calls[0].index("-o") + 1] == "/work/filtered.osm.pbf"
        assert runner.calls[1][-1] == "/work/filtered.osm.pbf"

    def test_apply_diffs_no_op_returns_source(self):
        runner = FakeRunner()
        out = ingest.apply_diffs(self._paths(), [], run=runner)
        assert out == Path("/data/nl.osm.pbf")
        assert runner.calls == []

    def test_apply_diffs_rolls_forward(self):
        runner = FakeRunner()
        out = ingest.apply_diffs(self._paths(), [Path("/work/4521.osc.gz")], run=runner)
        assert out == Path("/work/updated.osm.pbf")
        assert runner.calls[0][:2] == ["osmium", "apply-changes"]


class TestOsmiumGuard:
    def test_require_raises_when_absent(self, monkeypatch):
        monkeypatch.setattr(ingest.shutil, "which", lambda _: None)
        assert ingest.have_osmium() is False
        with pytest.raises(ingest.OsmiumNotFound):
            ingest.require_osmium()

    def test_present_when_on_path(self, monkeypatch):
        monkeypatch.setattr(ingest.shutil, "which", lambda _: "/usr/bin/osmium")
        assert ingest.have_osmium() is True
        ingest.require_osmium()   # no raise


class TestDerivationSeam:
    def test_export_feeds_the_spatial_core(self):
        # An exported .osm flows straight into the derivation, no osmium needed.
        tiles = ingest.build_tiles_from_osm(RESIDENTIAL)
        ids = {r["id"] for recs in tiles.values() for r in recs}
        assert 101 in ids   # residential road A survives ingest->derivation
