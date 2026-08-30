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

    #: The publication's own editorial ranking of this album within the
    #: week, where it has one -- NPR's "starting_5" against
    #: "lightning_round". Source-native and never compared across
    #: publications, for the same reason `score` is not.
    #:
    #: Nothing reads it yet. It is recorded because it is only available
    #: at parse time, and it is the signal consensus-weighted track
    #: counts would need (see SPEC.md, Later).
    editorial_tier: str | None = None

    #: Corroboration from a publication that contributes no tracks of its
    #: own. Two separate claims, never collapsed into one flag:
    #:
    #: `corroborated_by_list` -- the album appeared on a comprehensive
    #: weekly release list. Mostly a calendar fact; it proves the record
    #: came out, not that anyone judged it.
    #:
    #: `corroborated_editorially` -- another publication made this album
    #: its own editorial pick. This is real agreement.
    #:
    #: Nothing reads either yet (SPEC.md, Later).
    corroborated_by_list: bool = False
    corroborated_editorially: bool = False
