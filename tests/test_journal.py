"""NDJSON history. `unmatched.ndjson` is the bug queue."""

from __future__ import annotations

import json
from datetime import date, timedelta

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


# --- additions, removals, and the scoped dedup set -------------------------


def test_addition_records_the_spec_schema(tmp_path):
    journal = Journal(tmp_path)
    journal.addition(
        source="pitchfork_bnm", track="San Francisco", artist="Aldous Harding",
        album="Train on the Island", uri="spotify:track:x",
        source_url="https://pitchfork.com/reviews/albums/x/",
        score=9.0, match_confidence=1.0, run_date=date(2026, 8, 30),
    )
    record = json.loads((tmp_path / "additions.ndjson").read_text())
    assert set(record) == {
        "source", "track", "artist", "album", "uri", "source_url",
        "score", "match_confidence", "run_date",
    }


def test_removal_records_the_spec_schema(tmp_path):
    Journal(tmp_path).removal(uri="spotify:track:x", aged_out_date=date(2026, 8, 30))
    record = json.loads((tmp_path / "removals.ndjson").read_text())
    assert record == {"uri": "spotify:track:x", "aged_out_date": "2026-08-30"}


def test_recent_uris_is_bounded_by_the_window(tmp_path):
    """Reading the whole file would make the log a permanent blocklist."""
    journal = Journal(tmp_path)
    for uri, day in (("recent", 27), ("old", 40)):
        journal.addition(
            source="s", track="t", artist="a", album="b", uri=uri, source_url="u",
            run_date=date(2026, 8, 30) - timedelta(days=day),
        )
    assert journal.recent_uris(28, today=date(2026, 8, 30)) == {"recent"}


def test_recent_uris_on_a_missing_log(tmp_path):
    assert Journal(tmp_path).recent_uris(28) == set()


def test_a_malformed_line_does_not_lose_the_dedup_set(tmp_path):
    """A logging bug must not silently disable dedup."""
    journal = Journal(tmp_path)
    journal.addition(source="s", track="t", artist="a", album="b",
                     uri="good", source_url="u", run_date=date(2026, 8, 30))
    with (tmp_path / "additions.ndjson").open("a") as handle:
        handle.write("{not json\n\n")
    assert journal.recent_uris(28, today=date(2026, 8, 30)) == {"good"}
