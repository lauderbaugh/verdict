"""The rolling 4-week window. State lives in the playlist, not a database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from verdict.playlist.window import (
    WINDOW_DAYS,
    PlaylistItem,
    expired,
    parse_added_at,
    plan,
    read_items,
)

NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


def item(uri, days_ago):
    return PlaylistItem(uri=uri, added_at=NOW - timedelta(days=days_ago))


def entry(uri, days_ago):
    """A `GET /playlists/{id}/items` entry."""
    return {
        "added_at": (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "item": {"uri": uri},
    }


# --- reading --------------------------------------------------------------

def test_reads_added_at_and_the_item_key():
    """`item`, not `track` -- `track` is the pre-February 2026 shape."""
    items = read_items([entry("spotify:track:a", 3)])
    assert items[0].uri == "spotify:track:a"
    assert items[0].added_at.year == 2026


def test_null_items_are_skipped():
    """A removed or unavailable track leaves a null item behind."""
    assert read_items([{"added_at": "2026-08-01T00:00:00Z", "item": None}]) == []
    assert read_items([{"added_at": "2026-08-01T00:00:00Z"}]) == []


def test_parse_added_at_handles_the_z_suffix():
    """fromisoformat did not accept 'Z' before 3.11; this targets 3.9."""
    assert parse_added_at("2026-08-30T12:00:00Z").tzinfo is not None
    assert parse_added_at("nonsense") is None
    assert parse_added_at(None) is None


# --- ageing out -----------------------------------------------------------

def test_items_past_the_window_expire():
    items = [item("old", 29), item("edge", 27), item("fresh", 1)]
    assert expired(items, now=NOW) == ["old"]


def test_exactly_at_the_boundary_is_kept():
    assert expired([item("boundary", WINDOW_DAYS)], now=NOW) == []


def test_items_without_added_at_are_kept():
    """Removing on missing data would silently empty the playlist."""
    assert expired([PlaylistItem(uri="unknown", added_at=None)], now=NOW) == []


# --- planning -------------------------------------------------------------

def test_new_tracks_are_added():
    decision = plan(["a", "b"], [], now=NOW)
    assert decision.add == ("a", "b") and decision.remove == ()


def test_tracks_already_in_the_playlist_are_skipped():
    decision = plan(["a", "b"], [item("a", 2)], now=NOW)
    assert decision.add == ("b",) and decision.skipped == ("a",)


def test_dedup_is_scoped_not_permanent():
    """`additions.ndjson` is a history, not a blocklist.

    A record re-recommended months later, or picked up by a second
    source, must become eligible again.
    """
    recent = plan(["a"], [], recent_uris={"a"}, now=NOW)
    assert recent.add == () and recent.skipped == ("a",)

    # The same URI, with nothing recent, is eligible again.
    later = plan(["a"], [], recent_uris=set(), now=NOW)
    assert later.add == ("a",)


def test_a_uri_ageing_out_is_not_re_added_the_same_run():
    decision = plan(["a"], [item("a", 40)], now=NOW)
    assert decision.remove == ("a",)
    assert decision.add == ()


def test_the_same_track_from_two_sources_is_added_once():
    """The roundup and Best New Music overlap by design."""
    decision = plan(["a", "a", "b"], [], now=NOW)
    assert decision.add == ("a", "b")


def test_expiry_and_addition_together():
    decision = plan(["new"], [item("stale", 40), item("keep", 2)], now=NOW)
    assert decision.add == ("new",)
    assert decision.remove == ("stale",)


# --- timestamp hardening (adversarial QA) ---------------------------------


def test_a_naive_timestamp_does_not_crash_the_comparison():
    """fromisoformat accepts an offsetless string and returns naive.

    Comparing that against the aware cutoff raised TypeError, which
    escaped execute() entirely and ended the job on a traceback.
    """
    parsed = parse_added_at("2026-08-01T12:00:00")
    assert parsed.tzinfo is not None
    assert expired([PlaylistItem("x", parsed)], now=NOW) == ["x"]


def test_lowercase_z_is_accepted():
    """ISO 8601 permits it, and an unreadable timestamp never ages out."""
    assert parse_added_at("2026-08-01T12:00:00z") == parse_added_at("2026-08-01T12:00:00Z")


def test_non_utc_offsets_compare_correctly():
    aware = parse_added_at("2026-07-01T12:00:00+09:00")
    assert expired([PlaylistItem("x", aware)], now=NOW) == ["x"]


def test_duplicate_uris_are_removed_once():
    """Removal is by URI and takes every occurrence, so one slot suffices."""
    items = [item("dup", 40), item("dup", 41), item("other", 40)]
    assert expired(items, now=NOW) == ["dup", "other"]
