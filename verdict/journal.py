"""Append-only NDJSON history.

NDJSON rather than a JSON array so appends stay pure, diffs stay clean,
and writing needs no read-parse-rewrite cycle (SPEC: "Logs").

`unmatched.ndjson` is the bug queue: anything the pipeline could not turn
into a playlist entry lands there with a reason, so a silent stall is
always visible as a row rather than as an absence.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path("log")


class Journal:
    """Writes the NDJSON log files. One object per line, append-only."""

    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    #: The only files this class may write. Guards the path built below,
    #: since a stream name flows straight into a filename.
    STREAMS = frozenset({"additions", "removals", "unmatched"})

    def append(self, stream: str, record: dict[str, Any]) -> None:
        """Append one record to `log/{stream}.ndjson`."""
        if stream not in self.STREAMS:
            raise ValueError(f"unknown log stream: {stream!r}")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{stream}.ndjson"
        line = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def unmatched(
        self,
        *,
        source: str,
        artist: str | None,
        album: str | None,
        source_url: str,
        reason: str,
        run_date: date | None = None,
    ) -> None:
        """Record something the pipeline could not use, and why."""
        self.append(
            "unmatched",
            {
                "source": source,
                "artist": artist,
                "album": album,
                "source_url": source_url,
                "reason": reason,
                "run_date": (run_date or date.today()).isoformat(),
            },
        )

    # additions.ndjson and removals.ndjson are written by the resolver and
    # the rolling-window pass, which are not part of this change.
