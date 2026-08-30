"""Roundup adapter, against a real captured roundup page."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from verdict.feed import FeedItem
from verdict.sources import pitchfork_roundup as roundup
from verdict.sources.base import Candidate

FEED_DATE = datetime(2026, 8, 28, tzinfo=timezone.utc)
ITEM = FeedItem(
    link="https://pitchfork.com/news/13-new-albums-you-should-listen-to-now/",
    published_at=FEED_DATE,
)


def synthetic_page(body: list) -> str:
    """A minimal Verso page wrapping a hand-built body AST."""
    state = {"transformed": {"article": {"body": body}}}
    return f"<script>window.__PRELOADED_STATE__ = {json.dumps(state)};</script>"


@pytest.fixture(scope="module")
def parsed(roundup_html):
    return roundup.parse(Candidate(ITEM), roundup_html)


@pytest.mark.parametrize(
    "link, expected",
    [
        ("https://pitchfork.com/news/13-new-albums-you-should-listen-to-now/", True),
        ("https://pitchfork.com/news/9-new-albums-you-should-listen-to-now/", True),
        # Matched on slug, not title: the news feed carries unrelated
        # "Listen to ..." posts, and the album count varies weekly.
        ("https://pitchfork.com/news/listen-to-new-song-by-band/", False),
        ("https://pitchfork.com/reviews/albums/artist-album/", False),
    ],
)
def test_select_matches_on_slug(link, expected):
    assert roundup.select(FeedItem(link=link)) is expected


def test_finds_every_album(parsed):
    assert len(parsed.verdicts) == 13
    assert parsed.problems == ()


def test_header_is_split_into_artist_album_label(parsed):
    first = parsed.verdicts[0]
    assert first.artist == "Interpol"
    assert first.album == "This Mirror Weighs a Ton"
    assert first.label == "Partisan"


def test_artist_is_not_derived_from_a_url_slug(parsed):
    """The header is fully structured, so slashes and dots survive intact."""
    artists = [v.artist for v in parsed.verdicts]
    assert "Erykah Badu / the Alchemist" in artists
    assert "Dinosaur Jr." in artists


def test_album_titles_may_contain_colons(parsed):
    assert "Never Enough: Versions" in [v.album for v in parsed.verdicts]


def test_every_verdict_is_attributed(parsed):
    for verdict in parsed.verdicts:
        assert verdict.source == "pitchfork_roundup"
        assert verdict.artist and verdict.album
        assert verdict.score is None  # the roundup does not score


def test_published_at_comes_from_the_feed(parsed):
    """Article bodies carry no reliable date, so the feed is authoritative."""
    assert all(v.published_at == FEED_DATE for v in parsed.verdicts)


def test_some_albums_name_no_tracks(parsed):
    """Roughly 4 of 13 name nothing, which is why a fallback is required."""
    empty = [v for v in parsed.verdicts if not v.named_tracks]
    assert {v.artist for v in empty} == {
        "Mike D", "Asher White", "Billy Strings", "Sarah Davachi",
    }


def test_candidates_are_noisy_and_that_is_expected(parsed):
    """Lyrics come along with real titles; validation discards them later.

    Interpol's writeup quotes two lines of lyrics and names no tracks, so
    filtering "cleverly" here would be filtering the wrong thing.
    """
    interpol = parsed.verdicts[0]
    assert len(interpol.named_tracks) == 2
    assert all(len(c) <= 60 for c in interpol.named_tracks)


def test_album_titles_are_never_track_candidates(parsed):
    """Titles are italicised, never quoted, so `em` content cannot leak in."""
    for verdict in parsed.verdicts:
        assert verdict.album not in verdict.named_tracks


def test_trailing_punctuation_is_stripped(parsed):
    for verdict in parsed.verdicts:
        for candidate in verdict.named_tracks:
            assert candidate == candidate.strip()
            assert not candidate.endswith((",", ".", ";", ":"))


def test_candidates_are_deduplicated(parsed):
    for verdict in parsed.verdicts:
        assert len(set(verdict.named_tracks)) == len(verdict.named_tracks)


def test_non_prose_blocks_are_excluded():
    """`ad` and newsletter blocks sit between the h2s and must not leak.

    SPEC's denylist omits these three tags; the allowlist catches them.
    """
    page = synthetic_page(
        ["div",
         ["h2", "Band: ", ["em", "Record"], " [Label]"],
         ["p", "They open with “Real Track” here."],
         ["ad", "“Sponsored Thing”"],
         ["inline-newsletter", "“Sign Up Now”"],
         ["journey-inline-newsletter", "“Join Today”"],
         ["native-ad", "“Buy This”"],
         ["hr"]]
    )
    result = roundup.parse(Candidate(ITEM), page)
    assert len(result.verdicts) == 1
    assert result.verdicts[0].named_tracks == ("Real Track",)


def test_intro_prose_before_the_first_header_is_discarded():
    page = synthetic_page(
        ["div",
         ["p", "Intro copy mentioning “Not An Album Track”."],
         ["h2", "Band: ", ["em", "Record"], " [Label]"],
         ["p", "The single is “Real Track”."]]
    )
    result = roundup.parse(Candidate(ITEM), page)
    assert result.verdicts[0].named_tracks == ("Real Track",)


def test_straight_quotes_are_not_track_candidates():
    """Condé Nast uses typographic quotes; straight-quote matching finds nothing."""
    page = synthetic_page(
        ["div",
         ["h2", "Band: ", ["em", "Record"]],
         ["p", 'They play "Not A Match" tonight.']]
    )
    assert roundup.parse(Candidate(ITEM), page).verdicts[0].named_tracks == ()


def test_label_is_optional():
    page = synthetic_page(["div", ["h2", "Band: ", ["em", "Record"]], ["p", "Words."]])
    assert roundup.parse(Candidate(ITEM), page).verdicts[0].label is None


def test_malformed_header_is_logged_not_crashed():
    """A header with no album becomes a Problem; the good album survives."""
    page = synthetic_page(
        ["div",
         ["h2", "No Album Here"],
         ["p", "Some prose."],
         ["h2", "Band: ", ["em", "Record"], " [Label]"],
         ["p", "More prose."]]
    )
    result = roundup.parse(Candidate(ITEM), page)
    assert len(result.verdicts) == 1
    assert result.verdicts[0].artist == "Band"
    assert len(result.problems) == 1
    assert result.problems[0].reason == "header_unparsed"
    assert result.problems[0].artist == "No Album Here"


def test_leading_apostrophe_survives():
    """Only trailing punctuation is stripped; a leading one can be the title."""
    page = synthetic_page(
        ["div",
         ["h2", "Band: ", ["em", "Record"]],
         ["p", "The closer is “’Til the End,” a highlight."]]
    )
    assert roundup.parse(Candidate(ITEM), page).verdicts[0].named_tracks == ("’Til the End",)
