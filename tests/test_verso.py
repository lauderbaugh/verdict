"""The fragile layer. If Pitchfork moves, these fail first."""

from __future__ import annotations

import pytest

from verdict.verso import (
    StateShapeError,
    dig,
    extract_state,
    first_child_tagged,
    flat,
    item_reviewed_name,
    tag_of,
)


def test_extract_state_reads_the_blob(roundup_html):
    state = extract_state(roundup_html)
    assert dig(state, "transformed", "article", "body")[0] == "div"


def test_extract_state_survives_embedded_script_tags(roundup_html):
    """raw_decode must not truncate where a non-greedy regex would.

    The blob is ~600KB and carries `</script>` inside string values.
    """
    assert "</script>" in roundup_html
    body = dig(extract_state(roundup_html), "transformed", "article", "body")
    assert len([c for c in body if tag_of(c) == "h2"]) == 13


@pytest.mark.parametrize(
    "html, fragment",
    [
        ("<html>no blob here</html>", "not found"),
        ("<script>__PRELOADED_STATE__ = {oh no", "did not parse"),
        ("<script>__PRELOADED_STATE__ = {}", ""),
    ],
)
def test_extract_state_raises_rather_than_crashing(html, fragment):
    if fragment:
        with pytest.raises(StateShapeError) as excinfo:
            extract_state(html)
        assert fragment in str(excinfo.value)
    else:
        assert extract_state(html) == {}


def test_dig_reports_the_path_it_lost():
    with pytest.raises(StateShapeError) as excinfo:
        dig({"transformed": {}}, "transformed", "article", "body")
    assert "transformed/article/body" in str(excinfo.value)


def test_flat_skips_the_tag_name():
    """Including index 0 produces artifacts like `emThis Mirror Weighs a Ton`."""
    node = ["h2", "Interpol: ", ["em", "This Mirror Weighs a Ton"], " [Partisan]"]
    assert flat(node) == "Interpol: This Mirror Weighs a Ton [Partisan]"
    assert "emThis" not in flat(node)


def test_flat_ignores_attribute_dicts():
    assert flat(["p", {"class": "x"}, "kept"]) == "kept"


def test_first_child_tagged():
    node = ["h2", "Interpol: ", ["em", "Album"]]
    assert flat(first_child_tagged(node, "em")) == "Album"
    assert first_child_tagged(node, "strong") is None


def test_item_reviewed_name_from_ld_json(bnm_html):
    assert item_reviewed_name(bnm_html) == "Aldous Harding: Train on the Island"


def test_item_reviewed_name_absent():
    assert item_reviewed_name("<html></html>") is None
