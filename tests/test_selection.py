"""The three-step selection chain: named -> Last.fm -> positional."""

from __future__ import annotations

import pytest

from verdict.resolve.lastfm import AlbumPlays
from verdict.resolve.selection import (
    MAX_TRACKS,
    MIN_TRACKS,
    ResolvedTrack,
    positional_order,
    select,
)


def album(count=8, short=(), names=None):
    """A tracklist; positions in `short` are 60-second interludes."""
    return [
        {"uri": f"spotify:track:{i}",
         "name": (names or {}).get(i, f"Track {i}"),
         "duration_ms": 60_000 if i in short else 210_000}
        for i in range(1, count + 1)
    ]


def named(*names):
    return [ResolvedTrack(uri=f"named:{n}", name=n, selection="named", confidence=1.0)
            for n in names]


# --- step 1: named --------------------------------------------------------

def test_named_tracks_are_capped_at_four_in_prose_order():
    chosen = select(named("A", "B", "C", "D", "E", "F"), album())
    assert [t.name for t in chosen] == ["A", "B", "C", "D"]
    assert len(chosen) == MAX_TRACKS


def test_enough_named_tracks_consult_nothing_else():
    """The source already made the judgement; its pick beats an aggregate."""
    plays = AlbumPlays(total=999_999, by_track={"track 2": 500})
    chosen = select(named("A", "B"), album(), plays)
    assert [t.selection for t in chosen] == ["named", "named"]


def test_three_named_is_also_enough():
    chosen = select(named("A", "B", "C"), album(), AlbumPlays(10_000, {"track 2": 9}))
    assert all(t.selection == "named" for t in chosen)


# --- step 2: Last.fm ------------------------------------------------------

def test_lastfm_fills_to_the_minimum_ranked_by_playcount():
    plays = AlbumPlays(total=50_000,
                       by_track={"track 5": 900, "track 2": 100, "track 7": 50})
    chosen = select(named("A"), album(), plays)
    assert len(chosen) == MIN_TRACKS
    filled = chosen[1]
    assert filled.selection == "lastfm"
    assert filled.name == "Track 5"       # the most played, not the earliest
    assert filled.playcount == 900
    assert filled.album_playcount == 50_000
    assert filled.position == 5


def test_lastfm_fills_only_to_the_minimum_never_to_the_maximum():
    """Play counts break a tie the source left; they do not pad the album."""
    plays = AlbumPlays(total=50_000, by_track={f"track {i}": 100 * i for i in range(1, 9)})
    assert len(select(named(), album(), plays)) == MIN_TRACKS


def test_lastfm_does_not_repeat_a_named_track():
    tracks = album(names={2: "Already Named"})
    already = [ResolvedTrack(uri="spotify:track:2", name="Already Named",
                             selection="named", confidence=1.0)]
    plays = AlbumPlays(total=50_000, by_track={"already named": 9999, "track 5": 10})
    chosen = select(already, tracks, plays)
    assert len(chosen) == 2
    assert chosen[1].uri != "spotify:track:2"


def test_unranked_tracks_are_left_to_positional():
    """A zero playcount is no better information than position."""
    plays = AlbumPlays(total=50_000, by_track={"nothing matching": 5})
    chosen = select(named(), album(), plays)
    assert all(t.selection == "positional" for t in chosen)


# --- step 3: positional ---------------------------------------------------

def test_positional_prefers_tracks_two_and_four():
    chosen = select(named(), album())
    assert [t.position for t in chosen] == [2, 4]
    assert [t.rule for t in chosen] == ["position_2", "position_4"]


def test_track_one_is_never_preferred():
    """Disproportionately an intro."""
    assert 1 not in [t.position for t in select(named(), album())]


def test_short_tracks_are_skipped_when_a_longer_one_exists():
    chosen = select(named(), album(short=(2,)))
    assert 2 not in [t.position for t in chosen]
    assert [t.position for t in chosen] == [4, 3]


def test_short_tracks_are_accepted_when_nothing_longer_remains():
    chosen = select(named(), album(count=3, short=(1, 2, 3)))
    assert len(chosen) == MIN_TRACKS
    assert all("short_accepted" in t.rule for t in chosen)


@pytest.mark.parametrize("count, expected", [(1, [1]), (2, [2, 1]), (3, [2, 3])])
def test_short_albums_take_what_exists(count, expected):
    chosen = select(named(), album(count=count))
    assert [t.position for t in chosen] == expected


def test_a_single_track_album_yields_one_track():
    """Below the minimum, but there is nothing else to take."""
    assert len(select(named(), album(count=1))) == 1


# --- degradation ----------------------------------------------------------

def test_absent_lastfm_falls_through_to_positional():
    """None covers unreachable, malformed, unknown and too-sparse alike."""
    chosen = select(named(), album(), None)
    assert all(t.selection == "positional" for t in chosen)


def test_lastfm_with_no_track_counts_falls_through():
    chosen = select(named(), album(), AlbumPlays(total=50_000, by_track={}))
    assert all(t.selection == "positional" for t in chosen)


def test_every_track_records_how_it_was_chosen():
    """The selection has to be judgeable from additions.ndjson."""
    for chosen in (
        select(named("A", "B"), album()),
        select(named(), album(), AlbumPlays(9_000, {"track 2": 40, "track 5": 90})),
        select(named(), album()),
    ):
        for track in chosen:
            assert track.selection in {"named", "lastfm", "positional"}
            assert track.position is not None or track.selection == "named"
