"""Append-only NDJSON history.

NDJSON rather than a JSON array so appends stay pure, diffs stay clean,
and writing needs no read-parse-rewrite cycle (SPEC: "Logs").

`unmatched.ndjson` is the bug queue: anything the pipeline could not turn
into a playlist entry lands there with a reason, so a silent stall is
always visible as a row rather than as an absence.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

DEFAULT_ROOT = Path("log")


class Journal:
    """Writes the NDJSON log files. One object per line, append-only."""

    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    #: The only files this class may write. Guards the path built below,
    #: since a stream name flows straight into a filename.
    STREAMS = frozenset({"additions", "removals", "skips", "unmatched"})

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

    def addition(
        self,
        *,
        source: str,
        track: str,
        artist: str,
        album: str,
        uri: str,
        source_url: str,
        score: Optional[float] = None,
        match_confidence: Optional[float] = None,
        selection: Optional[str] = None,
        playcount: Optional[int] = None,
        album_playcount: Optional[int] = None,
        rule: Optional[str] = None,
        position: Optional[int] = None,
        corroborated_by_list: Optional[bool] = None,
        corroborated_editorially: Optional[bool] = None,
        editorial_tier: Optional[str] = None,
        run_date: Optional[date] = None,
    ) -> None:
        """Record a track added to the playlist.

        Doubles as the dedup history. Scoped, not permanent: the window
        only consults the trailing 28 days, so a record recommended again
        months later is eligible again.
        """
        self.append(
            "additions",
            {
                "source": source,
                "track": track,
                "artist": artist,
                "album": album,
                "uri": uri,
                "source_url": source_url,
                "score": score,
                "match_confidence": match_confidence,
                # How this track was chosen: named / lastfm / positional.
                # playcount and album_playcount are recorded so the
                # sparsity threshold can be tuned from real numbers
                # rather than guessed at a second time.
                "selection": selection,
                "playcount": playcount,
                "album_playcount": album_playcount,
                "rule": rule,
                "position": position,
                # Corroboration from a publication contributing no tracks.
                # Kept apart on purpose: appearing on a comprehensive
                # weekly list is a calendar fact, being another
                # publication's own pick is agreement. Nothing reads
                # either yet -- they are here to be measured.
                "corroborated_by_list": corroborated_by_list,
                "corroborated_editorially": corroborated_editorially,
                "editorial_tier": editorial_tier,
                "run_date": (run_date or date.today()).isoformat(),
            },
        )

    def removal(self, *, uri: str, aged_out_date: Optional[date] = None) -> None:
        """Record a track aged out of the window."""
        self.append(
            "removals",
            {
                "uri": uri,
                "aged_out_date": (aged_out_date or date.today()).isoformat(),
            },
        )

    def skip(
        self,
        *,
        source: str,
        artist: str,
        album: str,
        track: str,
        reason: str,
        run_date: Optional[date] = None,
    ) -> None:
        """Record a track the selection chain declined to consider.

        Not the bug queue: a skipped interlude is the filter working.
        Kept separate so the false-positive rate can be read without
        wading through real failures.
        """
        self.append(
            "skips",
            {
                "source": source,
                "artist": artist,
                "album": album,
                "track": track,
                "reason": reason,
                "run_date": (run_date or date.today()).isoformat(),
            },
        )

    def recent_uris(self, within_days: int, today: Optional[date] = None) -> set:
        """URIs added within the trailing `within_days`.

        The dedup set, deliberately bounded. Reading the whole file would
        make `additions.ndjson` a permanent blocklist and a record could
        never return.
        """
        path = self.root / "additions.ndjson"
        if not path.exists():
            return set()

        cutoff = (today or date.today()) - timedelta(days=within_days)
        uris = set()
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    added = date.fromisoformat(record["run_date"])
                except (ValueError, KeyError, TypeError):
                    # A malformed line is a logging bug, not a reason to
                    # lose the whole dedup set.
                    continue
                if added >= cutoff and record.get("uri"):
                    uris.add(record["uri"])
        return uris
