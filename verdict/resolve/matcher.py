"""Fuzzy matching of quoted candidates against real track names.

This is the step that makes noisy extraction safe. Candidates arrive
mixed with lyrics and scare-quoted prose; only those that match a real
track on the resolved album survive, so a false positive has to look
like an actual track name on that actual record.

`difflib` rather than a fuzzy-matching dependency: the strings are short,
the volume is ~13 albums a week, and a zero-dependency runtime is worth
more here than the speed.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, List, Optional, Sequence, Tuple

#: Above this, two names are the same track. Chosen to tolerate
#: punctuation and casing drift while still separating distinct songs.
TRACK_THRESHOLD = 0.87

#: Album matching is held to a lower bar than track matching. Search
#: already constrains the candidate set by artist and album, so the job
#: here is rejecting a wrong record rather than ranking near-identical
#: ones, and reissues legitimately carry suffixes.
ALBUM_THRESHOLD = 0.80

#: Trailing qualifiers Spotify appends that the prose never includes:
#: "Song - 2011 Remaster", "Song (feat. X)", "Album (Deluxe Edition)".
#: An optional year may sit between the delimiter and the keyword, as in
#: "Song - 2011 Remaster" or "Album (2019 Remaster)".
_SUFFIX_RE = re.compile(
    r"\s*[-–—(\[]\s*(?:\d{2,4}\s+)?("
    r"feat\.?|featuring|with\b|remaster|remastered|deluxe|expanded|"
    r"bonus|edition|version|mono|stereo|live|remix|edit|instrumental|"
    r"anniversary|reissue|explicit|radio edit"
    r")\b.*$",
    re.IGNORECASE,
)

#: Separators Pitchfork uses for shared credits: "Erykah Badu / the
#: Alchemist". Spotify lists such records with one artist per entry, so
#: the joined string matches neither entry on its own.
_CREDIT_SPLIT_RE = re.compile(r"\s*(?:/|&|,|\band\b|\bx\b|\bwith\b)\s*", re.IGNORECASE)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(name: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace.

    Accent stripping matters because non-ASCII artist names are a named
    resolution-failure mode, and the prose and Spotify often disagree on
    diacritics for the same record.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    stripped = _PUNCT_RE.sub(" ", without_accents.casefold())
    return _WS_RE.sub(" ", stripped).strip()


def base_form(name: str) -> str:
    """The name with a trailing qualifier removed, normalized."""
    return normalize(_SUFFIX_RE.sub("", name))


def similarity(left: str, right: str) -> float:
    """Best similarity across the full and suffix-stripped forms.

    Comparing both ways means "San Francisco" still matches the track
    "San Francisco - 2011 Remaster" without treating every remaster as a
    distinct song.
    """
    pairs = {
        (normalize(left), normalize(right)),
        (base_form(left), base_form(right)),
        (base_form(left), normalize(right)),
        (normalize(left), base_form(right)),
    }
    best = 0.0
    for a, b in pairs:
        if not a or not b:
            continue
        if a == b:
            return 1.0
        best = max(best, SequenceMatcher(None, a, b).ratio())
    return best


def best_match(
    candidate: str, options: Iterable[str], threshold: float = TRACK_THRESHOLD
) -> Optional[Tuple[str, float]]:
    """The closest option to `candidate`, or None if nothing clears the bar.

    Returns the score alongside the match so callers can log confidence
    and so near-misses stay auditable rather than being guessed at.
    """
    ranked = rank(candidate, options)
    if ranked and ranked[0][1] >= threshold:
        return ranked[0]
    return None


def rank(candidate: str, options: Iterable[str]) -> List[Tuple[str, float]]:
    """All options scored against `candidate`, best first."""
    scored = [(option, similarity(candidate, option)) for option in options]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def credits(name: str) -> List[str]:
    """A shared credit split into its individual artists.

    Returns the original as a single element when there is nothing to
    split, so callers can always iterate.
    """
    parts = [p.strip() for p in _CREDIT_SPLIT_RE.split(name) if p.strip()]
    return parts or [name]


def artist_similarity(credited: str, listed: Sequence[str]) -> float:
    """Match a verdict's artist credit against an album's artist list.

    Compares the credit whole (against each listed artist and against
    them joined) and split into parts, taking the best. Without the
    split, "Erykah Badu / the Alchemist" scores 0.68 against an album
    credited to both of them and falls below threshold -- a whole class
    of collaborations, not a one-off.

    Splitting cannot loosen matching dangerously: album title similarity
    is scored separately and the caller takes the weaker of the two.
    """
    if not listed:
        return 0.0
    scores = [similarity(credited, ", ".join(listed))]
    for entry in listed:
        scores.append(similarity(credited, entry))
        scores.extend(similarity(part, entry) for part in credits(credited))
    return max(scores)
