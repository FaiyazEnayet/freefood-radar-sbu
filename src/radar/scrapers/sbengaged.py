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

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar

FEED_URL = "https://sbengaged.stonybrook.edu/ical/stonybrook/ical_stonybrook.ics"
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
    """Select not-ended occurrences in [now, now + days], newest UID wins.

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
    for item in components:
        # Recurrence-set properties are not expanded by this parser. Any of
        # them means the occurrence set is unknown, so the feed fails closed
        # instead of silently dropping or inventing occurrences.
        if (item.get("RRULE") or item.get("RECURRENCE-ID")
                or item.get("RDATE") or item.get("EXDATE")):
            raise FeedError("Recurring-event schema changed; occurrence expansion needs review")
        uid = str(item.get("UID", "")).strip()
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
        # Frozen precedence (P2): SEQUENCE is the revision counter and wins
        # over LAST-MODIFIED; an exact tie keeps the first occurrence, so a
        # later exact duplicate (a retransmission, not a revision) cannot
        # override it.
        key = (sequence, modified)
        if uid not in newest or key > newest[uid][0]:
            newest[uid] = (key, item)
    events = []
    cancelled = 0
    out_of_window = 0
    invalid_uid = 0
    unknown_duration = 0
    until = now + timedelta(days=days)
    for uid, (_, item) in newest.items():
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
        source_components=len(components), unique_uids=len(newest), cancelled=cancelled,
        invalid_records=invalid_records, invalid_uid_records=invalid_uid,
        no_uid_records=no_uid, unorderable_records=unorderable,
        unsupported_recurrence=0, unknown_duration_events=unknown_duration,
        out_of_window=out_of_window, selected_events=len(events),
        source_sha256=sha256(raw).hexdigest(),
        window_start=now.isoformat(), window_end=until.isoformat(),
    )


def fetch_calendar(session=None) -> tuple[bytes, dict]:
    """One robots read and one bounded public-feed read with timeouts."""
    session = session or requests.Session()
    headers = {"User-Agent": UA}
    robots = session.get("https://sbengaged.stonybrook.edu/robots.txt", headers=headers, timeout=(10, 30))
    if robots.status_code != 200:
        raise FeedError("Could not verify public-source robots policy")
    policy = RobotFileParser()
    policy.parse(robots.text.splitlines())
    if not policy.can_fetch(UA, FEED_URL):
        raise FeedError("Calendar fetching is disallowed by the public robots policy")
    with session.get(FEED_URL, headers=headers, timeout=(10, 45), stream=True, allow_redirects=True) as response:
        if response.status_code != 200:
            raise FeedError(f"Calendar returned HTTP {response.status_code}")
        chain = list(response.history) + [response]
        if any(urlparse(r.url).scheme != "https" or urlparse(r.url).hostname not in ALLOWED_HOSTS for r in chain):
            raise FeedError("Unexpected feed redirect; inspect publisher configuration")
        if "text/calendar" not in response.headers.get("Content-Type", "").lower():
            raise FeedError("Source returned a non-calendar page, possibly a login screen")
        raw = bytearray()
        for chunk in response.iter_content(65536):
            raw.extend(chunk)
            if len(raw) > MAX_BYTES:
                raise FeedError("Calendar exceeds configured size limit")
        return bytes(raw), dict(url=FEED_URL, final_url=response.url, etag=response.headers.get("ETag"),
                                last_modified=response.headers.get("Last-Modified"))
