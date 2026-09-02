"""RSS discovery. One of the two durable interfaces."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from verdict.feed import parse_rss

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Pitchfork</title>
  <item>
    <title>13 New Albums You Should Listen to Now</title>
    <link>https://pitchfork.com/news/13-new-albums-you-should-listen-to-now/</link>
    <description>Featuring Interpol and more.</description>
    <pubDate>Fri, 28 Aug 2026 12:00:00 +0000</pubDate>
  </item>
  <item>
    <title>Listen to a New Song</title>
    <link>https://pitchfork.com/news/listen-to-a-new-song/</link>
    <description>A single.</description>
    <pubDate>Thu, 27 Aug 2026 09:30:00 +0000</pubDate>
  </item>
  <item>
    <title>No link here</title>
    <description>Skipped.</description>
  </item>
  <item>
    <title>Unparseable date</title>
    <link>https://pitchfork.com/news/whenever/</link>
    <pubDate>sometime last week</pubDate>
  </item>
</channel></rss>
"""


@pytest.fixture(scope="module")
def items():
    return parse_rss(FEED)


def test_items_without_a_link_are_skipped(items):
    assert len(items) == 3


def test_fields_are_read(items):
    first = items[0]
    assert first.title == "13 New Albums You Should Listen to Now"
    assert first.link.endswith("/13-new-albums-you-should-listen-to-now/")
    assert first.description == "Featuring Interpol and more."


def test_pubdate_is_parsed_to_an_aware_datetime(items):
    when = items[0].published_at
    assert (when.year, when.month, when.day) == (2026, 8, 28)
    assert when.utcoffset() == timezone.utc.utcoffset(None)


def test_unparseable_date_yields_none_not_a_guess(items):
    assert items[2].published_at is None


def test_malformed_feed_raises():
    with pytest.raises(ValueError):
        parse_rss("<rss><channel><item>")


def test_a_minus_zero_zero_zero_pubdate_comes_back_aware():
    """RFC 5322 "-0000" is UTC with an unknown local zone, and the stdlib
    returns it naive. Bandcamp Daily stamps every item that way, and a
    naive value meeting an aware cutoff is a TypeError mid-run, not a
    slightly different answer."""
    xml = (
        '<rss><channel><item><link>https://x/1</link>'
        '<pubDate>Tue, 01 Sep 2026 13:44:42 -0000</pubDate>'
        "</item></channel></rss>"
    )
    published = parse_rss(xml)[0].published_at
    assert published == datetime(2026, 9, 1, 13, 44, 42, tzinfo=timezone.utc)


def test_an_offset_pubdate_is_normalised_to_utc():
    xml = (
        '<rss><channel><item><link>https://x/1</link>'
        '<pubDate>Tue, 01 Sep 2026 09:44:42 -0400</pubDate>'
        "</item></channel></rss>"
    )
    published = parse_rss(xml)[0].published_at
    assert published == datetime(2026, 9, 1, 13, 44, 42, tzinfo=timezone.utc)
