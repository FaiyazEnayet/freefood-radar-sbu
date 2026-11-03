"""Public SB Engaged calendar ingestion. No login, cookies or private API.

The feed URL is advertised by https://sbengaged.stonybrook.edu/events.
Locations withheld by the publisher remain withheld. Calendar occurrences
are deduplicated by UID; cancellation and timezone information are retained.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo
import time as _time

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar

FEED_URL = "https://sbengaged.stonybrook.edu/ical/stonybrook/ical_stonybrook.ics"
ROBOTS_URL = "https://sbengaged.stonybrook.edu/robots.txt"
LISTING_URL = "https://sbengaged.stonybrook.edu/events"
NY = ZoneInfo("America/New_York")
UA = "freefood-radar-sbu/0.2 (+https://github.com/FaiyazEnayet/freefood-radar-sbu)"
MAX_BYTES = 20 * 1024 * 1024
ALLOWED_HOSTS = {"sbengaged.stonybrook.edu", "static-prod-us-east-1.campusgroups.com"}


class FeedError(RuntimeError):
    pass


def _public_url(value: str) -> str:
    parsed = urlparse(value)
    return value if parsed.scheme == "https" and parsed.hostname == "sbengaged.stonybrook.edu" else ""


def _plain(value) -> str:
    return BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)


def _date(value) -> tuple[datetime, bool, bool]:
    if isinstance(value, datetime):
        floating = value.tzinfo is None
        return value.replace(tzinfo=NY) if floating else value, False, floating
    if isinstance(value, date):
        return datetime.combine(value, time.min, NY), True, False
    raise ValueError("missing or invalid DTSTART")


def parse_calendar(raw: bytes, now: datetime, days: int = 14) -> tuple[list[dict], dict]:
    """Select not-ended occurrences in [now, now + days], newest revision wins.

    An unsupported recurrence is a source-contract failure, not an excuse to
    silently drop occurrences. The currently published feed expands them.
    """
    if now.tzinfo is None or not 1 <= days <= 90:
        raise ValueError("timezone-aware now and days in 1..90 required")
    if len(raw) > MAX_BYTES or b"BEGIN:VCALENDAR" not in raw[:256]:
        raise FeedError("Response is not a supported public calendar")
    try:
        cal = Calendar.from_ical(raw)
    except Exception as exc:
        raise FeedError("Invalid calendar data") from exc
    if str(cal.name) != "VCALENDAR":
        raise FeedError("Calendar envelope missing")
    components = cal.walk("VEVENT")
    if len(components) > 50000:
        raise FeedError("Unexpected event volume; inspect source contract")
    newest = {}
    no_uid = 0
    unorderable = 0
    recurrence = None
    for item in components:
        uid = str(item.get("UID", "")).strip()
        # Identity is the UID, plus the RECURRENCE-ID for a recurrence
        # instance (RFC 5546 section 2.1.5, rule 1): an override instance
        # is a distinct identity from its master record and is never merged
        # with it during deduplication.
        recurrence_id = ""
        rid_prop = item.get("RECURRENCE-ID")
        if rid_prop is not None:
            try:
                rid_value = item.decoded("RECURRENCE-ID")
            except Exception:
                rid_value = None
            recurrence_id = (rid_value.isoformat()
                             if isinstance(rid_value, (datetime, date))
                             else str(rid_prop))
        identity = (uid, recurrence_id)
        # Recurrence-set properties are not expanded by this parser. The
        # first offending identity is remembered and the feed fails closed
        # once deduplication has run, instead of silently dropping or
        # inventing occurrences.
        if recurrence is None:
            for prop in ("RRULE", "RDATE", "EXDATE", "RECURRENCE-ID"):
                if item.get(prop):
                    recurrence = (identity, prop)
                    break
        if not uid:
            no_uid += 1
            continue
        try:
            sequence = int(item.get("SEQUENCE", 0))
        except (ValueError, TypeError):
            # A revision that cannot be ordered cannot win or lose against
            # another variant; it is dropped and counted, never guessed.
            unorderable += 1
            continue
        modified = item.decoded("LAST-MODIFIED", datetime.min.replace(tzinfo=timezone.utc))
        if not isinstance(modified, datetime):
            modified = datetime.min.replace(tzinfo=timezone.utc)
        modified = modified.replace(tzinfo=timezone.utc) if modified.tzinfo is None else modified
        # Revision precedence follows iTIP message sequencing (RFC 5546
        # section 2.1.5, rule 2): within one identity, the highest SEQUENCE
        # obsoletes every lower revision, because SEQUENCE is the revision
        # counter (RFC 5545 section 3.8.7.4), incremented on every
        # significant change, cancellations included (RFC 5546 section
        # 2.1.4). LAST-MODIFIED is only a secondary key here; an exact tie
        # keeps the first occurrence, so a later exact duplicate (a
        # retransmission, not a revision) cannot override it.
        key = (sequence, modified)
        if identity not in newest or key > newest[identity][0]:
            newest[identity] = (key, item)
    if recurrence is not None:
        (ruid, rrid), prop = recurrence
        where = f"UID {ruid}" if ruid else "a component without a UID"
        if rrid:
            where += f", RECURRENCE-ID {rrid}"
        raise FeedError("Recurring-event schema changed; occurrence expansion "
                        f"needs review ({prop} on {where})")
    events = []
    cancelled = 0
    out_of_window = 0
    invalid_uid = 0
    unknown_duration = 0
    until = now + timedelta(days=days)
    for (uid, _instance), (_, item) in newest.items():
        if str(item.get("STATUS", "")).upper() == "CANCELLED":
            cancelled += 1
            continue
        try:
            start, all_day, floating = _date(item.decoded("DTSTART"))
            has_dtend = item.get("DTEND") is not None
            has_duration = item.get("DURATION") is not None
            if has_dtend and has_duration:
                raise ValueError("DTEND and DURATION must not both be present")
            if has_dtend:
                end_value = item.decoded("DTEND", None)
                end = _date(end_value)[0]
                if all_day and isinstance(end_value, date) and not isinstance(end_value, datetime):
                    # A date DTEND is an exclusive end: it must be strictly
                    # later than the DTSTART date, not equal to it.
                    if end <= start:
                        raise ValueError("date DTEND is not later than DTSTART")
                duration_unknown = False
            elif has_duration:
                duration = item.decoded("DURATION", None)
                if not isinstance(duration, timedelta):
                    raise ValueError("unsupported DURATION")
                # DURATION is elapsed time: add it in UTC so a DST transition
                # does not stretch or shrink it in wall-clock terms.
                end = (start.astimezone(timezone.utc) + duration).astimezone(start.tzinfo)
                duration_unknown = False
            elif all_day:
                # RFC 5545 default for a date DTSTART with no DTEND/DURATION.
                end = start + timedelta(days=1)
                duration_unknown = False
            else:
                # Timed event with no end information: no duration is
                # invented. The event is selected only on its start, and the
                # unknown duration is disclosed on the event record.
                end = None
                duration_unknown = True
            if end is not None and end < start:
                raise ValueError("negative duration")
            if start >= until or (end or start) <= now:
                out_of_window += 1
                continue
        except (ValueError, TypeError, KeyError):
            invalid_uid += 1
            continue
        title = _plain(item.get("SUMMARY"))
        url = _public_url(str(item.get("URL", "")))
        if not title or not url:
            invalid_uid += 1
            continue
        if duration_unknown:
            unknown_duration += 1
        location = _plain(item.get("LOCATION"))
        withheld = "sign in" in location.lower() or "log in" in location.lower()
        organizer = item.get("ORGANIZER")
        club = _plain(organizer.params.get("CN", "")) if organizer is not None else ""
        updated = item.decoded("LAST-MODIFIED", None)
        events.append(dict(
            id=sha256(uid.encode()).hexdigest()[:20], uid=uid, title=title,
            description=_plain(item.get("DESCRIPTION")), club=club,
            location="" if withheld else location,
            location_visibility="withheld_by_source" if withheld else ("public" if location else "not_listed"),
            starts_at=start.isoformat(), ends_at=end.isoformat() if end else None,
            all_day=all_day, timezone="America/New_York", floating_time_assumed=floating,
            duration_unknown=duration_unknown,
            url=url, source="sbengaged", source_updated_at=updated.isoformat() if isinstance(updated, datetime) else None,
            food_probability=None, score=None, food_keywords=[], food_verified=False,
        ))
    # Reject-stage accounting (P2). Every newest UID lands in exactly one
    # stage, so selected + cancelled + out_of_window + invalid_uid is
    # always equal to unique_uids. invalid_records is the total of all
    # unusable components, including ones without a UID and ones whose
    # revision could not be ordered. unsupported_recurrence is 0 on any
    # successful parse: a recurrence-set property is a contract error
    # raised above, never a silently counted stage.
    invalid_records = invalid_uid + no_uid + unorderable
    if invalid_records and invalid_records > max(3, len(components) * .05):
        raise FeedError(f"Too many invalid events ({invalid_records}); keeping the last good snapshot")
    return sorted(events, key=lambda x: (datetime.fromisoformat(x["starts_at"]).timestamp(), x["id"])), dict(
        source_components=len(components), unique_uids=len({u for u, _ in newest}), cancelled=cancelled,
        invalid_records=invalid_records, invalid_uid_records=invalid_uid,
        no_uid_records=no_uid, unorderable_records=unorderable,
        unsupported_recurrence=0, unknown_duration_events=unknown_duration,
        out_of_window=out_of_window, selected_events=len(events),
        source_sha256=sha256(raw).hexdigest(),
        window_start=now.isoformat(), window_end=until.isoformat(),
    )


# HTTP statuses worth one more attempt. Anything else fails immediately:
# a 404 or a contract failure (redirect, wrong content type, oversize body)
# will not be fixed by asking again.
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
DEFAULT_MAX_ATTEMPTS = 3


def conditional_headers(previous) -> dict:
    """Conditional request headers for a previous fetch, if it recorded any.

    `previous` is the source record of the last good snapshot (its etag and
    last_modified fields). An empty or missing record means a plain fetch.
    """
    headers = {}
    if isinstance(previous, dict):
        if previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        if previous.get("last_modified"):
            headers["If-Modified-Since"] = previous["last_modified"]
    return headers


def _public_hop(response) -> bool:
    """One HTTP hop stays inside the public boundary: HTTPS, an approved
    host, and no embedded credentials in the URL."""
    parsed = urlparse(response.url)
    return (parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS
            and not parsed.username and not parsed.password)


def _enforce_landing_policy(session, headers, origin_policy, response) -> None:
    """Robots policy for the URL the redirect chain actually lands on.

    A same-host redirect is rechecked against the origin policy, since the
    announced feed path being allowed says nothing about the landing path.
    A landing on the other approved host is checked against that host's own
    robots file when it publishes one: a 200 is parsed and enforced, a 5xx
    or a transport failure means the policy could not be verified and the
    fetch fails closed, and any other status (the static host answers 403
    for its missing robots key) means no policy is published, which under
    the robots access rules carries no restrictions to enforce.
    """
    final_url = response.url
    if urlparse(final_url).hostname == urlparse(FEED_URL).hostname:
        if final_url != FEED_URL and not origin_policy.can_fetch(UA, final_url):
            raise FeedError("Calendar fetching is disallowed by the public robots policy")
        return
    robots_url = f"https://{urlparse(final_url).hostname}/robots.txt"
    try:
        landed = session.get(robots_url, headers=headers, timeout=(10, 30))
    except requests.exceptions.RequestException as exc:
        raise FeedError("Could not verify redirect-host robots policy") from exc
    if any(not _public_hop(r) for r in list(landed.history) + [landed]):
        raise FeedError("Could not verify redirect-host robots policy")
    if landed.status_code >= 500:
        raise FeedError("Could not verify redirect-host robots policy")
    if landed.status_code == 200:
        landed_policy = RobotFileParser()
        landed_policy.parse(landed.text.splitlines())
        if not landed_policy.can_fetch(UA, final_url):
            raise FeedError("Calendar fetching is disallowed by the redirect host's robots policy")


def fetch_calendar(session=None, previous=None, max_attempts=DEFAULT_MAX_ATTEMPTS,
                   backoff_seconds=0.5) -> tuple[bytes | None, dict]:
    """One robots read and one bounded, conditional public-feed read.

    When `previous` carries an ETag or Last-Modified value, those are sent
    back as If-None-Match / If-Modified-Since. A 304 response returns
    (None, info) with not_modified True: the caller keeps its last good
    snapshot untouched. Transient failures (429 and 5xx, or a transport
    error) are retried up to max_attempts times with exponential backoff;
    every other failure raises immediately. Retries refetch the whole feed,
    so the parser still deduplicates by UID and no partial result is kept.

    The fetch boundary is fail closed. Every hop of a redirect chain must
    stay on an approved HTTPS host without embedded credentials, the origin
    robots policy must be retrievable and permit the feed, a redirect loop
    is a contract failure rather than a transient error, and the robots
    policy of the host the chain lands on is enforced as described in
    _enforce_landing_policy. Cookies set by any response during the fetch
    are dropped before returning: only public metadata is recorded.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    session = session or requests.Session()
    try:
        return _fetch_calendar(session, previous, max_attempts, backoff_seconds)
    finally:
        jar = getattr(session, "cookies", None)
        if jar is not None:
            jar.clear()


