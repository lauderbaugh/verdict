"""The core unit of the system: one publication's opinion on one album."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Verdict:
    """One publication's opinion on one album.

    Source adapters emit these; everything downstream is source-agnostic.

    `score` is deliberately source-native and is NEVER normalized across
    publications (SPEC: "Non-goals"). A Pitchfork 8.6 and some other
    outlet's 4/5 are not comparable and must not be made to look it.

    `named_tracks` holds *candidate* track names lifted from the writeup.
    They are noisy by design -- lyrics and descriptive phrases come along
    with real titles -- and are only trustworthy after being validated
    against a real tracklist during resolution.
    """

    source: str
    artist: str
    album: str
    source_url: str
    published_at: datetime | None = None
    label: str | None = None
    score: float | None = None
    named_tracks: tuple[str, ...] = field(default_factory=tuple)
