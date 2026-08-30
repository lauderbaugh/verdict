"""Raw-HTTP client. Transport is faked; nothing here touches the network."""

from __future__ import annotations

import json

import pytest

from verdict.spotify import Spotify, SpotifyError, TokenProvider


class FakeTransport:
    """Serves canned responses and records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        status, response_headers, payload = self.responses.pop(0)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return status, response_headers, payload


def token_provider(transport=None, **kw):
    transport = transport or FakeTransport(
        [(200, {}, {"access_token": "tok", "expires_in": 3600})]
    )
    return TokenProvider(
        client_id="id", client_secret="secret", refresh_token="refresh",
        transport=transport, **kw,
    )


def client(responses, sleeps=None, **kw):
    """A Spotify client whose API calls return `responses`."""
    api = FakeTransport(responses)
    spotify = Spotify(token_provider(), transport=api,
                      sleep=(sleeps.append if sleeps is not None else lambda _: None), **kw)
    return spotify, api


# --- auth -----------------------------------------------------------------

def test_token_uses_basic_auth_with_the_client_secret():
    """Authorization code flow WITH a secret, not PKCE.

    PKCE rotates the refresh token per use, which would mean writing a new
    secret back to the repo every run.
    """
    transport = FakeTransport([(200, {}, {"access_token": "tok", "expires_in": 3600})])
    assert token_provider(transport).token() == "tok"
    _, url, headers, body = transport.calls[0]
    assert url == "https://accounts.spotify.com/api/token"
    assert headers["Authorization"].startswith("Basic ")
    assert b"grant_type=refresh_token" in body


def test_token_is_cached_until_it_nears_expiry():
    clock = [1000.0]
    transport = FakeTransport([
        (200, {}, {"access_token": "first", "expires_in": 3600}),
        (200, {}, {"access_token": "second", "expires_in": 3600}),
    ])
    provider = token_provider(transport, now=lambda: clock[0])

    assert provider.token() == "first"
    clock[0] += 3000
    assert provider.token() == "first"      # still fresh
    clock[0] += 600                          # inside the 60s slack
    assert provider.token() == "second"
    assert len(transport.calls) == 2


def test_token_failure_is_an_error_not_a_silent_none():
    transport = FakeTransport([(400, {}, b'{"error":"invalid_grant"}')])
    with pytest.raises(SpotifyError, match="token refresh failed"):
        token_provider(transport).token()


# --- requests -------------------------------------------------------------

def test_rate_limit_honours_retry_after():
    sleeps = []
    spotify, _ = client(
        [(429, {"Retry-After": "7"}, b"{}"), (200, {}, {"ok": True})], sleeps
    )
    assert spotify.get("/x") == {"ok": True}
    assert sleeps == [7.0]


def test_server_errors_back_off_then_succeed():
    sleeps = []
    spotify, _ = client([(500, {}, b""), (503, {}, b""), (200, {}, {"ok": True})], sleeps)
    assert spotify.get("/x") == {"ok": True}
    assert sleeps == [1, 2]  # 2**0, 2**1


def test_persistent_failure_raises():
    spotify, _ = client([(500, {}, b"")] * 4)
    with pytest.raises(SpotifyError, match="HTTP 500"):
        spotify.get("/x")


def test_client_errors_do_not_retry():
    spotify, api = client([(404, {}, b"nope")])
    with pytest.raises(SpotifyError, match="HTTP 404"):
        spotify.get("/x")
    assert len(api.calls) == 1


# --- endpoints ------------------------------------------------------------

def test_search_uses_quoted_field_filters_and_the_capped_limit():
    """Search caps at 10; asking for more is a 400, not a silent clamp.

    Values are quoted so a title's punctuation cannot end the filter
    early -- `album:Never Enough: Versions` leaves a stray colon.
    """
    spotify, api = client([(200, {}, {"albums": {"items": [{"id": "a1"}]}})])
    assert spotify.search_albums("Interpol", "This Mirror Weighs a Ton") == [{"id": "a1"}]
    url = api.calls[0][1]
    assert "artist%3A%22Interpol%22" in url
    assert "album%3A%22This+Mirror" in url
    assert "limit=10" in url and "type=album" in url
    assert len(api.calls) == 1  # no fallback needed when the filter hits


def test_colon_in_an_album_title_stays_inside_the_filter():
    """'Never Enough: Versions' is in the captured roundup."""
    spotify, api = client([(200, {}, {"albums": {"items": [{"id": "a1"}]}})])
    spotify.search_albums("Turnstile", "Never Enough: Versions")
    assert "album%3A%22Never+Enough%3A+Versions%22" in api.calls[0][1]


def test_empty_filtered_search_retries_unfiltered():
    """Filters are precise but brittle; a miss costs a whole album."""
    spotify, api = client([
        (200, {}, {"albums": {"items": []}}),
        (200, {}, {"albums": {"items": [{"id": "found"}]}}),
    ])
    assert spotify.search_albums("Sigur Rós", "Á") == [{"id": "found"}]
    assert len(api.calls) == 2
    assert "artist%3A" not in api.calls[1][1]


def test_search_tolerates_both_attempts_finding_nothing():
    spotify, _ = client([
        (200, {}, {"albums": {"items": []}}),
        (200, {}, {"albums": {"items": []}}),
    ])
    assert spotify.search_albums("Nobody", "Nothing") == []


def test_quotes_in_a_title_cannot_terminate_the_filter():
    spotify, api = client([(200, {}, {"albums": {"items": [{"id": "a"}]}})])
    spotify.search_albums('Some "Band"', 'A "Quoted" Title')
    assert api.calls[0][1].count("%22") == 4  # only the four we opened


def test_album_id_is_url_quoted():
    spotify, api = client([(200, {}, {"items": [{"uri": "u"}], "next": None})])
    spotify.album_tracks("weird/id")
    assert "weird%2Fid" in api.calls[0][1]


def test_album_tracks_follows_pagination():
    """Deluxe editions exceed one 50-track page.

    A truncated tracklist would silently fail to validate real tracks.
    """
    page_one = {"items": [{"uri": f"u{i}"} for i in range(50)], "next": "more"}
    page_two = {"items": [{"uri": "u50"}], "next": None}
    spotify, api = client([(200, {}, page_one), (200, {}, page_two)])

    tracks = spotify.album_tracks("album1")
    assert len(tracks) == 51
    assert "offset=0" in api.calls[0][1]
    assert "offset=50" in api.calls[1][1]


def test_album_tracks_stops_on_a_single_page():
    spotify, api = client([(200, {}, {"items": [{"uri": "u0"}], "next": None})])
    assert len(spotify.album_tracks("a")) == 1
    assert len(api.calls) == 1


def test_credentials_are_not_in_the_repr():
    """A dataclass repr reaches tracebacks and logs."""
    text = repr(token_provider())
    assert "secret" not in text and "refresh" not in text
    assert "client_id='id'" in text


# --- header casing and transient failures ---------------------------------

def test_retry_after_is_read_case_insensitively():
    """HTTP/2 requires lowercase field names on the wire.

    A case-sensitive lookup silently misses, sleeps 1s, burns every retry
    in ~3s and hammers an endpoint that is already rate limiting us.
    """
    sleeps = []
    spotify, _ = client(
        [(429, {"retry-after": "30"}, b"{}"), (200, {}, {"ok": True})], sleeps
    )
    assert spotify.get("/x") == {"ok": True}
    assert sleeps == [30.0]


def test_retry_after_still_works_title_cased():
    sleeps = []
    spotify, _ = client(
        [(429, {"Retry-After": "12"}, b"{}"), (200, {}, {"ok": True})], sleeps
    )
    spotify.get("/x")
    assert sleeps == [12.0]


def test_urllib_transport_lowercases_header_keys():
    import email.message

    message = email.message.Message()
    message["Retry-After"] = "30"
    from verdict.spotify import _lower, header

    assert _lower(message) == {"retry-after": "30"}
    assert header({"RETRY-AFTER": "9"}, "Retry-After") == "9"
    assert header({}, "Retry-After") is None


def test_connection_errors_are_retried_then_wrapped():
    """A bare OSError must never escape: SPEC forbids crashing the run."""
    import urllib.error

    class Flaky:
        def __init__(self, failures):
            self.failures = failures
            self.calls = 0

        def __call__(self, *args):
            self.calls += 1
            if self.calls <= self.failures:
                raise urllib.error.URLError("Connection reset by peer")
            return 200, {}, b'{"ok": true}'

    sleeps = []
    flaky = Flaky(2)
    spotify = Spotify(token_provider(), transport=flaky, sleep=sleeps.append)
    assert spotify.get("/x") == {"ok": True}
    assert sleeps == [1, 2]

    persistent = Spotify(token_provider(), transport=Flaky(99), sleep=lambda _: None)
    with pytest.raises(SpotifyError, match="Connection reset"):
        persistent.get("/x")


def test_timeouts_are_transient_too():
    import socket

    def always_timeout(*args):
        raise socket.timeout("timed out")

    spotify = Spotify(token_provider(), transport=always_timeout, sleep=lambda _: None)
    with pytest.raises(SpotifyError):
        spotify.get("/x")


def test_token_network_failure_is_transient_not_fatal():
    import urllib.error

    from verdict.spotify import TransientError

    def refuse(*args):
        raise urllib.error.URLError("dns failure")

    with pytest.raises(TransientError):
        token_provider(refuse).token()


def test_token_network_failure_is_retried_by_get():
    """A reset during token refresh must not end the run."""
    import urllib.error

    calls = {"n": 0}

    def flaky_token(*args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("reset")
        return 200, {}, json.dumps({"access_token": "t", "expires_in": 3600}).encode()

    provider = TokenProvider("id", "secret", "refresh", transport=flaky_token)
    api = FakeTransport([(200, {}, {"ok": True})])
    spotify = Spotify(provider, transport=api, sleep=lambda _: None)
    assert spotify.get("/x") == {"ok": True}
