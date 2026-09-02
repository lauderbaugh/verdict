"""Bandcamp Daily's Album of the Day, against the real captured feed and page."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from verdict.feed import FeedItem, parse_rss
from verdict.sources import bandcamp_daily
from verdict.sources.base import Candidate

#: The day the fixtures were captured, so the window is deterministic.
CAPTURED = datetime(2026, 9, 1, tzinfo=timezone.utc)
#: Four days after capture, so the trailing end of the feed has aged out.
LATER = datetime(2026, 9, 5, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def feed():
    with open("tests/fixtures/bandcamp_daily_feed.xml", encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def page():
    with open("tests/fixtures/bandcamp_album_of_the_day.html", encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def discovery(feed):
    return bandcamp_daily.discover(lambda _: feed, now=CAPTURED)


def candidate_for(discovery, slug):
    for candidate in discovery.candidates:
        if slug in candidate.item.link:
            return candidate
    raise AssertionError(f"no candidate for {slug!r}")


def verdict_for(discovery, page, slug):
    return bandcamp_daily.parse(candidate_for(discovery, slug), page).verdicts[0]


# --- discovery ------------------------------------------------------------

def test_the_feed_carries_far_more_than_album_of_the_day(feed):
    """One site-wide feed for every section, so most of it is not ours."""
    items = parse_rss(feed)
    assert len(items) == 36
    picks = [i for i in items if bandcamp_daily.SECTION in i.link]
    assert len(picks) == 12


def test_only_album_of_the_day_is_taken(discovery):
    assert discovery.problems == ()
    assert discovery.candidates
    assert all(
        bandcamp_daily.SECTION in c.item.link for c in discovery.candidates
    )


def test_discovery_is_bounded_to_the_recent_window(discovery):
    """12 picks in the feed, 11 inside the window on the captured day."""
    assert len(discovery.candidates) == 11


def test_a_narrower_window_takes_fewer(feed):
    narrow = bandcamp_daily.discover(lambda _: feed, now=LATER).candidates
    assert 0 < len(narrow) < 11


def test_every_pick_needs_its_page(discovery):
    """Unlike NPR, the feed teaser names no tracks and no album alone."""
    assert all(c.needs_page for c in discovery.candidates)


def test_a_dead_feed_is_a_problem_not_an_exception():
    def explode(_):
        raise OSError("connection reset")

    result = bandcamp_daily.discover(explode, now=CAPTURED)
    assert result.candidates == ()
    assert "feed_unavailable" in result.problems[0].reason


# --- the headline ---------------------------------------------------------

def test_every_captured_headline_parses(discovery):
    """12 of 12 on one pattern -- the reason this source is worth having."""
    for candidate in discovery.candidates:
        assert bandcamp_daily._HEADLINE.match(candidate.item.title), candidate.item.title


def test_a_collaboration_credit_may_contain_commas(discovery):
    """Splitting on the first comma would call this artist "Henry Threadgill"."""
    candidate = candidate_for(discovery, "henry-threadgill")
    verdict = bandcamp_daily.parse(candidate, None).verdicts[0]
    assert verdict.artist == "Henry Threadgill, Vijay Iyer & Dafnis Prieto"
    assert verdict.album == "Fifteen"


def test_an_album_title_may_contain_commas(discovery):
    """And splitting on the last comma would truncate this album."""
    candidate = candidate_for(discovery, "scrambled-eggs")
    verdict = bandcamp_daily.parse(candidate, None).verdicts[0]
    assert verdict.album == "Happier Together, Filthier Than Ever: The Best Of"
    assert verdict.artist == "Scrambled Eggs"


def test_an_unreadable_headline_becomes_a_problem():
    """No quotes means no telling where the credit ends and the title begins."""
    candidate = Candidate(
        FeedItem(link="https://daily.bandcamp.com/album-of-the-day/x", title="Just Words")
    )
    result = bandcamp_daily.parse(candidate, "<article><article-end>")
    assert result.verdicts == ()
    assert result.problems[0].reason == "headline_unparsed"


def test_the_feed_title_stands_in_for_a_missing_ld_json():
    """The page's `ld+json` moving must not cost us the pick."""
    candidate = Candidate(
        FeedItem(
            link="https://daily.bandcamp.com/album-of-the-day/x",
            title="Lusine, “Melting Days”",
        )
    )
    verdict = bandcamp_daily.parse(candidate, "<article><article-end>").verdicts[0]
    assert (verdict.artist, verdict.album) == ("Lusine", "Melting Days")


