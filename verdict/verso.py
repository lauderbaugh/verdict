"""Condé Nast Verso page internals.

Everything that touches `__PRELOADED_STATE__` lives here. That blob is an
internal Verso detail with no stability contract (SPEC: "Failure
handling"), so it is quarantined in one module: when Pitchfork changes
their front end, this is the only file that should need to change.

Every entry point raises `StateShapeError` rather than letting a
KeyError or TypeError escape, so the caller can log
`state_shape_changed` and keep the run alive.
"""

from __future__ import annotations

import json
import re

STATE_MARKER = "__PRELOADED_STATE__"

_LD_JSON_RE = re.compile(
    r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)


class StateShapeError(Exception):
    """The Verso state blob was missing, unparseable, or shaped unexpectedly."""


def extract_state(html: str) -> dict:
    """Pull the `__PRELOADED_STATE__` object out of a Verso page.

    Uses `raw_decode` rather than a regex on purpose. The blob is ~600KB
    and contains `</script>` inside string values, so a non-greedy regex
    truncates it mid-object.
    """
    try:
        marker = html.index(STATE_MARKER)
        start = html.index("{", marker)
    except ValueError as exc:
        raise StateShapeError(f"{STATE_MARKER} not found in page") from exc

    try:
        data, _ = json.JSONDecoder().raw_decode(html[start:])
    except json.JSONDecodeError as exc:
        raise StateShapeError(f"state blob did not parse: {exc}") from exc

    if not isinstance(data, dict):
        raise StateShapeError(f"state blob is {type(data).__name__}, expected object")
    return data


def dig(data, *path):
    """Walk a key/index path, raising StateShapeError if it dead-ends.

    Used instead of grepping the blob: keys like `isBestNewMusic` and
    `score` appear many times per page inside recirculation modules for
    unrelated albums, where `score` is a relevance float rather than a
    review score (SPEC: "Adapter 2").
    """
    cursor = data
    for step in path:
        try:
            cursor = cursor[step]
        except (KeyError, IndexError, TypeError) as exc:
            trail = "/".join(str(p) for p in path)
            raise StateShapeError(f"missing key path: {trail}") from exc
    return cursor


def flat(node) -> str:
    """Flatten Verso's nested-list body AST to plain text.

    The AST is `[tag, *children]` where children are strings, nested
    lists, or dicts of attributes. The `node[1:]` slice is required:
    index 0 is the tag name, and including it produces artifacts like
    `emThis Mirror Weighs a Ton`.
    """
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(flat(child) for child in node[1:] if not isinstance(child, dict))
    return ""


def tag_of(node) -> str | None:
    """Return the tag name of an AST node, or None if it isn't one."""
    if isinstance(node, list) and node and isinstance(node[0], str):
        return node[0]
    return None


def children_of(node) -> list:
    """Child nodes of an AST node, with the attribute dicts dropped."""
    if not isinstance(node, list):
        return []
    return [child for child in node[1:] if not isinstance(child, dict)]


def first_child_tagged(node, tag: str):
    """First direct child of `node` with the given tag, else None."""
    for child in children_of(node):
        if tag_of(child) == tag:
            return child
    return None


def ld_json_docs(html: str) -> list[dict]:
    """Every parseable `ld+json` document on the page, flattened.

    `ld+json` is a durable public interface, unlike the state blob, so it
    is preferred wherever it carries what we need. A regex is safe here:
    these blocks are small and do not embed a literal `</script>`.
    """
    docs: list[dict] = []
    for match in _LD_JSON_RE.finditer(html):
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue  # one malformed block must not blind us to the others
        for doc in parsed if isinstance(parsed, list) else [parsed]:
            if isinstance(doc, dict):
                docs.append(doc)
    return docs


def item_reviewed_name(html: str) -> str | None:
    """The `itemReviewed.name` string, formatted "Artist: Album"."""
    for doc in ld_json_docs(html):
        reviewed = doc.get("itemReviewed")
        if isinstance(reviewed, dict):
            name = reviewed.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None
