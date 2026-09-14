"""The shared adapter interface, and the overlap between the two sources."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from verdict.feed import FeedItem
from verdict.sources import (
    bandcamp_best_jazz,
    bandcamp_daily,
    npr_new_music_friday,
    pitchfork_bnm,
    pitchfork_roundup,
)
from verdict.sources.base import Candidate, ParseResult, Source

#: The days the NPR and Bandcamp feed fixtures were captured, so their
#: recency windows are deterministic rather than aging out with the clock.
NPR_CAPTURED = datetime(2026, 8, 31, tzinfo=timezone.utc)
BANDCAMP_CAPTURED = datetime(2026, 9, 1, tzinfo=timezone.utc)

#: The feed-based adapters. Paste is a source too, but discovers from
#: an HTML index and has no FEED_URL to check.
ADAPTERS = [
    pitchfork_roundup,
    pitchfork_bnm,
    npr_new_music_friday,
    bandcamp_daily,
    bandcamp_best_jazz,
]


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.NAME)
def test_adapter_satisfies_the_source_protocol(adapter):
    assert isinstance(adapter, Source)
    assert adapter.NAME and adapter.FEED_URL.startswith("https://")


def test_every_source_owns_its_discovery():
    """The orchestrator no longer knows what a feed is.

    NPR forced this: it needs the same RSS parsed into a different shape
    and needs no second request, so a shared parse_rss called by the
    caller could not serve both.
    """
    for adapter in ADAPTERS:
        assert callable(adapter.discover)


def test_sources_use_different_feeds():
    """Except the two Bandcamp adapters, which read different sections of
    one site-wide feed. That is what makes selection their whole job."""
    urls = {a.FEED_URL for a in ADAPTERS}
    assert len(urls) == len(ADAPTERS) - 1
    assert bandcamp_daily.FEED_URL == bandcamp_best_jazz.FEED_URL
    assert bandcamp_daily.SECTION != bandcamp_best_jazz.SECTION


def test_bandcamp_shares_a_feed_with_every_other_section():
    """Its feed is site-wide, so selection is the adapter's whole job here."""
    feed = open("tests/fixtures/bandcamp_daily_feed.xml", encoding="utf-8").read()
    from verdict.feed import parse_rss

    assert len(parse_rss(feed)) > len(bandcamp_daily.discover(lambda _: feed, now=BANDCAMP_CAPTURED).candidates)


def test_only_npr_answers_without_a_page_fetch():
    """A source that can answer from the feed should not fetch a page."""
    feed = open("tests/fixtures/npr_music_podcast_feed.xml").read()
    npr_candidates = npr_new_music_friday.discover(lambda _: feed, now=NPR_CAPTURED).candidates
    assert npr_candidates and all(not c.needs_page for c in npr_candidates)


def test_both_adapters_return_parse_results(roundup_html, bnm_html):
    roundup_result = pitchfork_roundup.parse(Candidate(FeedItem(link="https://pitchfork.com/news/x-albums-you-should-listen-to-now/")), roundup_html)
    bnm_result = pitchfork_bnm.parse(Candidate(FeedItem(link="https://pitchfork.com/reviews/albums/x/")), bnm_html)
    assert isinstance(roundup_result, ParseResult)
    assert isinstance(bnm_result, ParseResult)


def test_the_two_sources_genuinely_overlap(roundup_html, not_bnm_html):
    """Dinosaur Jr. appears in both feeds, which is why dedup is needed.

    It is Best New Music in neither, so only the roundup emits it here --
    but the same album reaching two adapters is the normal case, and is
    resolved downstream by Spotify URI rather than by artist/album text.
    """
    roundup_result = pitchfork_roundup.parse(Candidate(FeedItem(link="https://pitchfork.com/news/x-albums-you-should-listen-to-now/")), roundup_html)
    from verdict.verso import item_reviewed_name

    assert item_reviewed_name(not_bnm_html) == "Dinosaur Jr.: There Near"
    overlap = [v for v in roundup_result.verdicts if v.artist == "Dinosaur Jr."]
    assert len(overlap) == 1 and overlap[0].album == "There Near"


def test_the_same_album_carries_different_labels_per_source(roundup_html):
    """Labels are log-only and must never be used for matching.

    The roundup header spells it `Jagjagwuar`; the review blob spells it
    `Jagjaguwar`. Both are faithful to their source.
    """
    roundup_result = pitchfork_roundup.parse(Candidate(FeedItem(link="https://pitchfork.com/news/x-albums-you-should-listen-to-now/")), roundup_html)
    dino = next(v for v in roundup_result.verdicts if v.artist == "Dinosaur Jr.")
    assert dino.label == "Jagjagwuar"
