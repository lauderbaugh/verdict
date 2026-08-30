"""Track-name candidates from Pitchfork prose.

Shared by both adapters. The roundup and a full review are written to the
same house style, so the extraction rule is identical; only the way the
prose blocks are located differs.
"""

from __future__ import annotations

import re

from verdict.verso import flat, tag_of

#: Only `p` blocks carry prose. This is an allowlist rather than a
#: denylist of ad/newsletter tags: verified roundup and review bodies
#: between them contain `inline-embed`, `native-ad`, `cm-unit`, `hr`,
#: `ad`, `inline-newsletter` and `journey-inline-newsletter`, and a
#: denylist has to be updated every time Verso adds another. Allowlisting
#: means a block type we have never seen cannot inject text into
#: candidates.
PROSE_TAGS = frozenset({"p"})

#: Track names appear in curly quotes (U+201C/U+201D). Straight-quote
#: matching returns nothing -- Condé Nast uses typographic quotes.
#: Album titles are italicised and never quoted, so `em` content is
#: never a track candidate.
_TRACK_RE = re.compile("“([^”]{1,60})”")

#: Pitchfork puts commas and full stops *inside* the quotes. Stripped
#: from the right only: a leading apostrophe can be part of the title,
#: as in “’Til the End”.
_TRAILING_PUNCT = " ,.;:!?—-’'"


def prose_blocks(nodes) -> list:
    """The prose-carrying blocks among `nodes`."""
    return [node for node in nodes if tag_of(node) in PROSE_TAGS]


def track_candidates(blocks) -> tuple[str, ...]:
    """Quoted phrases from prose blocks, de-duplicated in order.

    These are NOISY on purpose. Verified output mixes song lyrics and
    descriptive phrases in with real track names, and that is fine:
    validation against the real tracklist discards non-tracks, which is
    what makes false positives structurally hard. Filtering cleverly
    here would only risk dropping real titles, and costs accuracy to buy
    nothing -- a non-track that survives to resolution simply fails to
    match and is dropped there.
    """
    text = " ".join(flat(block) for block in blocks)
    seen: dict[str, None] = {}
    for raw in _TRACK_RE.findall(text):
        name = raw.strip().rstrip(_TRAILING_PUNCT).strip()
        if name:
            seen.setdefault(name, None)
    return tuple(seen)
