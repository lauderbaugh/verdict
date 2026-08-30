"""The weekly run, end to end against fakes. Nothing here hits the network."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from verdict.journal import Journal
from verdict.models import Verdict
from verdict.run import RunReport, dedupe_verdicts, execute, gather
from verdict.sources import pitchfork_roundup
from verdict.spotify import SpotifyError

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)
RUN_DATE = date(2026, 8, 30)

FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>13 New Albums</title>
<link>https://pitchfork.com/story/13-new-albums-you-should-listen-to-now/</link>
<pubDate>Fri, 28 Aug 2026 12:00:00 +0000</pubDate></item>
</channel></rss>"""


class FakeSpotify:
    """Records writes so a test can assert what reached the playlist."""

    def __init__(self, items=None, search=None, tracks=None, fail=None):
        self.items = items or []
        self._search = search
        self._tracks = tracks or [{"name": "Track One", "uri": "spotify:track:1"}]
        self.fail = fail or set()
        self.added, self.removed = [], []

    def search_albums(self, artist, album):
        if self._search is not None:
            return self._search
        return [{"id": "alb", "name": album, "artists": [{"name": artist}]}]

    def album_tracks(self, album_id):
        return self._tracks

    def playlist_items(self, playlist_id):
        if "read" in self.fail:
            raise SpotifyError("read boom")
        return self.items

    def add_items(self, playlist_id, uris):
        if "add" in self.fail:
            raise SpotifyError("add boom")
        self.added.extend(uris)

    def remove_items(self, playlist_id, uris):
        if "remove" in self.fail:
            raise SpotifyError("remove boom")
        self.removed.extend(uris)


def roundup_page(albums):
    body = ["div"]
    for artist, album, quoted in albums:
        body.append(["h2", f"{artist}: ", ["em", album], " [Label]"])
        body.append(["p", f"The single is “{quoted}”." if quoted else "No tracks named."])
    state = {"transformed": {"article": {"body": body}}}
    return f"<script>window.__PRELOADED_STATE__ = {json.dumps(state)};</script>"


def fetcher_for(pages):
    def fetch(url):
        if url in pages:
            return pages[url]
        raise AssertionError(f"unexpected fetch: {url}")
    return fetch


def read_log(tmp_path, name):
    path = tmp_path / f"{name}.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --- gather ---------------------------------------------------------------

def test_gather_collects_verdicts(tmp_path):
    link = "https://pitchfork.com/story/13-new-albums-you-should-listen-to-now/"
    pages = {pitchfork_roundup.FEED_URL: FEED,
             link: roundup_page([("Interpol", "This Mirror", "Track One")])}
    verdicts = gather(pitchfork_roundup, Journal(tmp_path), fetcher_for(pages),
                      sleep=lambda _: None, run_date=RUN_DATE)
    assert [v.artist for v in verdicts] == ["Interpol"]


def test_a_dead_feed_is_logged_not_raised(tmp_path):
    """SPEC: never crash the run, never silently stop updating."""
    def explode(url):
        raise OSError("no route to host")

    journal = Journal(tmp_path)
    assert gather(pitchfork_roundup, journal, explode, lambda _: None, RUN_DATE) == []
    rows = read_log(tmp_path, "unmatched")
    assert rows[0]["reason"].startswith("feed_unavailable")


def test_a_page_that_will_not_fetch_is_logged(tmp_path):
    link = "https://pitchfork.com/story/13-new-albums-you-should-listen-to-now/"

    def fetch(url):
        if url == pitchfork_roundup.FEED_URL:
            return FEED
        raise OSError("timeout")

    journal = Journal(tmp_path)
    assert gather(pitchfork_roundup, journal, fetch, lambda _: None, RUN_DATE) == []
    assert read_log(tmp_path, "unmatched")[0]["reason"].startswith("fetch_failed")


