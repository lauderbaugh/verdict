"""Last.fm play counts, used only to break ties the source did not.

Read-only, free API key, no OAuth. Nothing here may raise: a slow or
broken Last.fm must degrade to the positional fallback, never end the
run, on the same principle as StateShapeError.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

API = "https://ws.audioscrobbler.com/2.0/"

#: Below this album-level playcount the data is treated as absent and we
#: fall through to positional.
#:
#: These albums are days old, so counts are thin and skewed toward
#: whichever track had a pre-release single. At ~1000 scrobbles across a
#: dozen tracks the average track has ~80, enough that a 2:1 gap reflects
#: aggregate listening rather than a handful of individuals. Below it,
#: ordering is mostly noise plus single-release skew, and positional is
#: the more honest answer.
#:
#: A starting point, expected to be tuned: every selection logs both the
#: per-track count and the album total precisely so it can be.
MIN_ALBUM_PLAYCOUNT = 1000

#: Cap on per-track lookups for one album, so a long deluxe edition
#: cannot turn one fallback into fifty requests.
MAX_TRACK_LOOKUPS = 15

TIMEOUT = 10


@dataclass(frozen=True)
class AlbumPlays:
    """Per-track play counts for one album, plus the album total."""

    total: int
    by_track: Dict[str, int]


def urllib_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "verdict/0.1"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


class Lastfm:
    """album.getInfo, with per-track counts filled in where needed."""

    def __init__(
        self,
        api_key: str,
        fetch: Callable[[str], bytes] = urllib_get,
        sleep: Callable[[float], None] = lambda _: None,
        min_album_playcount: int = MIN_ALBUM_PLAYCOUNT,
    ) -> None:
        self.api_key = api_key
        self.fetch = fetch
        self.sleep = sleep
        self.min_album_playcount = min_album_playcount

    def _call(self, method: str, **params) -> Optional[dict]:
        query = urllib.parse.urlencode(
            {"method": method, "api_key": self.api_key, "format": "json", **params}
        )
        try:
            payload = self.fetch(f"{API}?{query}")
            data = json.loads(payload)
        except (OSError, ValueError):
            # Timeouts, resets, malformed JSON. The caller degrades.
            return None
        # Last.fm reports errors in a 200 body.
        return None if not isinstance(data, dict) or "error" in data else data

    def album_plays(self, artist: str, album: str) -> Optional[AlbumPlays]:
        """Play counts for an album, or None if unusable.

        None covers every degrade case with one signal: unreachable,
        malformed, unknown album, or too sparse to rank honestly.
        """
        data = self._call("album.getinfo", artist=artist, album=album)
        if data is None:
            return None

        info = data.get("album")
        if not isinstance(info, dict):
            return None

        total = _int(info.get("playcount"))
        if total < self.min_album_playcount:
            # Sparse enough that ranking would be noise.
            return None

        entries = _track_entries(info)
        if not entries:
            return None

        # album.getInfo documents no per-track playcount -- its track
        # objects carry rank, name, duration and url, and `rank` is
        # tracklist order, not popularity. It is read opportunistically
        # anyway in case a response does carry it, and track.getInfo
        # fills the gap otherwise.
        by_track = {
            _key(entry.get("name")): _int(entry.get("playcount"))
            for entry in entries
            if entry.get("name") and entry.get("playcount") is not None
        }
        if by_track:
            return AlbumPlays(total=total, by_track=by_track)

        return AlbumPlays(total=total, by_track=self._per_track(artist, entries))

    def _per_track(self, artist: str, entries: List[dict]) -> Dict[str, int]:
        """One track.getInfo per track, bounded and failure-tolerant."""
        counts: Dict[str, int] = {}
        for entry in entries[:MAX_TRACK_LOOKUPS]:
            name = entry.get("name")
            if not name:
                continue
            data = self._call("track.getinfo", artist=artist, track=name)
            self.sleep(0.25)  # Last.fm asks for a modest request rate
            if data is None:
                continue  # one missing track must not lose the others
            track = data.get("track")
            if isinstance(track, dict):
                counts[_key(name)] = _int(track.get("playcount"))
        return counts


def _track_entries(info: dict) -> List[dict]:
    tracks = info.get("tracks")
    if isinstance(tracks, dict):
        tracks = tracks.get("track")
    if isinstance(tracks, dict):
        tracks = [tracks]  # a single-track album is not wrapped in a list
    return [t for t in tracks or [] if isinstance(t, dict)]


def _int(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _key(name) -> str:
    from verdict.resolve.matcher import normalize

    return normalize(str(name or ""))
