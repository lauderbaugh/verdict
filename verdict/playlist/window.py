"""The rolling 4-week window.

There is no database. State lives in the playlist itself: each item
carries `added_at`, which is all the age-out decision needs. The NDJSON
log is history and a bounded dedup set, not the source of truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Set, Tuple

#: Four weeks.
WINDOW_DAYS = 28


@dataclass(frozen=True)
class PlaylistItem:
    """One entry currently in the playlist."""

    uri: str
    added_at: Optional[datetime] = None


@dataclass(frozen=True)
class Plan:
    """What a run intends to do to the playlist."""

    add: Tuple[str, ...] = field(default_factory=tuple)
    remove: Tuple[str, ...] = field(default_factory=tuple)
    skipped: Tuple[str, ...] = field(default_factory=tuple)


def parse_added_at(raw) -> Optional[datetime]:
    """Read Spotify's `added_at`, which is ISO 8601 ending in `Z`.

    Python's `fromisoformat` did not accept `Z` before 3.11, and this
    project supports 3.9. ISO 8601 permits a lowercase `z`, so the
    replacement is case-insensitive -- an unparsed timestamp reads as
    None, and `expired()` keeps such items forever.

    The result is always timezone-aware. `fromisoformat` accepts an
    offsetless string and returns a naive datetime, which then raises
    TypeError against the aware cutoff in `expired()` -- an exception
    that escapes `execute()` entirely and ends the job on a traceback.
    Assuming UTC is right for a field Spotify documents as UTC.
    """
    if not isinstance(raw, str) or not raw:
        return None
    normalised = re.sub(r"[zZ]$", "+00:00", raw.strip())
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_items(payload: Iterable[dict]) -> List[PlaylistItem]:
    """Turn `GET /playlists/{id}/items` entries into PlaylistItems.

    `added_at` sits on the outer object and the track under `item` -- not
    `track`, which is the pre-February 2026 shape.
    """
    items = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        track = entry.get("item")
        uri = track.get("uri") if isinstance(track, dict) else None
        if not uri:
            # A removed or unavailable track leaves a null item behind.
            continue
        items.append(PlaylistItem(uri=uri, added_at=parse_added_at(entry.get("added_at"))))
    return items


def expired(
    items: Sequence[PlaylistItem], now: Optional[datetime] = None, days: int = WINDOW_DAYS
) -> List[str]:
    """URIs older than the window.

    An item with no readable `added_at` is kept. Removing on missing data
    would silently empty the playlist if the field ever moved.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    stale = [i.uri for i in items if i.added_at is not None and i.added_at < cutoff]
    # De-duplicated: removal is by URI and takes every occurrence with it,
    # so a repeated URI would only waste one of the 100 slots per request.
    return list(dict.fromkeys(stale))


def plan(
    candidates: Sequence[str],
    current: Sequence[PlaylistItem],
    recent_uris: Optional[Set[str]] = None,
    now: Optional[datetime] = None,
    days: int = WINDOW_DAYS,
) -> Plan:
    """Decide what to add and remove this run.

    Dedup is scoped, not permanent: a candidate is skipped only if it is
    in the playlist right now, or was added within the trailing window.
    `additions.ndjson` is a history, not a blocklist -- a record
    re-recommended months later, or picked up by a second source, is
    eligible again.
    """
    recent = set(recent_uris or ())
    present = {item.uri for item in current}
    stale = expired(current, now=now, days=days)

    # A URI aging out this run must not be re-added by the same run.
    blocked = present | recent | set(stale)

    add: List[str] = []
    skipped: List[str] = []
    seen: Set[str] = set()
    for uri in candidates:
        if uri in seen:
            continue  # the same track reached us from two sources
        seen.add(uri)
        (skipped if uri in blocked else add).append(uri)

    return Plan(add=tuple(add), remove=tuple(stale), skipped=tuple(skipped))
