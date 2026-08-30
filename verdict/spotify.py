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
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

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


#: Both playlist writes cap at 100 objects per request.
WRITE_BATCH = 100

#: GET /playlists/{id}/items caps at 50 per page. A 4-week window holds
#: roughly 56-64 tracks, so this always paginates.
ITEMS_PAGE = 50


def batched(items: Sequence, size: int) -> Iterator[list]:
    """Split a sequence into chunks of at most `size`."""
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _quotable(value: str) -> str:
    """Strip the double quotes that would terminate a filter value early."""
    return str(value).replace('"', " ").strip()


class SpotifyError(Exception):
    """A request failed."""


class TransientError(SpotifyError):
    """A failure worth retrying: a reset connection, a timeout, DNS."""


class AuthError(SpotifyError):
    """Credentials are wrong. Retrying cannot help and neither can waiting.

    Kept distinct because this fails identically for every request in the
    run: without it, one bad secret produces a row per verdict, each one
    a fresh pointless round trip to the token endpoint.
    """


def header(headers: dict, name: str) -> Optional[str]:
    """Case-insensitive header lookup.

    HTTP field names are case-insensitive (RFC 9110) and HTTP/2 requires
    them lowercased on the wire, so an exact-key lookup silently misses.
    Done here rather than only in `urllib_transport` because `transport`
    is an injection point and a caller's transport must be safe too.
    """
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> Response:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, _lower(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        # An HTTP error is still a response: status and body are meaningful.
        return exc.code, _lower(exc.headers or {}), exc.read()


def _lower(headers) -> Dict[str, str]:
    return {str(k).lower(): v for k, v in headers.items()}


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
        try:
            status, _, payload = self.transport(
                "POST",
                TOKEN_URL,
                {
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                body,
            )
        except OSError as exc:
            # URLError and socket.timeout are both OSError subclasses.
            # Left retryable rather than fatal: a reset connection during
            # a token refresh must not end the whole run.
            raise TransientError(f"token refresh failed: {exc}") from exc

        if status in (400, 401):
            raise AuthError(
                f"token refresh rejected: HTTP {status} {payload[:200]!r}. "
                "Check SPOTIFY_REFRESH_TOKEN, SPOTIFY_CLIENT_ID and "
                "SPOTIFY_CLIENT_SECRET; re-run bootstrap.py if needed."
            )
        if status != 200:
            raise SpotifyError(f"token refresh failed: HTTP {status} {payload[:200]!r}")

        data = json.loads(payload)
        self._access_token = data["access_token"]
        self._expires_at = self.now() + float(data.get("expires_in", 3600))
        return self._access_token


class Spotify:
    """The endpoints this project needs, over raw HTTP.

    Request bodies for the playlist writes were confirmed against the
    live documentation on 2026-08-30 and are recorded in SPEC.md. The
    February 2026 rename moved more than the paths: `DELETE` renamed its
    body key from `tracks` to `items`, so a body inferred from the old
    endpoint is accepted and removes nothing.
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
        return self.request("GET", path, params=params)

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: Any = None,
    ) -> dict:
        """One API call, with auth, retries and rate-limit handling.

        Returns `{}` for a success with an empty body, which the playlist
        writes are entitled to return.
        """
        url = f"{API}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        encoded = None if body is None else json.dumps(body).encode()

        for attempt in range(self.max_retries + 1):
            try:
                request_headers = {"Authorization": f"Bearer {self.tokens.token()}"}
                if encoded is not None:
                    request_headers["Content-Type"] = "application/json"
                status, response_headers, payload = self.transport(
                    method, url, request_headers, encoded
                )
            except (TransientError, OSError) as exc:
                # A reset connection, timeout or DNS failure is transient.
                # Nothing may escape as a bare OSError: SPEC requires the
                # run to degrade to a logged row, never to crash.
                if attempt < self.max_retries:
                    self.sleep(2**attempt)
                    continue
                raise SpotifyError(f"{method} {path}: {exc}") from exc

            if 200 <= status < 300:
                return json.loads(payload) if payload else {}

            # Rate limited. Retry-After is in seconds and the API expects
            # it to be honoured rather than backed off arbitrarily.
            if status == 429 and attempt < self.max_retries:
                self.sleep(float(header(response_headers, "Retry-After") or 1))
                continue

            if status >= 500 and attempt < self.max_retries:
                self.sleep(2**attempt)
                continue

            raise SpotifyError(f"{method} {path}: HTTP {status} {payload[:200]!r}")

        raise SpotifyError(f"{method} {path}: retries exhausted")

    def _search(self, query: str) -> List[dict]:
        data = self.get("/search", {"q": query, "type": "album", "limit": SEARCH_LIMIT})
        return data.get("albums", {}).get("items", []) or []

    def search_albums(self, artist: str, album: str) -> List[dict]:
        """Album search, best matches first.

        Filter values are quoted because an unquoted one ends at the next
        delimiter: `album:Never Enough: Versions` leaves a stray colon in
        the query. When the filtered search finds nothing, one unfiltered
        retry follows -- filters are precise but brittle with punctuation,
        and a miss costs a whole album for the week.

        UNVERIFIED: the quoting behaviour has not been exercised against
        the live endpoint, only against the documented grammar.
        """
        items = self._search(f'artist:"{_quotable(artist)}" album:"{_quotable(album)}"')
        if items:
            return items
        return self._search(f"{_quotable(artist)} {_quotable(album)}")

    def album_tracks(self, album_id: str) -> list[dict]:
        """Every track on an album, following pagination.

        Deluxe editions routinely exceed one 50-track page, and a
        truncated tracklist would silently fail to validate real tracks.
        """
        tracks: list[dict] = []
        offset = 0
        while True:
            page = self.get(
                f"/albums/{urllib.parse.quote(str(album_id), safe='')}/tracks",
                {"limit": TRACKS_PAGE, "offset": offset},
            )
            items = page.get("items", []) or []
            tracks.extend(items)
            if not page.get("next") or not items:
                return tracks
            offset += len(items)

    # --- playlist ------------------------------------------------------

    def create_playlist(self, name: str, public: bool = True) -> dict:
        """Create a playlist owned by the authorising user.

        `POST /me/playlists`; the old `/users/{id}/playlists` form is
        gone. Public by default, which is the point -- friends follow the
        link rather than being added as users.
        """
        return self.request("POST", "/me/playlists", body={"name": name, "public": public})

    def playlist_items(self, playlist_id: str) -> List[dict]:
        """Every item in a playlist, following pagination.

        Each entry carries `added_at` on the outer object and the track
        under `item` -- not `track`, which is the pre-February shape.
        """
        items: List[dict] = []
        offset = 0
        while True:
            page = self.request(
                "GET",
                f"/playlists/{urllib.parse.quote(str(playlist_id), safe='')}/items",
                params={"limit": ITEMS_PAGE, "offset": offset},
            )
            batch = page.get("items", []) or []
            items.extend(batch)
            if not page.get("next") or not batch:
                return items
            offset += len(batch)

    def add_items(self, playlist_id: str, uris: Sequence[str]) -> List[str]:
        """Append tracks, in batches of 100. Returns the snapshot ids."""
        path = f"/playlists/{urllib.parse.quote(str(playlist_id), safe='')}/items"
        snapshots = []
        for chunk in batched(list(uris), WRITE_BATCH):
            result = self.request("POST", path, body={"uris": chunk})
            snapshots.append(result.get("snapshot_id", ""))
        return snapshots

    def remove_items(self, playlist_id: str, uris: Sequence[str]) -> List[str]:
        """Remove tracks, in batches of 100.

        The body key is `items` holding objects, NOT `tracks` holding
        strings. Removal is by URI and takes every occurrence with it,
        which is only safe because URIs are de-duplicated before they are
        ever added.
        """
        path = f"/playlists/{urllib.parse.quote(str(playlist_id), safe='')}/items"
        snapshots = []
        for chunk in batched(list(uris), WRITE_BATCH):
            result = self.request(
                "DELETE", path, body={"items": [{"uri": uri} for uri in chunk]}
            )
            snapshots.append(result.get("snapshot_id", ""))
        return snapshots
