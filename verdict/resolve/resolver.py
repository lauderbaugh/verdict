"""Verdict -> Spotify album -> validated track URIs.

The shape of this step is what keeps false positives rare. Quoted
candidates are only ever *filtered* against a real tracklist, never
trusted, so a lyric fragment has to coincide with an actual track on the
actual resolved album to survive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

from verdict.models import Verdict
from verdict.resolve.matcher import (
    ALBUM_THRESHOLD,
    TRACK_THRESHOLD,
    artist_similarity,
    best_match,
    similarity,
)
from verdict.spotify import AuthError, Spotify, SpotifyError


@dataclass(frozen=True)
class ResolvedTrack:
    uri: str
    name: str
    #: The match score, or None when this track came from the fallback
    #: and was never matched against anything.
    confidence: Optional[float] = None


@dataclass(frozen=True)
class Resolution:
    """A verdict successfully turned into playable URIs."""

    verdict: Verdict
    album_id: str
    album_name: str
    tracks: Tuple[ResolvedTrack, ...]
    used_fallback: bool = False


@dataclass(frozen=True)
class Unresolved:
    """A verdict that could not be resolved, and why.

    `detail` carries the near-miss so `unmatched.ndjson` records what was
    almost right. SPEC is explicit that near-misses are logged rather
    than guessed at.
    """

    verdict: Verdict
    reason: str
    detail: str = ""


Outcome = Union[Resolution, Unresolved]


def _album_score(verdict: Verdict, album: dict) -> float:
    """How well a search hit matches the verdict.

    The weaker of the artist and album scores, so a right-sounding title
    by the wrong artist cannot rescue itself -- self-titled albums and
    reissues make that a real failure mode.
    """
    names = [a.get("name", "") for a in album.get("artists", []) if isinstance(a, dict)]
    return min(
        artist_similarity(verdict.artist, names),
        similarity(verdict.album, album.get("name", "")),
    )


def pick_album(verdict: Verdict, albums: Sequence[dict]) -> Tuple[Optional[dict], float]:
    """The best search hit and its score, even if it fails the threshold.

    The score is returned regardless so the caller can log a near-miss.
    """
    if not albums:
        return None, 0.0
    scored = sorted(
        ((album, _album_score(verdict, album)) for album in albums),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return scored[0]


def _match_tracks(
    candidates: Sequence[str], tracks: Sequence[dict]
) -> List[ResolvedTrack]:
    """Keep only candidates that match a real track name.

    De-duplicated by URI: two candidates often name the same song, and a
    playlist must not carry it twice.
    """
    # Keyed on first occurrence: an album can carry two tracks with the
    # same name (a multi-disc "Intro", a reprise), and building the map
    # in order let the later one silently win.
    by_name: dict = {}
    for track in tracks:
        if track.get("uri"):
            by_name.setdefault(track.get("name", ""), track)
    matched: dict[str, ResolvedTrack] = {}

    for candidate in candidates:
        hit = best_match(candidate, by_name.keys(), TRACK_THRESHOLD)
        if hit is None:
            continue
        name, score = hit
        track = by_name[name]
        uri = track["uri"]
        # Keep the strongest confidence if two candidates land on one track.
        if uri not in matched or (matched[uri].confidence or 0) < score:
            matched[uri] = ResolvedTrack(uri=uri, name=name, confidence=score)
    return list(matched.values())


def resolve(client: Spotify, verdict: Verdict) -> Outcome:
    """Turn one verdict into validated track URIs."""
    try:
        albums = client.search_albums(verdict.artist, verdict.album)
    except AuthError:
        # Not this verdict's problem: bad credentials fail every request,
        # so the caller stops the run rather than logging one row per
        # album. Must be re-raised before the SpotifyError catch below,
        # which would otherwise swallow it as a per-album failure.
        raise
    except SpotifyError as exc:
        return Unresolved(verdict, "search_failed", str(exc))

    album, score = pick_album(verdict, albums)
    if album is None:
        return Unresolved(verdict, "album_not_found")
    if score < ALBUM_THRESHOLD:
        return Unresolved(
            verdict,
            "album_below_threshold",
            "closest: " + "; ".join(near_misses(verdict, albums)),
        )

    album_id = album.get("id")
    if not album_id:
        return Unresolved(verdict, "album_missing_id")

    try:
        tracks = client.album_tracks(album_id)
    except AuthError:
        raise
    except SpotifyError as exc:
        return Unresolved(verdict, "tracklist_failed", str(exc))

    if not tracks:
        return Unresolved(verdict, "album_has_no_tracks")

    matched = _match_tracks(verdict.named_tracks, tracks)
    if matched:
        return Resolution(
            verdict=verdict,
            album_id=album_id,
            album_name=album.get("name", ""),
            tracks=tuple(matched),
        )

    # Nothing named, or nothing that survived validation. Take the first
    # track only -- never the whole album. At ~13 albums a week over a
    # 4-week window, whole albums would mean a 500-track playlist.
    first = tracks[0]
    if not first.get("uri"):
        return Unresolved(verdict, "first_track_missing_uri")
    return Resolution(
        verdict=verdict,
        album_id=album_id,
        album_name=album.get("name", ""),
        tracks=(ResolvedTrack(uri=first["uri"], name=first.get("name", "")),),
        used_fallback=True,
    )


def near_misses(verdict: Verdict, albums: Sequence[dict], limit: int = 3) -> List[str]:
    """Human-readable runners-up, for the bug queue."""
    scored = sorted(
        ((a.get("name", ""), _album_score(verdict, a)) for a in albums),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return [f"{name} ({score:.2f})" for name, score in scored[:limit]]
