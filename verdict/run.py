"""The weekly run.

Discovery -> parse -> resolve -> playlist, with every failure written to
`unmatched.ndjson` rather than raised. SPEC is explicit on both halves of
this: never crash the run, and never let the playlist silently stop
updating.
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from verdict.journal import Journal
from verdict.models import Verdict
from verdict.playlist.window import WINDOW_DAYS, plan, read_items
from verdict.resolve.matcher import normalize
from verdict.resolve.resolver import Resolution, ResolvedTrack, Unresolved, resolve
from verdict.sources import npr_new_music_friday, pitchfork_bnm, pitchfork_roundup
from verdict.spotify import AuthError, Spotify, SpotifyError
from verdict.verso import StateShapeError

SOURCES = (pitchfork_roundup, pitchfork_bnm, npr_new_music_friday)

#: Politeness delay between page fetches. The feed reports 100 requests
#: per window and this run needs far fewer, so there is no reason to rush.
FETCH_DELAY = 1.0

USER_AGENT = "verdict/0.1 (+https://github.com/lauderbaugh/verdict)"


def fetch(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


@dataclass
class RunReport:
    """What happened, for the caller to print or assert on."""

    verdicts: int = 0
    resolved: int = 0
    unresolved: int = 0
    added: int = 0
    removed: int = 0
    skipped: int = 0
    problems: int = 0
    errors: List[str] = field(default_factory=list)


def gather(
    source,
    journal: Journal,
    fetcher: Callable[[str], str] = fetch,
    sleep: Callable[[float], None] = time.sleep,
    run_date: Optional[date] = None,
) -> List[Verdict]:
    """Every verdict one source yields this week.

    The source owns discovery -- its feed, its selection rules, and
    whether a page fetch follows at all. This function knows only how to
    fetch a URL and where to write a row.

    Nothing here may raise. A dead feed, an unfetchable page, or a state
    blob that has moved all become rows.
    """
    verdicts: List[Verdict] = []
    discovery = source.discover(fetcher)

    for problem in discovery.problems:
        journal.unmatched(
            source=source.NAME, artist=problem.artist, album=problem.album,
            source_url=problem.source_url, reason=problem.reason, run_date=run_date,
        )

    for candidate in discovery.candidates:
        page = None
        if candidate.needs_page:
            try:
                page = fetcher(candidate.url)
            except Exception as exc:  # noqa: BLE001
                journal.unmatched(
                    source=source.NAME, artist=None, album=None,
                    source_url=candidate.url, reason=f"fetch_failed: {exc}",
                    run_date=run_date,
                )
                continue
            finally:
                sleep(FETCH_DELAY)

        try:
            result = source.parse(candidate, page)
        except StateShapeError:
            # The state blob is the fragile interface and has no
            # stability contract. Skip the item, keep the run alive.
            journal.unmatched(
                source=source.NAME, artist=None, album=None,
                source_url=candidate.url, reason="state_shape_changed",
                run_date=run_date,
            )
            continue

        for problem in result.problems:
            journal.unmatched(
                source=source.NAME, artist=problem.artist, album=problem.album,
                source_url=problem.source_url, reason=problem.reason,
                run_date=run_date,
            )
        verdicts.extend(result.verdicts)

    return verdicts


def dedupe_verdicts(verdicts: Sequence[Verdict]) -> List[Verdict]:
    """Collapse the same album arriving from more than one source.

    The roundup and Best New Music overlap heavily by design. Resolving
    both costs two searches for one playlist entry, so they are merged
    before resolution rather than after. The richer verdict wins: a
    scored one over an unscored one, then one naming tracks over one
    that names none.
    """
    best: Dict[Tuple[str, str], Verdict] = {}
    for verdict in verdicts:
        key = (normalize(verdict.artist), normalize(verdict.album))
        incumbent = best.get(key)
        if incumbent is None or _richer(verdict, incumbent):
            best[key] = verdict
    return list(best.values())


def _richer(candidate: Verdict, incumbent: Verdict) -> bool:
    return (
        (candidate.score is not None, len(candidate.named_tracks))
        > (incumbent.score is not None, len(incumbent.named_tracks))
    )


def execute(
    client: Spotify,
    playlist_id: str,
    journal: Journal,
    fetcher: Callable[[str], str] = fetch,
    sleep: Callable[[float], None] = time.sleep,
    now: Optional[datetime] = None,
    run_date: Optional[date] = None,
    lastfm=None,
) -> RunReport:
    """One complete weekly run."""
    now = now or datetime.now(timezone.utc)
    run_date = run_date or now.date()
    report = RunReport()

    verdicts = []
    for source in SOURCES:
        verdicts.extend(gather(source, journal, fetcher, sleep, run_date))

    verdicts = dedupe_verdicts(verdicts)
    report.verdicts = len(verdicts)

    resolutions: List[Resolution] = []
    for verdict in verdicts:
        try:
            outcome = resolve(client, verdict, lastfm)
        except AuthError as exc:
            # Fails identically for every remaining verdict, so stop here.
            # Logging one row per album would bury the single real cause
            # under a dozen copies of itself.
            report.errors.append(str(exc))
            return report
        if isinstance(outcome, Unresolved):
            report.unresolved += 1
            journal.unmatched(
                source=verdict.source, artist=verdict.artist, album=verdict.album,
                source_url=verdict.source_url,
                reason=f"{outcome.reason}: {outcome.detail}" if outcome.detail
                else outcome.reason,
                run_date=run_date,
            )
            continue
        resolutions.append(outcome)
    report.resolved = len(resolutions)

    # Nothing resolved means discovery or resolution is broken -- both
    # feeds down, a changed slug, a dead search endpoint. Age-out would
    # still run and quietly drain a healthy playlist a week at a time,
    # green the whole way, which is exactly the "playlist silently stops
    # updating" outcome this module exists to prevent.
    #
    # Reporting it is not enough: by the time an exit code is returned the
    # removal has already happened. The writes have to be skipped, so the
    # worst case is stale tracks lingering an extra week.
    if not resolutions:
        report.errors.append(
            f"resolved 0 of {report.verdicts} verdicts; skipping playlist "
            "writes rather than draining it"
        )
        return report

    # Candidate URIs, with the verdict each came from for the log.
    origin: Dict[str, Tuple[Verdict, "ResolvedTrack"]] = {}
    candidates: List[str] = []
    for resolution in resolutions:
        for track in resolution.tracks:
            if track.uri not in origin:
                origin[track.uri] = (resolution.verdict, track)
                candidates.append(track.uri)

    try:
        current = read_items(client.playlist_items(playlist_id))
    except SpotifyError as exc:
        report.errors.append(f"playlist read failed: {exc}")
        return report

    decision = plan(
        candidates, current, recent_uris=journal.recent_uris(WINDOW_DAYS, run_date), now=now
    )
    report.skipped = len(decision.skipped)

    if decision.remove:
        try:
            client.remove_items(playlist_id, decision.remove)
            for uri in decision.remove:
                journal.removal(uri=uri, aged_out_date=run_date)
            report.removed = len(decision.remove)
        except SpotifyError as exc:
            report.errors.append(f"removal failed: {exc}")

    if decision.add:
        try:
            client.add_items(playlist_id, decision.add)
            for uri in decision.add:
                verdict, track = origin[uri]
                journal.addition(
                    source=verdict.source, track=track.name, artist=verdict.artist,
                    album=verdict.album, uri=uri, source_url=verdict.source_url,
                    score=verdict.score, match_confidence=track.confidence,
                    selection=track.selection, playcount=track.playcount,
                    album_playcount=track.album_playcount, rule=track.rule,
                    position=track.position, run_date=run_date,
                )
            report.added = len(decision.add)
        except SpotifyError as exc:
            report.errors.append(f"addition failed: {exc}")

    return report


def main() -> int:
    """Entry point for the weekly job.

    Credentials come from the environment; nothing is read from disk and
    nothing is written back, so the refresh token never lands in the repo.
    """
    import os
    import sys

    from verdict.resolve.lastfm import Lastfm
    from verdict.spotify import TokenProvider

    required = (
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
        "SPOTIFY_REFRESH_TOKEN",
        "SPOTIFY_PLAYLIST_ID",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print("missing environment: " + ", ".join(missing), file=sys.stderr)
        return 2

    client = Spotify(
        TokenProvider(
            client_id=os.environ["SPOTIFY_CLIENT_ID"],
            client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
            refresh_token=os.environ["SPOTIFY_REFRESH_TOKEN"],
        )
    )
    # Last.fm is optional: without a key the chain simply falls through
    # to positional, which is the same path a slow or broken Last.fm takes.
    lastfm_key = os.environ.get("LASTFM_API_KEY")
    lastfm = Lastfm(lastfm_key) if lastfm_key else None
    if lastfm is None:
        print("LASTFM_API_KEY not set; filling from track position only")

    report = execute(
        client, os.environ["SPOTIFY_PLAYLIST_ID"], Journal(), lastfm=lastfm
    )

    print(
        f"verdicts={report.verdicts} resolved={report.resolved} "
        f"unresolved={report.unresolved} added={report.added} "
        f"removed={report.removed} skipped={report.skipped}"
    )
    for error in report.errors:
        print(f"error: {error}", file=sys.stderr)

    # A failed write is worth a red run; an unresolved album is not, that
    # is what the bug queue is for.
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
