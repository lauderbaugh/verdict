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

def test_search_uses_field_filters_and_the_capped_limit():
    """Search caps at 10; asking for more is a 400, not a silent clamp."""
    spotify, api = client([(200, {}, {"albums": {"items": [{"id": "a1"}]}})])
    assert spotify.search_albums("Interpol", "This Mirror Weighs a Ton") == [{"id": "a1"}]
    url = api.calls[0][1]
    assert "artist%3AInterpol" in url
    assert "album%3AThis+Mirror" in url
    assert "limit=10" in url and "type=album" in url


def test_search_tolerates_an_empty_result():
    spotify, _ = client([(200, {}, {"albums": {"items": []}})])
    assert spotify.search_albums("Nobody", "Nothing") == []


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
