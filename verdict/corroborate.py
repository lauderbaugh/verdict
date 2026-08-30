"""Stereogum corroboration.

Not a source. Stereogum contributes no tracks: its weekly list is far too
broad and its Album Of The Week is one record. What it contributes is
evidence about albums the *sources* already found.

Two signals, deliberately kept apart, because they are not the same
claim (SPEC.md, "Agreement must be editorial, not mere presence"):

- **on the list** -- the album appears in "Other albums of note out this
  week", ~126 entries. Mostly a calendar fact: it proves the record came
  out that week, not that anyone judged it.
- **editorial** -- the album *is* Stereogum's Album Of The Week. One a
  week, chosen deliberately. This is real agreement.

Nothing consumes either yet. This is instrumentation: the signals are
recorded on the Verdict and logged so the split can be read from real
data before anything is built on it.

One fetch per run. Any failure degrades to no corroboration and a logged
row -- never a failed run.
"""

from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

FEED_URL = "https://www.stereogum.com/category/franchises/album-of-the-week/feed/"

#: `Album Of The Week: <Artist> <em><Album></em>` in the page's Next.js
#: state. The RSS title strips the markup and leaves no separator at all
#: ("Maripool Rotten Luck"), so the page is required to split them.
_PICK = re.compile(
    r"Album\s+Of\s+The\s+Week:\s*(.*?)\s*<(?:em|i)>(.*?)</(?:em|i)>", re.IGNORECASE
)

#: The weekly list block.
_LIST_HEADING = re.compile(r"Other\s+albums?\s+of\s+note", re.IGNORECASE)

#: `Artist's Album` entries, bullet-separated.
_ENTRY_SPLIT = re.compile(r"[•·]")

_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)

#: Below this the list block is assumed truncated -- the site runs a
#: paywall, and a metered wall may behave differently from a datacentre
#: IP than from a browser. Verified at 126 entries when captured.
MIN_LIST_ENTRIES = 20


@dataclass(frozen=True)
class Corroboration:
    """What Stereogum said about this week."""

    #: (artist, album) of the Album Of The Week pick.
    editorial: Optional[tuple] = None
    #: The weekly list as one normalized blob, matched by containment.
    #:
    #: Entries are prose, not fields -- "Alabama Shakes' I Must Be
    #: Dreaming", "Beastie Boy Mike D's solo debut as Mike D 5D, Thank
    #: You". Both possessive forms appear and the artist is sometimes
    #: described rather than named, so splitting on the possessive loses
    #: roughly half of them. Containment against the normalized block
    #: reads all of them.
    listed_blob: str = ""
    #: Entry count, for the truncation check only.
    entry_count: int = 0

    def __bool__(self) -> bool:
        return bool(self.editorial or self.listed_blob)

    def lists(self, album: str) -> bool:
        """True if the weekly list names this album.

        Word-bounded so a short title cannot match inside a longer word,
        and refused below four characters, where containment stops being
        evidence of anything.
        """
        name = _normalize(album)
        if len(name) < 4 or not self.listed_blob:
            return False
        return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", self.listed_blob) is not None


def _text(html: str) -> str:
    return html_module.unescape(re.sub(r"<[^>]+>", " ", html or ""))


def _normalize(name: str) -> str:
    from verdict.resolve.matcher import normalize

    return normalize(name)


def _blocks(state: dict):
    post = state.get("props", {}).get("pageProps", {}).get("post") or {}
    content = post.get("contentBlocks") or {}
    return post, (content.get("blocks") or [])


def parse_page(page: str) -> Corroboration:
    """Read the pick and the weekly list out of one Album Of The Week page."""
    match = _NEXT_DATA.search(page or "")
    if not match:
        return Corroboration()
    try:
        state = json.loads(match.group(1))
    except ValueError:
        return Corroboration()

    post, blocks = _blocks(state)

    editorial = None
    pick = _PICK.search(post.get("title") or "")
    if pick:
        artist, album = pick.group(1).strip(), pick.group(2).strip()
        if artist and album:
            editorial = (artist, album)

    blob, count = "", 0
    for block in blocks:
        inner = block.get("innerHTML") if isinstance(block, dict) else None
        if not isinstance(inner, str) or not _LIST_HEADING.search(inner):
            continue
        body = _text(inner)
        count += len([e for e in _ENTRY_SPLIT.split(body)[1:] if e.strip()])
        blob += " " + _normalize(body)
    return Corroboration(editorial=editorial, listed_blob=blob.strip(), entry_count=count)


def fetch_week(
    fetch: Callable[[str], str], journal=None, run_date=None
) -> Corroboration:
    """One fetch: the newest Album Of The Week post.

    Returns an empty Corroboration on any failure. Stereogum is evidence
    about someone else's albums, so losing it costs a log field and
    nothing else -- it must never be able to fail a run.
    """
    def note(reason):
        if journal is not None:
            journal.unmatched(
                source="stereogum_corroboration", artist=None, album=None,
                source_url=FEED_URL, reason=reason, run_date=run_date,
            )

    try:
        from verdict.feed import parse_rss

        items = parse_rss(fetch(FEED_URL))
    except Exception as exc:  # noqa: BLE001
        note(f"corroboration_unavailable: {exc}")
        return Corroboration()

    if not items:
        note("corroboration_feed_empty")
        return Corroboration()

    try:
        page = fetch(items[0].link)
    except Exception as exc:  # noqa: BLE001
        note(f"corroboration_unavailable: {exc}")
        return Corroboration()

    result = parse_page(page)
    if not result.editorial and not result.listed_blob:
        note("corroboration_unreadable")
        return result

    if result.entry_count < MIN_LIST_ENTRIES:
        # Short list means the body was truncated -- most likely the
        # paywall behaving differently here than it did when captured.
        # The editorial pick is kept: it comes from the page title, which
        # a paywall does not hide.
        note(f"corroboration_list_truncated: {result.entry_count} entries")
        return Corroboration(editorial=result.editorial)

    return result


def apply(verdict, corroboration: Corroboration):
    """Stamp both signals onto a verdict, returning a new one."""
    import dataclasses

    if not corroboration:
        return verdict
    editorially = False
    if corroboration.editorial:
        _, pick_album = corroboration.editorial
        editorially = _normalize(pick_album) == _normalize(verdict.album)
    return dataclasses.replace(
        verdict,
        corroborated_by_list=corroboration.lists(verdict.album),
        corroborated_editorially=editorially,
    )
