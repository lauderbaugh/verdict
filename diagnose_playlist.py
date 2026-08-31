#!/usr/bin/env python3
"""Read-only: compare log/additions.ndjson against the live playlist.

Answers the one question the logs cannot: the run reported adding tracks
and wrote them to the log, but are they actually on the playlist?

Those two can disagree. The dedup window reads `additions.ndjson`, so a
URI logged but never actually added would be skipped by every later run
-- silently, forever. This script is how that shows up.

Writes nothing. Safe to run any time.

    export SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... \
           SPOTIFY_REFRESH_TOKEN=... SPOTIFY_PLAYLIST_ID=...
    python3 diagnose_playlist.py
"""

from __future__ import annotations

import collections
import json
import os
import sys

from verdict.spotify import Spotify, SpotifyError, TokenProvider


def main() -> int:
    required = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET",
                "SPOTIFY_REFRESH_TOKEN", "SPOTIFY_PLAYLIST_ID")
    missing = [n for n in required if not os.environ.get(n)]
    if missing:
        print("missing environment: " + ", ".join(missing), file=sys.stderr)
        return 2

    playlist_id = os.environ["SPOTIFY_PLAYLIST_ID"]
    client = Spotify(TokenProvider(
        client_id=os.environ["SPOTIFY_CLIENT_ID"],
        client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        refresh_token=os.environ["SPOTIFY_REFRESH_TOKEN"],
    ))

    try:
        entries = client.playlist_items(playlist_id)
    except SpotifyError as exc:
        print(f"could not read playlist {playlist_id}: {exc}", file=sys.stderr)
        return 1

    live = {}
    for entry in entries:
        track = entry.get("item") or {}
        if track.get("uri"):
            live[track["uri"]] = track.get("name", "")

    print(f"playlist {playlist_id}: {len(entries)} entries, {len(live)} readable URIs\n")

    try:
        with open("log/additions.ndjson", encoding="utf-8") as handle:
            logged = [json.loads(line) for line in handle if line.strip()]
    except FileNotFoundError:
        print("no log/additions.ndjson", file=sys.stderr)
        return 1

    by_source = collections.defaultdict(lambda: [0, 0])
    missing_rows = []
    for row in logged:
        uri = row.get("uri")
        if not uri:
            continue
        bucket = by_source[row.get("source", "?")]
        if uri in live:
            bucket[0] += 1
        else:
            bucket[1] += 1
            missing_rows.append(row)

    print(f"{'source':26} {'on playlist':>12} {'MISSING':>9}")
    for source in sorted(by_source):
        present, absent = by_source[source]
        print(f"{source:26} {present:>12} {absent:>9}")

    logged_uris = {r.get("uri") for r in logged}
    extra = [u for u in live if u not in logged_uris]
    print(f"\non the playlist but not in the log: {len(extra)}")

    if missing_rows:
        print(f"\n{len(missing_rows)} logged tracks are NOT on the playlist:")
        for row in missing_rows[:25]:
            print(f"  {row.get('run_date')}  {row.get('source', '')[:20]:22} "
                  f"{row.get('artist', '')[:22]:24} {row.get('track', '')[:28]:30} {row.get('uri')}")
        if len(missing_rows) > 25:
            print(f"  ... and {len(missing_rows) - 25} more")
        print("\nTwo explanations, and they look identical from here:")
        print("  1. You removed them from the playlist by hand.")
        print("  2. The run reported adding them and the write did not take.")
        print("\nEither way they are inside the dedup window, so no later run")
        print("will re-add them until they age out of additions.ndjson. For a")
        print("deliberate deletion that is the behaviour you want.")
    else:
        print("\nEvery logged track is on the playlist. The log and Spotify agree.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
