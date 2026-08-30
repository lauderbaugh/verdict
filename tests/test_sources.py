"""The shared adapter interface, and the overlap between the two sources."""

from __future__ import annotations

import pytest

from verdict.feed import FeedItem
from verdict.sources import pitchfork_bnm, pitchfork_roundup
from verdict.sources.base import ParseResult, Source

ADAPTERS = [pitchfork_roundup, pitchfork_bnm]


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.NAME)
def test_adapter_satisfies_the_source_protocol(adapter):
    assert isinstance(adapter, Source)
    assert adapter.NAME and adapter.FEED_URL.startswith("https://")


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.NAME)
def test_select_is_pure_and_needs_no_network(adapter):
    assert isinstance(adapter.select(FeedItem(link="https://example.com/x/")), bool)


def test_adapters_use_different_feeds():
    assert pitchfork_roundup.FEED_URL != pitchfork_bnm.FEED_URL


def test_both_adapters_return_parse_results(roundup_html, bnm_html):
    roundup_result = pitchfork_roundup.parse(
        FeedItem(link="https://pitchfork.com/news/x-albums-you-should-listen-to-now/"),
        roundup_html,
    )
    bnm_result = pitchfork_bnm.parse(
        FeedItem(link="https://pitchfork.com/reviews/albums/x/"), bnm_html
    )
    assert isinstance(roundup_result, ParseResult)
    assert isinstance(bnm_result, ParseResult)


def test_the_two_sources_genuinely_overlap(roundup_html, not_bnm_html):
    """Dinosaur Jr. appears in both feeds, which is why dedup is needed.

    It is Best New Music in neither, so only the roundup emits it here --
    but the same album reaching two adapters is the normal case, and is
    resolved downstream by Spotify URI rather than by artist/album text.
    """
    roundup_result = pitchfork_roundup.parse(
        FeedItem(link="https://pitchfork.com/news/x-albums-you-should-listen-to-now/"),
        roundup_html,
    )
    from verdict.verso import item_reviewed_name

    assert item_reviewed_name(not_bnm_html) == "Dinosaur Jr.: There Near"
    overlap = [v for v in roundup_result.verdicts if v.artist == "Dinosaur Jr."]
    assert len(overlap) == 1 and overlap[0].album == "There Near"


def test_the_same_album_carries_different_labels_per_source(roundup_html):
    """Labels are log-only and must never be used for matching.

    The roundup header spells it `Jagjagwuar`; the review blob spells it
    `Jagjaguwar`. Both are faithful to their source.
    """
    roundup_result = pitchfork_roundup.parse(
        FeedItem(link="https://pitchfork.com/news/x-albums-you-should-listen-to-now/"),
        roundup_html,
    )
    dino = next(v for v in roundup_result.verdicts if v.artist == "Dinosaur Jr.")
    assert dino.label == "Jagjagwuar"
