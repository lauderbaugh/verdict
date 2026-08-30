"""Skipping interludes and skits in the fallback rungs."""

from __future__ import annotations

import json
from datetime import date

import pytest

from verdict.journal import Journal
from verdict.models import Verdict
from verdict.resolve.resolver import resolve
from verdict.resolve.selection import (
    MIN_TRACKS,
    ResolvedTrack,
    interludes,
    is_interlude,
    select,
)


def album(*names, short=()):
    return [
        {"uri": f"spotify:track:{i}", "name": n,
         "duration_ms": 60_000 if i in short else 200_000}
        for i, n in enumerate(names, 1)
    ]


# --- what counts as an interlude -----------------------------------------

@pytest.mark.parametrize(
    "title",
    [
        "Interlude", "Intro", "Outro", "Prelude", "Skit",
        "Skit 2", "Interlude 1",
        "Money Trees (Interlude)", "Song [Skit]",
        "Alright - Skit", "Wesley's Theory — Interlude",
        "Runaway (Reprise)",
    ],
)
def test_labelled_tracks_are_interludes(title):
    assert is_interlude({"name": title}) is True


@pytest.mark.parametrize(
    "title",
    [
        # The word appears, but as part of a real title rather than a label.
        "Interlude City", "Introspection", "The Intro Song",
        "Outrun", "Skittles", "Preludes for Memnon", "Reprise Your Love",
        # Ordinary titles.
        "Chrome", "Marrow Deep", "A Sunflower Garden in Harlem Is Hard to Find",
    ],
)
def test_titles_merely_containing_the_word_survive(title):
    """Word boundaries alone would not do this.

    `\\binterlude\\b` matches "Interlude City", which is a song title
    rather than an interlude, so the match is on a standalone label:
    the whole title, bracketed, or dash-suffixed.
    """
    assert is_interlude({"name": title}) is False


def test_a_missing_title_is_not_an_interlude():
    assert is_interlude({}) is False


# --- scope: fallback rungs only ------------------------------------------

def test_a_track_a_source_named_is_never_filtered():
    """If a critic writes about an interlude, that is a deliberate pick."""
    tracks = album("Intro", "Money Trees (Interlude)", "Real One", "Real Two")
    named = [ResolvedTrack(uri="spotify:track:2", name="Money Trees (Interlude)",
                           selection="named", confidence=1.0)]
    chosen = select(named, tracks)
    assert "Money Trees (Interlude)" in [t.name for t in chosen]


def test_positional_skips_interludes():
    chosen = select([], album("Intro", "Skit", "Real One", "(Interlude)", "Real Two"))
    assert [t.name for t in chosen] == ["Real One", "Real Two"]


def test_lastfm_skips_interludes():
    from verdict.resolve.lastfm import AlbumPlays

    plays = AlbumPlays(total=50_000, by_track={"skit": 99_999, "real one": 10})
    chosen = select([], album("Real Two", "Skit", "Real One"), plays)
    assert "Skit" not in [t.name for t in chosen]


def test_a_title_track_that_is_an_interlude_is_refused():
    """Unlike the sub-90s case, an explicit label is the artist saying so."""
    chosen = select([], album("Chrome (Interlude)", "Real One", "Real Two"),
                    album_name="Chrome")
    assert all(t.selection != "title_track" for t in chosen)
    assert "Chrome (Interlude)" not in [t.name for t in chosen]


def test_a_title_track_that_is_not_an_interlude_still_wins():
    chosen = select([], album("Intro", "Chrome", "Real Two"), album_name="Chrome")
    assert chosen[0].selection == "title_track"


# --- relaxing rather than returning fewer --------------------------------

def test_the_filter_relaxes_rather_than_returning_fewer():
    """A record made mostly of labelled fragments still gets represented."""
    chosen = select([], album("Intro", "Outro", "Skit"))
    assert len(chosen) == MIN_TRACKS
    assert all("interlude_filter_relaxed" in (t.rule or "") for t in chosen)


def test_relaxing_is_recorded_on_the_lastfm_rung():
    from verdict.resolve.lastfm import AlbumPlays

    plays = AlbumPlays(total=50_000, by_track={"intro": 900, "outro": 800})
    chosen = select([], album("Intro", "Outro"), plays)
    assert len(chosen) == MIN_TRACKS
    assert any(t.rule == "interlude_filter_relaxed" for t in chosen)


def test_no_relaxing_when_enough_real_tracks_remain():
    chosen = select([], album("Intro", "Real One", "Real Two", "Skit"))
    assert all("relaxed" not in (t.rule or "") for t in chosen)


def test_a_single_track_album_of_one_interlude_still_yields_it():
    chosen = select([], album("Intro"))
    assert len(chosen) == 1


# --- logging --------------------------------------------------------------

class FakeClient:
    def __init__(self, tracks):
        self._tracks = tracks

    def search_albums(self, artist, album_name):
        return [{"id": "a1", "name": album_name, "artists": [{"name": artist}]}]

    def album_tracks(self, album_id):
        return self._tracks


def test_skips_are_reported_for_logging():
    tracks = album("Intro", "Real One", "Money Trees (Interlude)", "Real Two")
    result = resolve(FakeClient(tracks),
                     Verdict(source="s", artist="A", album="B", source_url="u"))
    assert set(result.skipped_interludes) == {"Intro", "Money Trees (Interlude)"}


def test_a_named_interlude_is_not_reported_as_skipped():
    """It was selected, so it was not filtered out."""
    tracks = album("Intro", "Real One", "Money Trees (Interlude)", "Real Two")
    verdict = Verdict(source="s", artist="A", album="B", source_url="u",
                      named_tracks=("Money Trees (Interlude)",))
    result = resolve(FakeClient(tracks), verdict)
    assert "Money Trees (Interlude)" not in result.skipped_interludes
    assert "Intro" in result.skipped_interludes


def test_the_journal_writes_a_skips_stream(tmp_path):
    journal = Journal(tmp_path)
    journal.skip(source="pitchfork_roundup", artist="A", album="B",
                 track="Intro", reason="interlude", run_date=date(2026, 8, 31))
    record = json.loads((tmp_path / "skips.ndjson").read_text())
    assert record["track"] == "Intro"
    assert record["reason"] == "interlude"


def test_interludes_lists_every_labelled_track():
    tracks = album("Intro", "Real One", "Skit")
    assert [t["name"] for t in interludes(tracks)] == ["Intro", "Skit"]