def test_a_moved_state_blob_is_logged_as_a_shape_change(tmp_path):
    link = "https://pitchfork.com/story/13-new-albums-you-should-listen-to-now/"
    pages = {pitchfork_roundup.FEED_URL: FEED, link: "<html>no blob</html>"}
    journal = Journal(tmp_path)
    assert gather(pitchfork_roundup, journal, fetcher_for(pages),
                  lambda _: None, RUN_DATE) == []
    assert read_log(tmp_path, "unmatched")[0]["reason"] == "state_shape_changed"


# --- dedup ----------------------------------------------------------------

def test_the_same_album_from_two_sources_collapses():
    """The roundup and BNM overlap by design; resolving twice wastes a search."""
    roundup = Verdict(source="pitchfork_roundup", artist="Dinosaur Jr.",
                      album="There Near", source_url="u1")
    bnm = Verdict(source="pitchfork_bnm", artist="dinosaur jr.",
                  album="There Near", source_url="u2", score=8.6)
    merged = dedupe_verdicts([roundup, bnm])
    assert len(merged) == 1
    assert merged[0].score == 8.6  # the scored verdict is the richer one


def test_a_verdict_naming_tracks_beats_one_that_does_not():
    bare = Verdict(source="s", artist="A", album="B", source_url="u")
    named = Verdict(source="s", artist="A", album="B", source_url="u",
                    named_tracks=("One",))
    assert dedupe_verdicts([bare, named])[0].named_tracks == ("One",)


# --- execute --------------------------------------------------------------

def _run(tmp_path, spotify, albums=None, **kw):
    link = "https://pitchfork.com/story/13-new-albums-you-should-listen-to-now/"
    pages = {pitchfork_roundup.FEED_URL: FEED,
             "https://pitchfork.com/feed/feed-album-reviews/rss":
                 "<rss version='2.0'><channel></channel></rss>",
             link: roundup_page(albums or [("Interpol", "This Mirror", "Track One")])}
    return execute(spotify, "pl1", Journal(tmp_path), fetcher_for(pages),
                   sleep=lambda _: None, now=NOW, run_date=RUN_DATE, **kw)


def test_a_full_run_adds_and_logs(tmp_path):
    spotify = FakeSpotify()
    report = _run(tmp_path, spotify)
    assert spotify.added == ["spotify:track:1"]
    assert report.added == 1 and report.resolved == 1 and not report.errors

    addition = read_log(tmp_path, "additions")[0]
    assert addition["uri"] == "spotify:track:1"
    assert addition["artist"] == "Interpol"
    assert addition["run_date"] == "2026-08-30"


