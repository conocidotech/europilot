"""OSM replication bookkeeping: which daily diffs to apply, in what order.

Keeping the local extract current means rolling it forward with the same
minutely/hourly/daily `.osc.gz` diffs OSM publishes on a replication server
(Geofabrik ships one per regional extract). Each diff has a monotonic sequence
number; the server advertises the newest in a `state.txt`, and every extract we
hold carries the sequence it was built at. The gap between the two is exactly
the list of diffs to fetch and apply, oldest first.

This module is that arithmetic and nothing else -- no network, no osmium, no
disk. `ingest.py` does the I/O and hands the raw text in here. Pure so the
one part that must not skip or reorder a diff is trivially testable.
"""

from dataclasses import dataclass

# The replication layout: a sequence number is zero-padded to at least 9 digits
# and split into groups of three, so 4523 lives at 000/004/523.osc.gz. This is
# the osmosis/planet convention every extract mirror follows.
_MIN_WIDTH = 9


@dataclass(frozen=True)
class ReplicationState:
    sequence: int
    timestamp: str   # ISO 8601, e.g. 2026-07-12T00:00:00Z ("" if unknown)


def sequence_path(seq: int) -> str:
    """Path fragment for a diff, e.g. 4523 -> '000/004/523' (no extension)."""
    if seq < 0:
        raise ValueError(f"negative sequence: {seq}")
    s = str(seq)
    width = _MIN_WIDTH
    if len(s) > width:                       # beyond 9 digits: pad to a multiple of 3
        width = len(s) + (-len(s) % 3)
    s = s.zfill(width)
    return "/".join(s[i:i + 3] for i in range(0, len(s), 3))


def diff_url(base_url: str, seq: int) -> str:
    """URL of one diff file on the replication server."""
    return f"{base_url.rstrip('/')}/{sequence_path(seq)}.osc.gz"


def state_url(base_url: str) -> str:
    """URL of the server's current-state file."""
    return f"{base_url.rstrip('/')}/state.txt"


def parse_state(text: str) -> ReplicationState:
    """Read an osmosis-style state.txt (Java .properties: `\\:`-escaped values)."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip().replace("\\:", ":").replace("\\\\", "\\")
    if "sequenceNumber" not in fields:
        raise ValueError("state.txt has no sequenceNumber")
    return ReplicationState(int(fields["sequenceNumber"]), fields.get("timestamp", ""))


def format_state(state: ReplicationState) -> str:
    """Serialize back to state.txt form (round-trips parse_state)."""
    ts = state.timestamp.replace("\\", "\\\\").replace(":", "\\:")
    return f"sequenceNumber={state.sequence}\ntimestamp={ts}\n"


def pending(local_seq: int, latest_seq: int, max_batch: int | None = None) -> list[int]:
    """Sequence numbers to apply to go from local_seq to latest_seq, oldest first.

    Empty when already current (or somehow ahead). `max_batch` caps a single run
    so a long-neglected extract catches up over several passes instead of
    fetching thousands of diffs at once; the next run resumes from where it left.
    """
    if latest_seq <= local_seq:
        return []
    seqs = list(range(local_seq + 1, latest_seq + 1))
    return seqs[:max_batch] if max_batch else seqs
