"""Last.fm client. Nothing here may raise; degrade to None instead."""

from __future__ import annotations

import json

from verdict.resolve.lastfm import MIN_ALBUM_PLAYCOUNT, Lastfm


def lastfm(fetch, **kw):
    """A client that does not really sleep.

    Injected rather than defaulted: the default is time.sleep so that a
    call site which forgets it throttles anyway.
    """
    return Lastfm("k", fetch, sleep=lambda _: None, **kw)


def responses(*payloads):
    """A fetcher serving canned bodies, recording the URLs requested."""
    calls = []
    queue = list(payloads)

    def fetch(url):
        calls.append(url)
        if not queue:
            raise AssertionError(f"unexpected request: {url}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return json.dumps(item).encode()

    fetch.calls = calls
    return fetch


def album_info(total, tracks):
    return {"album": {"playcount": str(total),
                      "tracks": {"track": [{"name": n} for n in tracks]}}}


def test_album_level_playcount_gates_on_sparsity():
    """Days-old albums are thin and skewed toward the pre-release single."""
    fetch = responses(album_info(MIN_ALBUM_PLAYCOUNT - 1, ["A", "B"]))
    assert lastfm(fetch).album_plays("Artist", "Album") is None
    assert len(fetch.calls) == 1  # no per-track lookups for a sparse album


def test_per_track_counts_are_fetched_when_the_album_omits_them():
    """album.getInfo documents no per-track playcount; track.getInfo has it."""
    fetch = responses(
        album_info(50_000, ["One", "Two"]),
        {"track": {"playcount": "900"}},
        {"track": {"playcount": "40"}},
    )
    plays = lastfm(fetch).album_plays("Artist", "Album")
    assert plays.total == 50_000
    assert plays.by_track == {"one": 900, "two": 40}
    assert "track.getinfo" in fetch.calls[1]


def test_inline_playcount_is_used_without_extra_requests():
    """Used opportunistically in case a response does carry it."""
    payload = {"album": {"playcount": "50000", "tracks": {"track": [
        {"name": "One", "playcount": "900"}, {"name": "Two", "playcount": "40"}]}}}
    fetch = responses(payload)
    plays = lastfm(fetch).album_plays("Artist", "Album")
    assert plays.by_track == {"one": 900, "two": 40}
    assert len(fetch.calls) == 1


def test_a_network_failure_degrades_to_none():
    """A slow or broken Last.fm must never end the run."""
    assert lastfm(responses(OSError("timeout"))).album_plays("A", "B") is None


def test_a_lastfm_error_body_degrades_to_none():
    """Errors arrive in a 200 body."""
    fetch = responses({"error": 6, "message": "Album not found"})
    assert lastfm(fetch).album_plays("A", "B") is None


def test_malformed_json_degrades_to_none():
    def fetch(url):
        return b"<html>not json</html>"

    assert lastfm(fetch).album_plays("A", "B") is None


def test_one_failed_track_lookup_does_not_lose_the_others():
    fetch = responses(
        album_info(50_000, ["One", "Two"]),
        OSError("reset"),
        {"track": {"playcount": "40"}},
    )
    plays = lastfm(fetch).album_plays("Artist", "Album")
    assert plays.by_track == {"two": 40}


def test_track_lookups_are_capped():
    """A long deluxe edition must not become fifty requests."""
    from verdict.resolve.lastfm import MAX_TRACK_LOOKUPS

    fetch = responses(
        album_info(50_000, [f"T{i}" for i in range(40)]),
        *[{"track": {"playcount": "10"}} for _ in range(MAX_TRACK_LOOKUPS)],
    )
    lastfm(fetch).album_plays("Artist", "Album")
    assert len(fetch.calls) == 1 + MAX_TRACK_LOOKUPS


def test_a_single_track_album_is_not_wrapped_in_a_list():
    payload = {"album": {"playcount": "50000",
                         "tracks": {"track": {"name": "Only", "playcount": "12"}}}}
    plays = lastfm(responses(payload)).album_plays("A", "B")
    assert plays.by_track == {"only": 12}


def test_the_api_key_is_sent_and_json_requested():
    fetch = responses(album_info(50_000, []))
    Lastfm("secret-key", fetch, sleep=lambda _: None).album_plays("Artist", "Album")
    assert "api_key=secret-key" in fetch.calls[0]
    assert "format=json" in fetch.calls[0]


def test_the_default_sleep_actually_throttles():
    """Regression: the default was a no-op, so production never paced.

    run.py constructs Lastfm(key) with no sleep argument, so a no-op
    default meant 15 track.getInfo calls as fast as the socket allowed.
    """
    import time

    assert Lastfm("k", responses()).sleep is time.sleep


def test_per_track_lookups_are_paced():
    slept = []
    fetch = responses(
        album_info(50_000, ["One", "Two", "Three"]),
        *[{"track": {"playcount": "10"}} for _ in range(3)],
    )
    Lastfm("k", fetch, sleep=slept.append).album_plays("Artist", "Album")
    assert len(slept) == 3
    assert all(delay > 0 for delay in slept)
