"""Bandcamp Daily's Best Jazz column, against two real captured months.

Two fixtures rather than one because the closing "Other Albums of Note"
heading is marked up differently in each, and getting three months right
and one wrong is the failure this source is most exposed to.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from verdict.feed import FeedItem
from verdict.sources import bandcamp_best_jazz as best_jazz
from verdict.sources.base import Candidate

CAPTURED = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def feed():
    with open("tests/fixtures/bandcamp_daily_feed.xml", encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def august():
    with open("tests/fixtures/bandcamp_best_jazz_article.html", encoding="utf-8") as h:
        return h.read()


@pytest.fixture(scope="module")
def june():
    with open("tests/fixtures/bandcamp_best_jazz_june.html", encoding="utf-8") as h:
        return h.read()


def parse(page, link="https://daily.bandcamp.com/best-jazz/august-2026"):
    return best_jazz.parse(
        Candidate(FeedItem(link=link, published_at=CAPTURED)), page
    )


# --- discovery ------------------------------------------------------------

def test_the_column_is_picked_out_of_the_site_wide_feed(feed):
    result = best_jazz.discover(lambda _: feed, now=CAPTURED)
    assert result.problems == ()
    assert len(result.candidates) == 1
    assert result.candidates[0].item.link.startswith(best_jazz.SECTION)


def test_the_section_filter_pins_the_host():
    xml = """<rss><channel>
      <item><link>https://daily.bandcamp.com/best-jazz/real</link></item>
      <item><link>https://elsewhere.example/best-jazz/spoofed</link></item>
    </channel></rss>"""
    links = [c.item.link for c in best_jazz.discover(lambda _: xml).candidates]
    assert links == ["https://daily.bandcamp.com/best-jazz/real"]


def test_a_dead_feed_is_a_problem_not_an_exception():
    def explode(_):
        raise OSError("connection reset")

    result = best_jazz.discover(explode, now=CAPTURED)
    assert result.candidates == ()
    assert "feed_unavailable" in result.problems[0].reason


# --- segmentation ---------------------------------------------------------

def test_one_article_yields_a_month_of_albums(august):
    result = parse(august)
    assert len(result.verdicts) == 11
    assert all(v.artist and v.album for v in result.verdicts)


def test_prose_belongs_to_the_heading_above_it(august):
    """`h3` delimits albums here exactly as `h2` does in the roundup."""
    verdicts = {v.artist: v for v in parse(august).verdicts}
    assert "Red Admiral Red Moon Rising" in verdicts["Henry Threadgill’s Zooid"].named_tracks
    assert "Red Admiral Red Moon Rising" not in verdicts["Jakob Bro"].named_tracks


def test_the_album_is_the_italicised_part_of_the_heading(august):
    verdicts = {v.artist: v.album for v in parse(august).verdicts}
    assert verdicts["Jakob Bro"] == "8"
    assert verdicts["Punkt.vrt.Plastik"] == "Kompress"


def test_an_ampersand_in_a_credit_is_unescaped(august):
    """`Linda May Han Oh &amp; Melissa Aldana` must not reach Spotify raw."""
    artists = {v.artist for v in parse(august).verdicts}
    assert "Linda May Han Oh & Melissa Aldana" in artists


# --- the closing courtesy list -------------------------------------------

def test_other_albums_of_note_is_dropped_as_an_h2(august):
    """August marks it up as `<h2>Other Albums of Note:</h2>`."""
    result = parse(august)
    assert not any("Other Albums" in (v.artist or "") for v in result.verdicts)


def test_other_albums_of_note_is_dropped_as_a_strong_wrapped_h3(june):
    """June marks it up as `<h3><strong>Other Albums of Note:</strong></h3>`.

    A regex against the raw markup matches August and misses this one,
    which is why the heading text is stripped before it is tested.
    """
    result = parse(june, link="https://daily.bandcamp.com/best-jazz/june-2026")
    assert len(result.verdicts) == 12
    assert result.problems == ()


def test_the_courtesy_list_does_not_leak_into_the_last_album(august):
    """Without the cut, the final entry's prose swallows the whole list.

    "Ngayaya" is quoted down in the courtesy list, and would otherwise be
    attributed to Danny Keane as a track the writer named.
    """
    last = parse(august).verdicts[-1]
    assert last.artist == "Danny Keane"
    assert "Ngayaya" not in last.named_tracks


# --- entries that are not albums -----------------------------------------

def test_an_entry_naming_no_album_becomes_a_problem(august):
    """August: "Ben Goldberg… released eight albums in August. Yes, eight."

    A real editorial mention with nothing to resolve. Logged rather than
    skipped, so there is a trace of what was left on the table.
    """
    result = parse(august)
    assert [p.reason for p in result.problems] == ["entry_names_no_album"]


def test_an_article_whose_markup_moved_is_a_problem_not_a_quiet_month():
    result = parse("<article>nothing here<article-end>")
    assert result.verdicts == ()
    assert result.problems[-1].reason == "article_shape_changed"


def test_a_missing_page_does_not_raise():
    assert parse(None).verdicts == ()


# --- the verdict ----------------------------------------------------------

def test_selection_is_the_verdict_and_carries_no_score(august):
    for verdict in parse(august).verdicts:
        assert verdict.score is None
        assert verdict.editorial_tier == "best_jazz"


def test_the_genre_is_recorded(august):
    """This source exists to widen the playlist; the field is how that is
    checked rather than hoped."""
    assert all(v.genre == "Jazz" for v in parse(august).verdicts)


def test_every_album_shares_the_articles_date(august):
    assert all(v.published_at == CAPTURED for v in parse(august).verdicts)


def test_named_tracks_are_sparse_and_that_is_expected(august):
    """Four of twelve August entries quote a title, so most albums here
    reach the fallback chain -- fine for jazz, where the positional rungs
    pick discrete tunes rather than movements."""
    named = [v for v in parse(august).verdicts if v.named_tracks]
    assert 0 < len(named) < 6
