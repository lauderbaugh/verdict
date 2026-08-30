#!/usr/bin/env python3
"""Throwaway: verify the playlist write path against the live API.

The February 2026 rename moved more than the paths. `DELETE` renamed its
body key from `tracks` to `items`, so a body inferred from the old
endpoint is *accepted* and removes nothing -- a silent no-op you would
not notice for weeks. The bodies used here were read from the live docs,
but read is not the same as ran.

Creates a scratch playlist, adds one known URI, reads it back, removes
it, reads back again, then unfollows the scratch playlist. Your real
playlist is never touched.

    export SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... SPOTIFY_REFRESH_TOKEN=...
    python3 probe_write_path.py

Delete this file once the write path is confirmed.
"""

from __future__ import annotations

import os
import sys

from verdict.spotify import Spotify, SpotifyError, TokenProvider

#: Rick Astley, "Never Gonna Give You Up" -- a stable, always-available URI.
KNOWN_URI = "spotify:track:4PTG3Z6ehGkBFwjybzWkR8"


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    return condition


def main() -> int:
    required = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN")
    if any(not os.environ.get(name) for name in required):
        print("set " + ", ".join(required), file=sys.stderr)
        return 2

    client = Spotify(
        TokenProvider(
            client_id=os.environ["SPOTIFY_CLIENT_ID"],
            client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
            refresh_token=os.environ["SPOTIFY_REFRESH_TOKEN"],
        )
    )

    ok = True
    playlist = client.create_playlist("verdict scratch (safe to delete)", public=False)
    playlist_id = playlist["id"]
    print(f"\nscratch playlist: {playlist_id}\n")

    try:
        print("POST /playlists/{id}/items  body {'uris': [...]}")
        client.add_items(playlist_id, [KNOWN_URI])
        uris = [i["item"]["uri"] for i in client.playlist_items(playlist_id)
                if i.get("item")]
        ok &= check("add wrote the track", uris == [KNOWN_URI], f"got {uris}")

        print("\nDELETE /playlists/{id}/items  body {'items': [{'uri': ...}]}")
        client.remove_items(playlist_id, [KNOWN_URI])
        after = [i["item"]["uri"] for i in client.playlist_items(playlist_id)
                 if i.get("item")]
        ok &= check("remove actually removed it", after == [],
                    f"still present: {after}" if after else "playlist empty")

        print("\nGET /playlists/{id}/items shape")
        client.add_items(playlist_id, [KNOWN_URI])
        raw = client.playlist_items(playlist_id)
        ok &= check("entries carry added_at", bool(raw and raw[0].get("added_at")))
        ok &= check("track is under 'item', not 'track'",
                    bool(raw and "item" in raw[0] and "track" not in raw[0]))

        print("\nGET /search with quoted field filters")
        hits = client.search_albums("Turnstile", "Never Enough: Versions")
        ok &= check("colon-bearing title does not break the query",
                    isinstance(hits, list), f"{len(hits)} hits")
    finally:
        # Unfollowing your own playlist is how Spotify deletes it.
        try:
            client.request("DELETE", f"/playlists/{playlist_id}/followers")
            print(f"\ncleaned up scratch playlist {playlist_id}")
        except SpotifyError as exc:
            print(f"\ncould not clean up {playlist_id}: {exc}", file=sys.stderr)

    print("\n" + ("all checks passed" if ok else "SOME CHECKS FAILED -- do not ship"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
