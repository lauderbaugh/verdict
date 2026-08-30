"""NPR New Music Friday, against the real captured podcast feed."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

#: The day the fixture was captured, so the recency window is deterministic.
CAPTURED = datetime(2026, 8, 31, tzinfo=timezone.utc)

#: Early enough that every episode in the fixture clears the window,
#: for the tests that exercise the whole captured archive.
ARCHIVE = datetime(2024, 10, 1, tzinfo=timezone.utc)

from verdict.feed import FeedItem
from verdict.sources import npr_new_music_friday as npr
from verdict.sources.base import Candidate

FEED_PATH = "tests/fixtures/npr_music_podcast_feed.xml"


@pytest.fixture(scope="module")
def feed():
    with open(FEED_PATH, encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def discovery(feed):
    """Every episode in the fixture, for shape and parsing tests."""
    return npr.discover(lambda _: feed, now=ARCHIVE)


@pytest.fixture(scope="module")
def recent(feed):
    """What a run on the capture date would actually pick up."""
    return npr.discover(lambda _: feed, now=CAPTURED)


def episode(discovery, fragment):
    for candidate in discovery.candidates:
        if fragment in (candidate.item.title or ""):
            return candidate
    raise AssertionError(f"no episode matching {fragment!r}")


# --- discovery ------------------------------------------------------------

def test_finds_the_series_within_a_mixed_feed(discovery):
    """The podcast feed carries other NPR Music shows too."""
    assert len(discovery.candidates) == 80
    assert discovery.problems == ()


def test_discovery_is_bounded_to_recent_episodes(recent):
    """The feed holds ~two years; an unbounded run reprocesses all of it.

    Pitchfork's feeds span a few days and need no bound, which is why
    this belongs to the source rather than to the orchestrator.
    """
    assert len(recent.candidates) == 2
    total = sum(len(npr.parse(c).verdicts) for c in recent.candidates)
    assert total == 20


def test_an_undated_episode_is_kept():
    """Dropping on missing data would lose a real week."""
    undated = """<rss version="2.0"><channel><item>
      <title>New Music Friday: undated</title>
      <link>https://npr.org/x</link>
      <description>The Starting 5- Band, 'Record'</description></item></channel></rss>"""
    assert len(npr.discover(lambda _: undated, now=CAPTURED).candidates) == 1


def test_no_page_fetch_is_needed(discovery):
    """The show notes on the feed item are the whole story."""
    assert all(not c.needs_page for c in discovery.candidates)


def test_other_shows_are_not_selected(feed):
    titles = [c.item.title for c in npr.discover(lambda _: feed, now=ARCHIVE).candidates]
    assert all("New Music Friday" in t for t in titles)
    assert not any("Alt.Latino" in t for t in titles)


def test_a_dead_feed_is_a_problem_not_an_exception():
    def explode(url):
        raise OSError("no route to host")

    result = npr.discover(explode, now=CAPTURED)
    assert result.candidates == ()
    assert result.problems[0].reason.startswith("feed_unavailable")


def test_an_empty_feed_is_reported():
    empty = "<rss version='2.0'><channel></channel></rss>"
    assert npr.discover(lambda _: empty, now=CAPTURED).problems[0].reason == "feed_empty"


def test_a_feed_without_the_series_is_reported():
    """A renamed or dropped series is not a quiet week."""
    other = """<rss version="2.0"><channel><item>
      <title>Alt.Latino: something else</title>
      <link>https://npr.org/x</link></item></channel></rss>"""
    assert npr.discover(lambda _: other, now=CAPTURED).problems[0].reason == "series_not_found"


# --- parsing --------------------------------------------------------------

def test_the_captured_week_parses_exactly(discovery):
    """The week matching the Pitchfork roundup fixture."""
    result = npr.parse(episode(discovery, "Aug. 28"))
    pairs = [(v.artist, v.album) for v in result.verdicts]
    assert len(pairs) == 10
    assert ("Mastodon", "Marrow Deep") in pairs
    assert ("Interpol", "This Mirror Weighs a Ton") in pairs
    assert ("Liim", "A Sunflower Garden In Harlem Is Hard To Find") in pairs
    assert result.problems == ()