def _fetch_calendar(session, previous, max_attempts, backoff_seconds
                    ) -> tuple[bytes | None, dict]:
    headers = {"User-Agent": UA}
    conditional = conditional_headers(previous)
    headers.update(conditional)
    try:
        robots = session.get(ROBOTS_URL, headers=headers, timeout=(10, 30))
    except requests.exceptions.RequestException as exc:
        # A slow or failed robots read means the public policy could not be
        # verified; that fails closed like any other contract failure.
        raise FeedError("Could not verify public-source robots policy") from exc
    if robots.status_code != 200:
        raise FeedError("Could not verify public-source robots policy")
    if any(not _public_hop(r) for r in list(robots.history) + [robots]):
        raise FeedError("Could not verify public-source robots policy")
    policy = RobotFileParser()
    policy.parse(robots.text.splitlines())
    if not policy.can_fetch(UA, FEED_URL):
        raise FeedError("Calendar fetching is disallowed by the public robots policy")
    for attempt in range(1, max_attempts + 1):
        try:
            with session.get(FEED_URL, headers=headers, timeout=(10, 45), stream=True,
                             allow_redirects=True) as response:
                chain = list(response.history) + [response]
                if any(not _public_hop(r) for r in chain):
                    raise FeedError("Unexpected feed redirect; inspect publisher configuration")
                if response.status_code == 304:
                    return None, dict(url=FEED_URL, final_url=response.url,
                                      etag=response.headers.get("ETag") or previous_etag(previous),
                                      last_modified=response.headers.get("Last-Modified")
                                      or previous_last_modified(previous),
                                      not_modified=True, conditional_request=bool(conditional),
                                      attempts=attempt)
                if response.status_code != 200:
                    if response.status_code in TRANSIENT_STATUSES and attempt < max_attempts:
                        _sleep_backoff(attempt, backoff_seconds)
                        continue
                    raise FeedError(f"Calendar returned HTTP {response.status_code}")
                _enforce_landing_policy(session, headers, policy, response)
                if "text/calendar" not in response.headers.get("Content-Type", "").lower():
                    raise FeedError("Source returned a non-calendar page, possibly a login screen")
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise FeedError("Calendar exceeds configured size limit")
                return bytes(raw), dict(url=FEED_URL, final_url=response.url,
                                        etag=response.headers.get("ETag"),
                                        last_modified=response.headers.get("Last-Modified"),
                                        not_modified=False, conditional_request=bool(conditional),
                                        attempts=attempt)
        except requests.exceptions.TooManyRedirects as exc:
            # A redirect loop is a publisher contract failure; retrying the
            # same chain cannot fix it.
            raise FeedError("Calendar redirect loop; inspect publisher configuration") from exc
        except requests.exceptions.RequestException as exc:
            if attempt < max_attempts:
                _sleep_backoff(attempt, backoff_seconds)
                continue
            raise FeedError(f"Calendar fetch failed: {exc}") from exc
    raise FeedError("Calendar fetch failed: retry limit reached")


def previous_etag(previous):
    return previous.get("etag") if isinstance(previous, dict) else None


def previous_last_modified(previous):
    return previous.get("last_modified") if isinstance(previous, dict) else None


def _sleep_backoff(attempt, backoff_seconds):
    if backoff_seconds and backoff_seconds > 0:
        _time.sleep(backoff_seconds * 2 ** (attempt - 1))
