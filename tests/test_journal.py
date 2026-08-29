"""NDJSON history. `unmatched.ndjson` is the bug queue."""

from __future__ import annotations

import json
from datetime import date

from verdict.journal import Journal


def test_appends_one_object_per_line(tmp_path):
    journal = Journal(tmp_path)
    for reason in ("state_shape_changed", "header_unparsed"):
        journal.unmatched(
            source="pitchfork_roundup",
            artist="Interpol",
            album="This Mirror Weighs a Ton",
            source_url="https://pitchfork.com/news/x/",
            reason=reason,
            run_date=date(2026, 8, 30),
        )

    lines = (tmp_path / "unmatched.ndjson").read_text().splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert [r["reason"] for r in records] == ["state_shape_changed", "header_unparsed"]
    assert records[0]["run_date"] == "2026-08-30"


def test_creates_the_log_directory(tmp_path):
    root = tmp_path / "log"
    Journal(root).unmatched(
        source="pitchfork_bnm", artist=None, album=None,
        source_url="https://pitchfork.com/reviews/albums/x/",
        reason="state_shape_changed",
    )
    assert (root / "unmatched.ndjson").exists()


def test_non_ascii_survives_the_round_trip(tmp_path):
    """Non-ASCII artist names are an expected resolution failure mode."""
    journal = Journal(tmp_path)
    journal.unmatched(
        source="pitchfork_roundup", artist="Sigur Rós", album="Á",
        source_url="https://pitchfork.com/news/x/", reason="no_spotify_match",
    )
    record = json.loads((tmp_path / "unmatched.ndjson").read_text())
    assert record["artist"] == "Sigur Rós"


def test_unknown_stream_is_rejected(tmp_path):
    """A stream name flows into a filename, so it is not free-form."""
    import pytest

    for bad in ("../escape", "additions/../../etc/passwd", "arbitrary"):
        with pytest.raises(ValueError):
            Journal(tmp_path).append(bad, {})
    assert not list(tmp_path.iterdir())