def test_the_editorial_tier_is_recorded(discovery):
    """Starting 5 against Lightning Round -- editorial, not a threshold."""
    verdicts = npr.parse(episode(discovery, "Aug. 28")).verdicts
    starting = [v.artist for v in verdicts if v.editorial_tier == npr.STARTING_5]
    lightning = [v.artist for v in verdicts if v.editorial_tier == npr.LIGHTNING_ROUND]
    assert len(starting) == 5 and len(lightning) == 5
    assert "Mastodon" in starting
    assert "Billy Strings" in lightning


@pytest.mark.parametrize(
    "fragment, expected",
    [
        # Timestamps plus "Album No. N" plus dashes.
        ("Aug. 28", ("Mastodon", "Marrow Deep")),
        # Dash-delimited, no timestamps.
        ("Nov. 21", ("Tobias Jesso Jr.", "s h i n e")),
        # "Album No.1" with no separator before the artist, label in parens.
        ("June 5", ("Death Cab for Cutie", "I Built You a Tower")),
        # Timestamps with no "Album No." label at all.
        ("Jan. 16", ("The Sha La Das", "Your Picture")),
    ],
)
def test_the_notes_change_shape_between_episodes(discovery, fragment, expected):
    """Four separator conventions appear across the captured range.

    Parsing anchors on the quoted album rather than on any separator,
    because the separators vary and the quoting does not.
    """
    pairs = [(v.artist, v.album) for v in npr.parse(episode(discovery, fragment)).verdicts]
    assert expected in pairs


def test_a_heading_does_not_run_into_an_album_title(discovery):
    """'...Saved Us'The Lightning Round:- Keaton Henson' must split."""
    verdicts = npr.parse(episode(discovery, "Nov. 21")).verdicts
    albums = [v.album for v in verdicts]
    assert "The Fall That Saved Us" in albums
    assert not any("Lightning Round" in a for a in albums)


def test_production_credits_are_not_albums(discovery):
    for verdict in npr.parse(episode(discovery, "Aug. 28")).verdicts:
        assert "Stephen Thompson" not in verdict.artist
        assert "Producer" not in verdict.album


def test_npr_names_no_tracks(discovery):
    """An audio show: the notes give albums, never tracks.

    Every NPR-only album therefore reaches resolution with nothing named
    and falls through to Last.fm, then position.
    """
    for verdict in npr.parse(episode(discovery, "Aug. 28")).verdicts:
        assert verdict.named_tracks == ()


def test_score_is_always_none(discovery):
    """NPR publishes no rating, and nothing is normalized regardless."""
    for candidate in discovery.candidates:
        for verdict in npr.parse(candidate).verdicts:
            assert verdict.score is None


def test_published_at_comes_from_the_feed(discovery):
    verdicts = npr.parse(episode(discovery, "Aug. 28")).verdicts
    assert all(v.published_at is not None for v in verdicts)
    assert verdicts[0].published_at.year == 2026


def test_duplicate_entries_collapse():
    item = FeedItem(
        link="https://npr.org/x", title="New Music Friday: test",
        description="The Starting 5- Band, 'Record'- Band, 'Record'",
    )
    assert len(npr.parse(Candidate(item, needs_page=False)).verdicts) == 1


# --- degradation ----------------------------------------------------------

def test_unreadable_notes_become_a_problem_not_a_quiet_week(discovery):
    """Some episodes leave albums unquoted, or name only artists.

    Guessing where an unquoted title begins would put junk on the
    playlist, so those go to the bug queue instead.
    """
    result = npr.parse(episode(discovery, "Oct. 31"))
    assert result.verdicts == ()
    assert result.problems[0].reason == "notes_shape_unreadable"


def test_every_episode_either_yields_or_reports(discovery):
    """No episode may pass silently."""
    for candidate in discovery.candidates:
        result = npr.parse(candidate)
        assert result.verdicts or result.problems


def test_the_whole_feed_parses_without_raising(discovery):
    total = sum(len(npr.parse(c).verdicts) for c in discovery.candidates)
    reported = sum(1 for c in discovery.candidates if npr.parse(c).problems)
    assert total == 532
    assert reported == 11  # unquoted or artist-only note formats
