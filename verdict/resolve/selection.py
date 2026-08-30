"""Choosing which tracks from an album reach the playlist.

Three steps, in order, per album:

1. Tracks the source named, validated against the real tracklist.
2. If that yields fewer than the minimum, fill from Last.fm play counts.
3. If Last.fm is unavailable or too sparse, fill positionally.

Every track records how it was chosen, so the selection can be judged
from `additions.ndjson` rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

#: Two is enough to represent an album; four is where a 4-week window
#: stops being a playlist and starts being a discography.
MIN_TRACKS = 2
MAX_TRACKS = 4

#: Below this, a track is an interlude or a skit rather than a song.
SHORT_TRACK_MS = 90_000

#: Track 1 is disproportionately an intro, so it is never preferred --
#: only reached when nothing longer is left.
PREFERRED_POSITIONS = (2, 4)

NAMED = "named"
LASTFM = "lastfm"
POSITIONAL = "positional"


@dataclass(frozen=True)
class ResolvedTrack:
    """One track chosen for the playlist, and why."""

    uri: str
    name: str
    selection: str
    #: named
    confidence: Optional[float] = None
    #: lastfm
    playcount: Optional[int] = None
    album_playcount: Optional[int] = None
    #: positional
    rule: Optional[str] = None
    #: 1-indexed position on the album, for any selection method
    position: Optional[int] = None


def _position_of(track: dict, tracks: Sequence[dict]) -> Optional[int]:
    for index, candidate in enumerate(tracks, start=1):
        if candidate.get("uri") == track.get("uri"):
            return index
    return None


def _is_short(track: dict) -> bool:
    duration = track.get("duration_ms")
    return isinstance(duration, int) and 0 < duration < SHORT_TRACK_MS


def positional_order(tracks: Sequence[dict]) -> List[tuple]:
    """Tracks in fallback preference order, each with the rule that placed it.

    Positions 2 and 4 first, then the rest ascending, with track 1 last:
    it is disproportionately an intro, so it is a last resort rather than
    a default. Tracks under 90 seconds sort behind everything else, so
    they are only reached when no longer alternative remains.
    """
    ranked = []
    for index, track in enumerate(tracks, start=1):
        if index in PREFERRED_POSITIONS:
            rule = f"position_{index}"
            rank = PREFERRED_POSITIONS.index(index)
        elif index == 1:
            rule = "first_track_last_resort"
            rank = len(tracks) + 10
        else:
            rule = "next_available"
            rank = len(PREFERRED_POSITIONS) + index
        # Short tracks sort behind every full-length one.
        ranked.append(((1 if _is_short(track) else 0, rank), track, rule, index))

    ranked.sort(key=lambda row: row[0])
    return [
        (track, rule if not _is_short(track) else f"{rule}_short_accepted", index)
        for _, track, rule, index in ranked
    ]


def select(
    named: Sequence[ResolvedTrack],
    tracks: Sequence[dict],
    plays=None,
) -> List[ResolvedTrack]:
    """Apply the selection chain to one album.

    `named` are already-validated matches in prose order; `tracks` is the
    real tracklist; `plays` is an `AlbumPlays` or None.
    """
    chosen: List[ResolvedTrack] = list(named[:MAX_TRACKS])
    taken = {track.uri for track in chosen}

    if len(chosen) >= MIN_TRACKS:
        return chosen

    remaining = [t for t in tracks if t.get("uri") and t["uri"] not in taken]

    # Step 2: Last.fm, only when the source named too few. Never called
    # when a source named enough -- the source's own judgement wins.
    if plays is not None and plays.by_track:
        from verdict.resolve.matcher import normalize

        ranked = sorted(
            (
                (plays.by_track.get(normalize(t.get("name", "")), 0), t)
                for t in remaining
            ),
            key=lambda row: row[0],
            reverse=True,
        )
        for count, track in ranked:
            if len(chosen) >= MIN_TRACKS:
                break
            if count <= 0:
                break  # unranked tracks are no better than positional
            chosen.append(
                ResolvedTrack(
                    uri=track["uri"],
                    name=track.get("name", ""),
                    selection=LASTFM,
                    playcount=count,
                    album_playcount=plays.total,
                    position=_position_of(track, tracks),
                )
            )
            taken.add(track["uri"])

    if len(chosen) >= MIN_TRACKS:
        return chosen

    # Step 3: positional.
    for track, rule, index in positional_order(tracks):
        if len(chosen) >= MIN_TRACKS:
            break
        if track["uri"] in taken:
            continue
        chosen.append(
            ResolvedTrack(
                uri=track["uri"],
                name=track.get("name", ""),
                selection=POSITIONAL,
                rule=rule,
                position=index,
            )
        )
        taken.add(track["uri"])

    return chosen
