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
    named = [t.name for t in result.tracks if t.selection == "named"]
    assert named == ["San Francisco"]


def test_two_candidates_naming_one_track_are_deduplicated():
    result = resolve(
        FakeClient(tracks=[track("San Francisco - 2011 Remaster")]),
        verdict(named_tracks=("San Francisco", "San Francisco (2019 Remaster)")),
    )
    assert len(result.tracks) == 1


# --- fallback -------------------------------------------------------------

def test_no_named_tracks_falls_back_to_the_minimum():
    """Never the whole album.

    ~13 albums a week over a 4-week window would be a 500-track playlist.
    """
    result = resolve(FakeClient(), verdict(named_tracks=()))
    assert result.used_fallback is True
    assert len(result.tracks) == 2
    assert all(t.selection == "positional" for t in result.tracks)


def test_candidates_that_all_fail_validation_also_fall_back():
    result = resolve(FakeClient(), verdict(named_tracks=("honesty", "real", "I")))
    assert result.used_fallback is True
    assert len(result.tracks) == 2


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


# --- fixes from adversarial QA --------------------------------------------


def test_shared_credits_resolve():
    """'Erykah Badu / the Alchemist' is in the captured roundup.

    Spotify lists such records with one artist per entry, so the joined
    credit matched neither and scored 0.68 -- below the 0.80 threshold.
    A whole class of collaborations, not a one-off.
    """
    hits = [{
        "id": "collab", "name": "Before the World Blows",
        "artists": [{"name": "Erykah Badu"}, {"name": "The Alchemist"}],
    }]
    result = resolve(
        FakeClient(albums=hits, tracks=[track("Ghost Ship")]),
        verdict(artist="Erykah Badu / the Alchemist", album="Before the World Blows"),
    )
    assert isinstance(result, Resolution)
    assert result.album_id == "collab"


@pytest.mark.parametrize(
    "credit", ["A & B", "A and B", "A, B", "A x B", "A with B"],
)
def test_other_shared_credit_separators(credit):
    hits = [{"id": "c", "name": "Record", "artists": [{"name": "A"}, {"name": "B"}]}]
    result = resolve(
        FakeClient(albums=hits, tracks=[track("T")]),
        verdict(artist=credit, album="Record"),
    )
    assert isinstance(result, Resolution)


def test_splitting_credits_does_not_match_unrelated_artists():
    """The split must not loosen matching into false positives."""
    hits = [{"id": "x", "name": "Before the World Blows",
             "artists": [{"name": "Some Other Band"}]}]
    result = resolve(
        FakeClient(albums=hits),
        verdict(artist="Erykah Badu / the Alchemist", album="Before the World Blows"),
    )
    assert isinstance(result, Unresolved)


def test_duplicate_track_names_keep_the_first():
    """A multi-disc 'Intro' or a reprise: the later one silently won."""
    tracks = [
        {"name": "Intro", "uri": "spotify:track:disc1"},
        {"name": "Outro", "uri": "spotify:track:outro"},
        {"name": "Intro", "uri": "spotify:track:disc2"},
    ]
    result = resolve(FakeClient(tracks=tracks), verdict(named_tracks=("Intro",)))
    named = [t.uri for t in result.tracks if t.selection == "named"]
    assert named == ["spotify:track:disc1"]


def test_near_misses_list_several_runners_up():
    """The bug queue is more useful with the alternatives than without."""
    hits = [
        {"id": "1", "name": "Wrong One", "artists": [{"name": "Nobody"}]},
        {"id": "2", "name": "Wrong Two", "artists": [{"name": "Nobody"}]},
    ]
    result = resolve(FakeClient(albums=hits), verdict())
    assert isinstance(result, Unresolved)
    assert "Wrong One" in result.detail and "Wrong Two" in result.detail
    assert result.detail.startswith("closest:")
