"""The shared adapter interface, and the overlap between the two sources."""

from __future__ import annotations

import pytest

from verdict.feed import FeedItem
from verdict.sources import npr_new_music_friday, pitchfork_bnm, pitchfork_roundup
from verdict.sources.base import Candidate, ParseResult, Source

ADAPTERS = [pitchfork_roundup, pitchfork_bnm, npr_new_music_friday]


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
    urls = {a.FEED_URL for a in ADAPTERS}
    assert len(urls) == len(ADAPTERS)


def test_only_npr_answers_without_a_page_fetch():
    """A source that can answer from the feed should not fetch a page."""
    feed = open("tests/fixtures/npr_music_podcast_feed.xml").read()
    npr_candidates = npr_new_music_friday.discover(lambda _: feed).candidates
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
