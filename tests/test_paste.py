"""Paste album reviews, against the real captured index and review page."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from verdict.feed import FeedItem
from verdict.sources import paste
from verdict.sources.base import Candidate

#: The day the fixtures were captured, so the window is deterministic.
CAPTURED = datetime(2026, 8, 31, tzinfo=timezone.utc)
ARCHIVE = datetime(2026, 4, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def index():
    with open("tests/fixtures/paste_reviews_index.html", encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def review():
    with open("tests/fixtures/paste_review.html", encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def discovery(index):
    return paste.discover(lambda _: index, now=CAPTURED)


def candidate_for(discovery, slug):
    for candidate in discovery.candidates:
        if slug in candidate.item.link:
            return candidate
    raise AssertionError(f"no candidate for {slug!r}")


# --- discovery ------------------------------------------------------------

def test_the_index_is_read_without_a_feed(index):
    """Paste has no working section feed; the index is richer than one."""
    rows = paste.index_rows(index)
    assert len(rows) == 108
    assert all(r.link.startswith("https://www.pastemagazine.com/music/") for r in rows)


def test_discovery_is_bounded_to_the_recent_window(discovery):
    """108 rows span four months; an unbounded run reprocesses the archive."""
    assert len(discovery.candidates) == 9
    assert discovery.problems == ()


def test_a_wider_window_takes_more(index):
    assert len(paste.discover(lambda _: index, now=ARCHIVE).candidates) > 9


def test_every_candidate_needs_its_page(discovery):
    """The index gives artist and album, but not the grade or the prose.

    The prose is where the named tracks are, which is the whole reason
    this source is worth more than a listing.
    """
    assert all(c.needs_page for c in discovery.candidates)


def test_an_undated_row_is_kept():
    row = ('<a class="auto cell copy-container " href="https://www.pastemagazine.com'
           '/music/band/rec-review"><b class="title">Band on <em>Rec</em></b>'
           '<b class="byline">By X</b><b class="time">sometime</b></a>')
    assert len(paste.discover(lambda _: row, now=CAPTURED).candidates) == 1


def test_a_dead_index_is_a_problem_not_an_exception():
    def explode(url):
        raise OSError("no route to host")

    result = paste.discover(explode, now=CAPTURED)
    assert result.candidates == ()
    assert result.problems[0].reason.startswith("index_unavailable")


def test_an_index_with_no_rows_is_a_shape_change():
    """A 200 with no rows means the markup moved, not a quiet week."""
    result = paste.discover(lambda _: "<html>nothing</html>", now=CAPTURED)
    assert result.problems[0].reason == "index_shape_changed"


# --- parsing --------------------------------------------------------------

def test_the_captured_review_parses(discovery, review):
    verdict = paste.parse(candidate_for(discovery, "ty-segall"), review).verdicts[0]
    assert verdict.source == "paste"
    assert verdict.artist == "Ty Segall"
    assert verdict.album == "Chrome"


def test_the_letter_grade_is_kept_source_native(discovery, review):
    """Paste kept its grades; AV Club dropped them under the same owner.

    A "B-" is carried as "B-". Normalizing it into a number would make it
    comparable with a Pitchfork 8.6, which it is not.
    """
    verdict = paste.parse(candidate_for(discovery, "ty-segall"), review).verdicts[0]
    assert verdict.score == "B-"


def test_a_review_without_a_grade_still_parses(discovery):
    verdict = paste.parse(
        candidate_for(discovery, "ty-segall"),
        "<h1>Band on <em>Rec</em></h1>",
    ).verdicts[0]
    assert verdict.score is None


def test_tracks_come_from_prose_unchanged(discovery, review):
    """Same curly-quote convention as Pitchfork; prose.py needs no changes."""
    verdict = paste.parse(candidate_for(discovery, "ty-segall"), review).verdicts[0]
    assert len(verdict.named_tracks) == 6
    assert "Black Paint" in verdict.named_tracks


def test_the_album_is_never_a_track_candidate(discovery, review):
    """Paste italicises albums rather than quoting them."""
    verdict = paste.parse(candidate_for(discovery, "ty-segall"), review).verdicts[0]
    assert verdict.album not in verdict.named_tracks


def test_no_editorial_tier_exists(discovery, review):
    """Every Paste review is just a review; there is no 'best of' flag."""
    verdict = paste.parse(candidate_for(discovery, "ty-segall"), review).verdicts[0]
    assert verdict.editorial_tier is None


def test_published_at_comes_from_the_index(discovery, review):
    verdict = paste.parse(candidate_for(discovery, "ty-segall"), review).verdicts[0]
    assert verdict.published_at.date().isoformat() == "2026-08-28"


# --- artist, and the release-credit question ------------------------------

def test_the_artist_comes_from_the_url_slug(discovery):
    """The headline is prose with the artist in a varying position.

    The slug is also the *release* credit rather than a canonical artist,
    which is what Spotify wants: an AV Club retrospective on Smog
    carried a breadcrumb reading "Bill Callahan", and searching for that
    would miss the record.
    """
    verdict = paste.parse(candidate_for(discovery, "saul-williams"), None).verdicts[0]
    assert verdict.artist == "Saul Williams"


def test_casing_is_recovered_from_the_headline(discovery):
    """The slug is lowercased; the headline is not."""
    verdict = paste.parse(candidate_for(discovery, "katseye"), None).verdicts[0]
    assert verdict.artist == "KATSEYE"


def test_a_url_without_an_artist_slug_is_a_problem():
    item = FeedItem(link="https://www.pastemagazine.com/somewhere-else",
                    title="Band on <em>Rec</em>")
    result = paste.parse(Candidate(item), None)
    assert result.verdicts == ()
    assert result.problems[0].reason == "artist_album_unparsed"


# --- the index / page split -----------------------------------------------

def test_the_index_headline_alone_yields_most_verdicts(index):
    """88% of rows italicise the album, so the page is not needed for it.

    Measured across the whole index rather than one week: any given
    fortnight varies, and the captured window happens to hold three
    headlines that name the album without italicising it ("Lambchop's
    17th album makes a hymn out of a failed America").
    """
    rows = paste.index_rows(index)
    from_index = sum(1 for r in rows if paste.parse(Candidate(r), None).verdicts)
    assert from_index == 96
    assert from_index / len(rows) > 0.85


def test_a_headline_without_italics_still_resolves_from_the_page(discovery, review):
    """The remainder is why every candidate fetches its page anyway."""
    lambchop = candidate_for(discovery, "lambchop")
    assert paste.parse(lambchop, None).problems  # index alone cannot
    # A page that does italicise it recovers the album.
    recovered = paste.parse(lambchop, "<h1>Lambchop on <em>Punching The Clown</em></h1>")
    assert recovered.verdicts[0].album == "Punching The Clown"


def test_a_page_whose_headline_drops_the_italics_falls_back(discovery):
    """Discovery already captured the index title; try both."""
    verdict = paste.parse(
        candidate_for(discovery, "ty-segall"), "<html><h1>No italics here</h1></html>"
    ).verdicts[0]
    assert verdict.album == "Chrome"


def test_an_album_named_nowhere_is_a_problem():
    item = FeedItem(link="https://www.pastemagazine.com/music/band/x",
                    title="A headline with no italics at all")
    result = paste.parse(Candidate(item), None)
    assert result.problems[0].reason == "artist_album_unparsed"


def test_nothing_passes_silently(discovery, review):
    for candidate in discovery.candidates:
        result = paste.parse(candidate, review)
        assert result.verdicts or result.problems


# --- the expectation worth watching ---------------------------------------

def test_paste_albums_resolve_via_named_not_the_fallback(discovery, review):
    """Paste names tracks in prose, unlike NPR.

    If these came back as `lastfm`, the extraction would not be finding
    what the fixtures show.
    """
    from verdict.resolve.resolver import resolve

    verdict = paste.parse(candidate_for(discovery, "ty-segall"), review).verdicts[0]

    class FakeClient:
        def search_albums(self, artist, album):
            return [{"id": "a", "name": album, "artists": [{"name": artist}]}]

        def album_tracks(self, album_id):
            named = list(verdict.named_tracks)[:4]
            return [
                {"name": n, "uri": f"spotify:track:{i}", "duration_ms": 210_000}
                for i, n in enumerate(named, 1)
            ] + [
                {"name": f"Other {i}", "uri": f"spotify:track:o{i}",
                 "duration_ms": 210_000}
                for i in range(1, 5)
            ]

    result = resolve(FakeClient(), verdict)
    assert all(t.selection == "named" for t in result.tracks)
    assert len(result.tracks) == 4