def test_expired_tracks_are_removed_and_logged(tmp_path):
    old = (NOW - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    spotify = FakeSpotify(items=[{"added_at": old, "item": {"uri": "spotify:track:old"}}])
    report = _run(tmp_path, spotify)
    assert spotify.removed == ["spotify:track:old"]
    assert report.removed == 1
    assert read_log(tmp_path, "removals")[0]["uri"] == "spotify:track:old"


def test_an_unresolvable_album_lands_in_the_bug_queue(tmp_path):
    spotify = FakeSpotify(search=[])
    report = _run(tmp_path, spotify)
    assert report.unresolved == 1 and spotify.added == []
    reasons = [row["reason"] for row in read_log(tmp_path, "unmatched")]
    assert any(r.startswith("album_not_found") for r in reasons)


def test_a_failed_playlist_read_stops_before_writing(tmp_path):
    """Better to do nothing than to write against an unknown state."""
    spotify = FakeSpotify(fail={"read"})
    report = _run(tmp_path, spotify)
    assert spotify.added == [] and spotify.removed == []
    assert report.errors and "read" in report.errors[0]


def test_a_failed_add_is_reported_and_not_logged_as_added(tmp_path):
    """The log must never claim a track the playlist does not have."""
    spotify = FakeSpotify(fail={"add"})
    report = _run(tmp_path, spotify)
    assert report.added == 0 and report.errors
    assert read_log(tmp_path, "additions") == []


def test_a_track_added_recently_is_not_added_again(tmp_path):
    journal = Journal(tmp_path)
    journal.addition(source="s", track="t", artist="a", album="b",
                     uri="spotify:track:1", source_url="u", run_date=RUN_DATE)
    spotify = FakeSpotify()
    link = "https://pitchfork.com/story/13-new-albums-you-should-listen-to-now/"
    pages = {pitchfork_roundup.FEED_URL: FEED,
             "https://pitchfork.com/feed/feed-album-reviews/rss":
                 "<rss version='2.0'><channel></channel></rss>",
             link: roundup_page([("Interpol", "This Mirror", "Track One")])}
    report = execute(spotify, "pl1", journal, fetcher_for(pages),
                     sleep=lambda _: None, now=NOW, run_date=RUN_DATE)
    assert spotify.added == [] and report.skipped == 1


# --- the drain guard (adversarial QA) -------------------------------------


class StockedPlaylist(FakeSpotify):
    """A playlist already holding four weeks of tracks."""

    def playlist_items(self, playlist_id):
        return [
            {"added_at": (NOW - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "item": {"uri": f"spotify:track:{d}"}}
            for d in (29, 30, 35, 2, 3)
        ]


def test_dead_feeds_do_not_drain_a_healthy_playlist(tmp_path):
    """The failure this module exists to prevent.

    Age-out ran even when discovery produced nothing, so a broken run
    removed a week of tracks and reported success. Repeated weekly it
    empties the playlist with CI green throughout.
    """
    def dead(url):
        raise OSError("no route to host")

    spotify = StockedPlaylist()
    report = execute(spotify, "pl1", Journal(tmp_path), dead,
                     sleep=lambda _: None, now=NOW, run_date=RUN_DATE)
    assert spotify.removed == []
    assert spotify.added == []
    assert report.errors  # and therefore a non-zero exit


def test_an_empty_feed_is_logged_rather_than_silent(tmp_path):
    """A 200 with no items left no trace anywhere."""
    def empty(url):
        return "<rss version='2.0'><channel></channel></rss>"

    spotify = StockedPlaylist()
    report = execute(spotify, "pl1", Journal(tmp_path), empty,
                     sleep=lambda _: None, now=NOW, run_date=RUN_DATE)
    reasons = {row["reason"] for row in read_log(tmp_path, "unmatched")}
    assert "feed_empty" in reasons
    assert spotify.removed == [] and report.errors


def test_resolution_failing_outright_also_blocks_writes(tmp_path):
    """13 verdicts and nothing resolved is a broken run, not a quiet week."""
    spotify = StockedPlaylist(search=[])
    report = _run(tmp_path, spotify)
    assert report.verdicts == 1 and report.resolved == 0
    assert spotify.removed == [] and spotify.added == []
    assert report.errors


def test_a_healthy_run_still_ages_out(tmp_path):
    """The guard must not block ordinary maintenance."""
    spotify = StockedPlaylist()
    report = _run(tmp_path, spotify)
    assert report.added == 1
    assert set(spotify.removed) == {"spotify:track:29", "spotify:track:30",
                                    "spotify:track:35"}
    assert not report.errors


def test_a_bad_credential_stops_the_run_once(tmp_path):
    """One cause, one row -- not one row per album.

    Seen live: an invalid refresh token produced 14 identical rows and 14
    round trips, burying the single real cause under copies of itself.
    """
    from verdict.spotify import AuthError

    class Unauthorised(FakeSpotify):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def search_albums(self, artist, album):
            self.attempts += 1
            raise AuthError("token refresh rejected: HTTP 400")

    spotify = Unauthorised()
    report = _run(tmp_path, spotify)
    assert spotify.attempts == 1
    assert spotify.added == [] and spotify.removed == []
    assert report.errors and "token refresh rejected" in report.errors[0]
