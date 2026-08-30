"""Raw-HTTP Spotify client.

No client library: SPEC rules out any wrapper not confirmed updated after
the February 2026 API change, and the endpoints this project needs are
few enough that raw HTTP is the smaller risk. Standard library only.

Transport is injected so the whole client is testable without network
access or credentials.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"

#: Search caps at 10 (default 5). Asking for more is a 400, not a silent clamp.
SEARCH_LIMIT = 10

#: Album tracks page at 50.
TRACKS_PAGE = 50

# Written with typing generics rather than PEP 604 syntax: these are
# runtime values, not annotations, so `from __future__ import annotations`
# does not defer them and `X | None` would need Python 3.10.
Response = Tuple[int, Dict[str, str], bytes]
Transport = Callable[[str, str, dict, Optional[bytes]], Response]


class SpotifyError(Exception):
    """A request failed in a way retrying will not fix."""


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> Response:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


@dataclass
class TokenProvider:
    """Exchanges the long-lived refresh token for access tokens.

    Authorization code flow WITH a client secret, not PKCE: PKCE rotates
    the refresh token on every use, which would mean writing a new secret
    back to the repo on every run.
    """

    client_id: str
    # repr suppressed: a dataclass repr lands in tracebacks and logs, and
    # these two are the credentials the whole deployment rests on.
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    transport: Transport = urllib_transport
    now: Callable[[], float] = time.monotonic

    _access_token: Optional[str] = field(default=None, repr=False)
    _expires_at: float = field(default=0.0, repr=False)

    def token(self) -> str:
        # 60s of slack so a token cannot expire mid-flight.
        if self._access_token and self.now() < self._expires_at - 60:
            return self._access_token

        basic = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        body = urllib.parse.urlencode(
            {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        ).encode()
        status, _, payload = self.transport(
            "POST",
            TOKEN_URL,
            {
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body,
        )
        if status != 200:
            raise SpotifyError(f"token refresh failed: HTTP {status} {payload[:200]!r}")

        data = json.loads(payload)
        self._access_token = data["access_token"]
        self._expires_at = self.now() + float(data.get("expires_in", 3600))
        return self._access_token


class Spotify:
    """The read endpoints resolution needs.

    Playlist writes live with the rolling-window pass, not here.
    """

    def __init__(
        self,
        tokens: TokenProvider,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ) -> None:
        self.tokens = tokens
        self.transport = transport
        self.sleep = sleep
        self.max_retries = max_retries

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        url = f"{API}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        for attempt in range(self.max_retries + 1):
            headers = {"Authorization": f"Bearer {self.tokens.token()}"}
            status, response_headers, payload = self.transport("GET", url, headers, None)

            if status == 200:
                return json.loads(payload)

            # Rate limited. Retry-After is in seconds and the API expects
            # it to be honoured rather than backed off arbitrarily.
            if status == 429 and attempt < self.max_retries:
                self.sleep(float(response_headers.get("Retry-After", 1)))
                continue

            if status >= 500 and attempt < self.max_retries:
                self.sleep(2**attempt)
                continue

            raise SpotifyError(f"GET {path}: HTTP {status} {payload[:200]!r}")

        raise SpotifyError(f"GET {path}: retries exhausted")

    def search_albums(self, artist: str, album: str) -> list[dict]:
        """Field-filtered album search, best matches first."""
        query = f"artist:{artist} album:{album}"
        data = self.get("/search", {"q": query, "type": "album", "limit": SEARCH_LIMIT})
        return data.get("albums", {}).get("items", []) or []

    def album_tracks(self, album_id: str) -> list[dict]:
        """Every track on an album, following pagination.

        Deluxe editions routinely exceed one 50-track page, and a
        truncated tracklist would silently fail to validate real tracks.
        """
        tracks: list[dict] = []
        offset = 0
        while True:
            page = self.get(
                f"/albums/{album_id}/tracks", {"limit": TRACKS_PAGE, "offset": offset}
            )
            items = page.get("items", []) or []
            tracks.extend(items)
            if not page.get("next") or not items:
                return tracks
            offset += len(items)