# --- the verdict ----------------------------------------------------------

def test_the_pick_itself_is_the_verdict(discovery, page):
    """No score exists, and inventing one to mean "chosen" is normalisation."""
    verdict = verdict_for(discovery, page, "floating-points")
    assert verdict.score is None
    assert verdict.editorial_tier == "album_of_the_day"


def test_the_genre_is_recorded_in_bandcamps_own_words(discovery, page):
    """Source-native, like `score`. This source exists to widen the playlist,
    and this field is how that gets checked against the log rather than hoped."""
    assert verdict_for(discovery, page, "floating-points").genre == "Soundtrack"


def test_the_date_comes_from_the_feed(discovery, page):
    verdict = verdict_for(discovery, page, "floating-points")
    assert verdict.published_at == datetime(2026, 9, 1, 13, 44, 42, tzinfo=timezone.utc)


# --- named tracks ---------------------------------------------------------

def test_tracks_are_lifted_from_prose_in_curly_quotes(discovery, page):
    """The same house convention Pitchfork and Paste use; `prose.py` unchanged."""
    named = verdict_for(discovery, page, "floating-points").named_tracks
    assert "Opening of the Jar" in named
    assert "Falling to Earth" in named


def test_a_track_name_wrapped_in_a_link_still_reads(discovery, page):
    """Bandcamp links track names to their own player: `“<a>Nuits Sonores</a>”`."""
    assert "Nuits Sonores" in verdict_for(discovery, page, "floating-points").named_tracks


def test_candidates_stay_noisy(discovery, page):
    """"Nuits Sonores" is off a different record entirely.

    It is kept on purpose. Validation against the real tracklist is what
    discards non-tracks; filtering here would cost real titles to buy
    nothing, since a non-track simply fails to match at resolution.
    """
    named = verdict_for(discovery, page, "floating-points").named_tracks
    assert "Nuits Sonores" in named


def test_the_italicised_album_is_not_a_track(discovery, page):
    """Bandcamp quotes the album in the headline but italicises it in prose."""
    verdict = verdict_for(discovery, page, "floating-points")
    assert verdict.album == "Mere Mortals"
    assert "Mere Mortals" not in verdict.named_tracks


def test_the_embedded_tracklist_is_deliberately_not_read(discovery, page):
    """Every page ships the album's *complete* tracklist in the player JSON.

    Lifting it would claim the writer named every track. A manifest is
    not a recommendation -- the same reason Stereogum is not a source.
    """
    verdict = verdict_for(discovery, page, "floating-points")
    assert "Gift from the Gods" in page  # it is right there in the page
    assert "Gift from the Gods" not in verdict.named_tracks
    assert len(verdict.named_tracks) < 10


def test_a_missing_page_yields_no_tracks_rather_than_raising():
    candidate = Candidate(
        FeedItem(
            link="https://daily.bandcamp.com/album-of-the-day/x",
            title="Lusine, “Melting Days”",
        )
    )
    verdict = bandcamp_daily.parse(candidate, None).verdicts[0]
    assert verdict.named_tracks == ()


def test_the_section_filter_pins_the_host():
    """Discovery decides what gets fetched, so the filter is a prefix.

    A bare path-segment test would admit any host that happened to use
    the same path.
    """
    feed = """<rss><channel>
      <item><link>https://daily.bandcamp.com/album-of-the-day/real</link>
        <title>Real Artist, &#8220;Real Album&#8221;</title></item>
      <item><link>https://elsewhere.example/album-of-the-day/spoofed</link>
        <title>Spoofed, &#8220;Nope&#8221;</title></item>
    </channel></rss>"""
    links = [c.item.link for c in bandcamp_daily.discover(lambda _: feed).candidates]
    assert links == ["https://daily.bandcamp.com/album-of-the-day/real"]
