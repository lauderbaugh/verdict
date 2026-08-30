"""Verdict -> album -> validated track URIs."""

from __future__ import annotations

import pytest

from verdict.models import Verdict
from verdict.resolve.resolver import Resolution, Unresolved, pick_album, resolve
from verdict.spotify import SpotifyError


def album(name, artist="Aldous Harding", album_id="alb1"):
    return {"id": album_id, "name": name, "artists": [{"name": artist}]}


def track(name, uri=None):
    return {"name": name, "uri": uri or f"spotify:track:{name.replace(' ', '')}"}


class FakeClient:
    """Stands in for Spotify, recording what was asked for."""

    def __init__(self, albums=None, tracks=None, search_error=None, tracks_error=None):
        self.albums = albums if albums is not None else [album("Train on the Island")]
        self.tracks = tracks if tracks is not None else [
            track("I Ate the Most"), track("San Francisco"), track("One Stop"),
        ]
        self.search_error = search_error
        self.tracks_error = tracks_error
        self.searches = []

    def search_albums(self, artist, album_name):
        self.searches.append((artist, album_name))
        if self.search_error:
            raise self.search_error
        return self.albums

    def album_tracks(self, album_id):
        if self.tracks_error:
            raise self.tracks_error
        return self.tracks


def verdict(**kw):
    base = dict(
        source="pitchfork_bnm", artist="Aldous Harding", album="Train on the Island",
        source_url="https://pitchfork.com/reviews/albums/x/", named_tracks=(),
    )
    base.update(kw)
    return Verdict(**base)


# --- happy path -----------------------------------------------------------

def test_named_tracks_that_validate_are_kept():
    result = resolve(FakeClient(), verdict(named_tracks=("San Francisco", "One Stop")))
    assert isinstance(result, Resolution)
    assert [t.name for t in result.tracks] == ["San Francisco", "One Stop"]
    assert result.used_fallback is False
    assert all(t.confidence == 1.0 for t in result.tracks)


def test_noise_is_discarded_but_real_tracks_survive():
    """The whole point: a lyric alongside a real title costs nothing."""
    result = resolve(
        FakeClient(),
        verdict(named_tracks=("honesty", "San Francisco", "He’s got a new bag")),
    )
    assert [t.name for t in result.tracks] == ["San Francisco"]
    assert result.used_fallback is False


def test_two_candidates_naming_one_track_are_deduplicated():
    result = resolve(
        FakeClient(tracks=[track("San Francisco - 2011 Remaster")]),
        verdict(named_tracks=("San Francisco", "San Francisco (2019 Remaster)")),
    )
    assert len(result.tracks) == 1


# --- fallback -------------------------------------------------------------

def test_no_named_tracks_falls_back_to_the_first_track_only():
    """Never the whole album.

    ~13 albums a week over a 4-week window would be a 500-track playlist.
    """
    result = resolve(FakeClient(), verdict(named_tracks=()))
    assert result.used_fallback is True
    assert [t.name for t in result.tracks] == ["I Ate the Most"]
    assert result.tracks[0].confidence is None


def test_candidates_that_all_fail_validation_also_fall_back():
    result = resolve(FakeClient(), verdict(named_tracks=("honesty", "real", "I")))
    assert result.used_fallback is True
    assert len(result.tracks) == 1


# --- album selection ------------------------------------------------------

def test_the_best_matching_album_is_chosen():
    hits = [
        album("Train on the Island (Deluxe)", album_id="deluxe"),
        album("Train on the Island", album_id="exact"),
        album("Completely Different", album_id="wrong"),
    ]
    result = resolve(FakeClient(albums=hits), verdict())
    assert result.album_id in {"exact", "deluxe"}
    assert result.album_id != "wrong"


def test_a_right_title_by_the_wrong_artist_is_rejected():
    """Self-titled albums and covers make this a real failure mode.

    Scoring takes the weaker of artist and album similarity, so a correct
    title cannot rescue a wrong artist.
    """
    hits = [album("Train on the Island", artist="Some Other Band")]
    result = resolve(FakeClient(albums=hits), verdict())
    assert isinstance(result, Unresolved)
    assert result.reason == "album_below_threshold"


def test_near_misses_are_logged_not_guessed():
    hits = [album("A Totally Different Record", album_id="x")]
    result = resolve(FakeClient(albums=hits), verdict())
    assert isinstance(result, Unresolved)
    assert "A Totally Different Record" in result.detail
    assert result.reason == "album_below_threshold"


def test_empty_search_is_unresolved():
    result = resolve(FakeClient(albums=[]), verdict())
    assert isinstance(result, Unresolved) and result.reason == "album_not_found"


def test_pick_album_reports_a_score_even_when_it_fails():
    chosen, score = pick_album(verdict(), [album("Nothing Alike")])
    assert chosen is not None and 0.0 <= score < 1.0


# --- failures -------------------------------------------------------------

def test_search_failure_is_unresolved_not_an_exception():
    """A run must never crash, and never silently stop updating."""
    result = resolve(FakeClient(search_error=SpotifyError("boom")), verdict())
    assert isinstance(result, Unresolved) and result.reason == "search_failed"


def test_tracklist_failure_is_unresolved():
    result = resolve(FakeClient(tracks_error=SpotifyError("boom")), verdict())
    assert isinstance(result, Unresolved) and result.reason == "tracklist_failed"


def test_album_with_no_tracks_is_unresolved():
    result = resolve(FakeClient(tracks=[]), verdict())
    assert isinstance(result, Unresolved) and result.reason == "album_has_no_tracks"


def test_non_ascii_artist_resolves():
    """Named as an expected failure mode, so it gets a test."""
    hits = [album("Á", artist="Sigur Rós", album_id="s1")]
    result = resolve(
        FakeClient(albums=hits, tracks=[track("Rósas")]),
        verdict(artist="Sigur Ros", album="A", named_tracks=("Rosas",)),
    )
    assert isinstance(result, Resolution)
    assert result.tracks[0].name == "Rósas"
