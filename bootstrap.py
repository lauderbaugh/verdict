#!/usr/bin/env python3
"""One-time Spotify handshake. Run this locally, once.

Prints a refresh token and creates the playlist, both of which go into
GitHub secrets. Single-user by design: the owner authorises once and the
playlist is public so friends follow the link. Spotify Dev Mode caps at
25 manually added users and extended quota review routinely rejects hobby
projects, so multi-user OAuth is explicitly not built.

Authorization code flow WITH a client secret, not PKCE. PKCE rotates the
refresh token on every use, which would mean writing a new secret back to
the repo on every run.

Usage:

    export SPOTIFY_CLIENT_ID=...
    export SPOTIFY_CLIENT_SECRET=...
    python3 bootstrap.py
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"

#: Must match a redirect URI registered on the Spotify app exactly.
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

#: Modify to add and remove tracks; read to see what is already there.
SCOPES = "playlist-modify-public playlist-read-private"

PLAYLIST_NAME = os.environ.get("VERDICT_PLAYLIST_NAME", "Verdict")


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catches the single redirect Spotify sends back."""

    result: dict = {}

    def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        query = urllib.parse.urlparse(self.path).query
        _CallbackHandler.result = dict(urllib.parse.parse_qsl(query))
        body = b"<html><body><h2>Verdict: you can close this tab.</h2></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # the handshake is noisy enough without access logs


def _authorize(client_id: str) -> str:
    """Open the browser, catch the redirect, return the auth code."""
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
    }
    url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    parsed = urllib.parse.urlparse(REDIRECT_URI)
    server = http.server.HTTPServer((parsed.hostname, parsed.port), _CallbackHandler)

    def serve_until_callback():
        # Not handle_request(): that serves exactly one request, and a
        # stray favicon or preflight would consume it while the real
        # callback went unanswered.
        while not _CallbackHandler.result:
            server.handle_request()

    thread = threading.Thread(target=serve_until_callback, daemon=True)
    thread.start()

    print("Opening your browser to authorise. If it does not open, visit:\n")
    print(f"  {url}\n")
    webbrowser.open(url)
    thread.join(timeout=300)
    server.server_close()

    result = _CallbackHandler.result
    if not result:
        raise SystemExit("timed out waiting for the redirect")
    if "error" in result:
        raise SystemExit(f"authorisation refused: {result['error']}")
    # Guards against a redirect that did not originate from our request.
    if result.get("state") != state:
        raise SystemExit("state mismatch; aborting")
    return result["code"]


def _post(url: str, payload: dict, headers: dict) -> dict:
    body = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _api(path: str, token: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"https://api.spotify.com/v1{path}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def main() -> int:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET first", file=sys.stderr)
        return 2

    print(f"Redirect URI: {REDIRECT_URI}")
    print("This must be registered on your Spotify app, character for character.\n")

    code = _authorize(client_id)
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    tokens = _post(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise SystemExit(f"no refresh token in response: {tokens}")

    playlist = _api(
        "/me/playlists", tokens["access_token"], {"name": PLAYLIST_NAME, "public": True}
    )

    print("\n" + "=" * 66)
    print("Add these as GitHub secrets. The refresh token is long-lived --")
    print("treat it like a password and keep it out of the repo.")
    print("=" * 66)
    print(f"SPOTIFY_REFRESH_TOKEN={refresh_token}")
    print(f"SPOTIFY_PLAYLIST_ID={playlist['id']}")
    print(f"\nPlaylist: {playlist.get('external_urls', {}).get('spotify', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
