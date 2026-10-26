"""Stage 3 P2 regression: standards-aware ICS validation and zero-selection proof.

freefood-radar P2 tightens the SB Engaged calendar parser: RFC 5545 DURATION
and date-only DTEND semantics, fail-closed recurrence-set properties, a
frozen UID/sequence precedence rule, structured reject-stage counts, and a
zero-selection proof rule under which a zero selected count is only
verified_empty when every UID is demonstrably cancelled or out of window.
No network needed.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from radar.live import _count_change, refresh
from radar.scrapers.sbengaged import FeedError, parse_calendar

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
NOW_DST = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SCORER = lambda rows, root: {"test_model": True}
URL = "https://sbengaged.stonybrook.edu/rsvp?id=1"


def calendar(*events):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//tests//EN\r\n"
            + "".join(events) + "END:VCALENDAR\r\n").encode()


def vevent(uid="one", start="20261018T160000Z", end="20261018T180000Z",
           extra="", summary="Free pizza", url=URL, date_value=False):
    if date_value:
        start_line = f"DTSTART;VALUE=DATE:{start}"
        end_line = f"DTEND;VALUE=DATE:{end}" if end is not None else ""
    else:
        start_line = f"DTSTART:{start}"
        end_line = f"DTEND:{end}" if end is not None else ""
    lines = [f"BEGIN:VEVENT\r\nUID:{uid}\r\n{start_line}\r\n"]
    if end_line:
        lines.append(f"{end_line}\r\n")
    lines.append(f"SUMMARY:{summary}\r\nDESCRIPTION:Food provided\r\n"
                 f"LOCATION:SAC 302\r\nURL:{url}\r\n{extra}END:VEVENT\r\n")
    return "".join(lines)


def uid_hash(uid):
    return hashlib.sha256(uid.encode()).hexdigest()[:20]


# --- Date-only DTEND (exclusive end) and missing DTEND ---

def test_date_only_dtend_is_exclusive_end():
    rows, stats = parse_calendar(
        calendar(vevent(start="20261018", end="20261020", date_value=True)), NOW)
    assert len(rows) == 1 and rows[0]["all_day"] is True
    assert rows[0]["ends_at"] == "2026-10-20T00:00:00-04:00"
    assert stats["selected_events"] == 1


def test_date_only_event_ending_today_is_out_of_window():
    # DTSTART 20261016, exclusive DTEND 20261017: the event covered Oct 16
    # only, so at noon UTC on Oct 17 it has already ended.
    rows, stats = parse_calendar(
        calendar(vevent(start="20261016", end="20261017", date_value=True)), NOW)
    assert rows == [] and stats["out_of_window"] == 1


def test_date_only_dtend_equal_to_dtstart_is_invalid():
    rows, stats = parse_calendar(
        calendar(vevent(start="20261018", end="20261018", date_value=True)), NOW)
    assert rows == [] and stats["invalid_records"] == 1


def test_missing_dtend_all_day_defaults_to_one_day():
    rows, _ = parse_calendar(
        calendar(vevent(start="20261018", end=None, date_value=True)), NOW)
    assert len(rows) == 1 and rows[0]["ends_at"] == "2026-10-19T00:00:00-04:00"


def test_missing_dtend_timed_is_disclosed_unknown_not_invented():
    rows, stats = parse_calendar(
        calendar(vevent(end=None)), NOW)
    assert len(rows) == 1
    assert rows[0]["ends_at"] is None
    assert rows[0]["duration_unknown"] is True
    assert stats["unknown_duration_events"] == 1


# --- DURATION ---

def test_duration_valid_timed_and_date():
    rows, _ = parse_calendar(
        calendar(vevent(end=None, extra="DURATION:PT2H\r\n")), NOW)
    assert rows[0]["ends_at"] == "2026-10-18T18:00:00+00:00"
    assert rows[0]["duration_unknown"] is False
    rows, _ = parse_calendar(
        calendar(vevent(start="20261018", end=None, date_value=True,
                        extra="DURATION:P2D\r\n")), NOW)
    assert rows[0]["ends_at"] == "2026-10-20T00:00:00-04:00"


def test_duration_invalid_is_an_invalid_record_not_a_reinterpretation():
    rows, stats = parse_calendar(
        calendar(vevent(end=None, extra="DURATION:INVALID\r\n")), NOW)
    assert rows == [] and stats["invalid_records"] == 1


def test_dtend_and_duration_together_is_an_invalid_record():
    rows, stats = parse_calendar(
        calendar(vevent(extra="DURATION:PT2H\r\n")), NOW)
    assert rows == [] and stats["invalid_records"] == 1


def test_timed_dtend_before_dtstart_is_an_invalid_record():
    rows, stats = parse_calendar(
        calendar(vevent(start="20261018T180000Z", end="20261018T160000Z")), NOW)
    assert rows == [] and stats["invalid_records"] == 1


# --- DST boundary and floating time ---

def test_duration_across_fall_back_is_elapsed_time():
    rows, _ = parse_calendar(
        calendar(vevent(start="20261101T003000", end=None,
                        extra="DURATION:PT2H\r\n").replace(
                            "DTSTART:20261101T003000",
                            "DTSTART;TZID=America/New_York:20261101T003000")),
        NOW_DST)
    assert len(rows) == 1
    assert rows[0]["starts_at"].endswith("-04:00")
    # 00:30 EDT plus 2 elapsed hours is 01:30 EST, not 02:30.
    assert rows[0]["ends_at"] == "2026-11-01T01:30:00-05:00"


def test_floating_time_is_assumed_new_york_and_flagged():
    rows, _ = parse_calendar(
        calendar(vevent(start="20261018T120000", end="20261018T140000")), NOW)
    assert rows[0]["starts_at"] == "2026-10-18T12:00:00-04:00"
    assert rows[0]["floating_time_assumed"] is True


# --- Recurrence-set properties fail closed ---

@pytest.mark.parametrize("prop", [
    "RDATE:20261019T160000Z\r\n",
    "EXDATE:20261019T160000Z\r\n",
    "RRULE:FREQ=DAILY\r\n",
    "RECURRENCE-ID:20261018T160000Z\r\n",
])
def test_recurrence_set_property_fails_closed(prop):
    with pytest.raises(FeedError):
        parse_calendar(calendar(vevent(extra=prop)), NOW)


# --- UID + sequence precedence (frozen: SEQUENCE primary, LAST-MODIFIED
# secondary, first occurrence wins an exact tie) ---

def test_sequence_beats_last_modified_in_either_feed_order():
    active = vevent(extra="SEQUENCE:5\r\nLAST-MODIFIED:20261015T000000Z\r\n")
    cancelled = vevent(extra="SEQUENCE:2\r\nLAST-MODIFIED:20261016T000000Z\r\n"
                             "STATUS:CANCELLED\r\n")
    for feed in (calendar(active, cancelled), calendar(cancelled, active)):
        rows, stats = parse_calendar(feed, NOW)
        assert len(rows) == 1 and stats["cancelled"] == 0


def test_exact_tie_keeps_the_first_occurrence():
    active = vevent(extra="SEQUENCE:3\r\nLAST-MODIFIED:20261016T000000Z\r\n")
    cancelled = vevent(extra="SEQUENCE:3\r\nLAST-MODIFIED:20261016T000000Z\r\n"
                             "STATUS:CANCELLED\r\n")
    rows, _ = parse_calendar(calendar(active, cancelled), NOW)
    assert len(rows) == 1
    rows, stats = parse_calendar(calendar(cancelled, active), NOW)
    assert rows == [] and stats["cancelled"] == 1


def test_cancelled_newest_is_not_resurrected_by_older_active():
    old_active = vevent(extra="SEQUENCE:1\r\nLAST-MODIFIED:20261015T000000Z\r\n")
    new_cancelled = vevent(extra="SEQUENCE:2\r\nLAST-MODIFIED:20261016T000000Z\r\n"
                                 "STATUS:CANCELLED\r\n")
    for feed in (calendar(old_active, new_cancelled),
                 calendar(new_cancelled, old_active)):
        rows, stats = parse_calendar(feed, NOW)
        assert rows == [] and stats["cancelled"] == 1


def test_uid_change_does_not_resurrect_a_cancelled_listing():
    cancelled = vevent(uid="old-uid", extra="STATUS:CANCELLED\r\n")
    active = vevent(uid="new-uid")
    rows, stats = parse_calendar(calendar(cancelled, active), NOW)
    assert len(rows) == 1 and rows[0]["uid"] == "new-uid"
    assert rows[0]["id"] == uid_hash("new-uid")
    assert stats["cancelled"] == 1


# --- Reject-stage counts and invariant ---

def test_reject_stage_counts_satisfy_the_uid_invariant():
    feed = calendar(
        vevent(uid="selected"),
        vevent(uid="cancelled", extra="STATUS:CANCELLED\r\n"),
        vevent(uid="expired", start="20261016T160000Z", end="20261016T180000Z"),
        vevent(uid="bad-url", url="javascript:alert(1)"),
        vevent(uid="", summary="No uid"),
    )
    _, stats = parse_calendar(feed, NOW)
    assert stats["source_components"] == 5
    assert stats["unique_uids"] == 4
    assert stats["selected_events"] == 1
    assert stats["cancelled"] == 1
    assert stats["out_of_window"] == 1
    assert stats["invalid_records"] == 2
    assert stats["invalid_uid_records"] == 1
    assert stats["no_uid_records"] == 1
    assert stats["unsupported_recurrence"] == 0
    assert (stats["selected_events"] + stats["cancelled"]
            + stats["out_of_window"] + stats["invalid_uid_records"]
            == stats["unique_uids"])
    assert stats["invalid_records"] == (stats["invalid_uid_records"]
                                        + stats["no_uid_records"])


# --- Malformed-event percentage triggers a known error ---

def test_malformed_percentage_triggers_feed_error():
    bad = [vevent(uid=f"bad-{i}", url="javascript:alert(1)") for i in range(5)]
    good = [vevent(uid=f"good-{i}") for i in range(5)]
    with pytest.raises(FeedError):
        parse_calendar(calendar(*(bad + good)), NOW)


def test_below_threshold_malformed_events_do_not_fail_the_feed():
    rows, stats = parse_calendar(
        calendar(*([vevent(uid="bad", url="javascript:alert(1)")]
                   + [vevent(uid=f"good-{i}") for i in range(9)])), NOW)
    assert len(rows) == 9 and stats["invalid_records"] == 1


# --- Zero-selection proof ---

def mixed_zero_feed():
    return calendar(
        vevent(uid="cancelled", extra="STATUS:CANCELLED\r\n"),
        vevent(uid="expired", start="20261016T160000Z", end="20261016T180000Z"),
        vevent(uid="bad-url", url="javascript:alert(1)"),
    )


def test_mixed_zero_selection_is_not_verified_empty():
    _, stats = parse_calendar(mixed_zero_feed(), NOW)
    assert stats["selected_events"] == 0 and stats["invalid_records"] == 1
    state, suspect = _count_change(30, stats)
    assert state == "zero_unverified_suspect" and suspect is True


def test_all_cancelled_and_all_out_of_window_remain_verified_empty():
    _, cancelled_stats = parse_calendar(
        calendar(vevent(extra="STATUS:CANCELLED\r\n")), NOW)
    assert _count_change(30, cancelled_stats) == ("verified_empty", False)
    _, expired_stats = parse_calendar(
        calendar(vevent(start="20261016T160000Z", end="20261016T180000Z")), NOW)
    assert _count_change(30, expired_stats) == ("verified_empty", False)


def test_count_change_without_proof_fails_closed():
    state, suspect = _count_change(30, {"selected_events": 0,
                                       "source_components": 2})
    assert state == "zero_unverified_suspect" and suspect is True


def seed_snapshot(tmp_path):
    assert refresh(tmp_path, now=NOW,
                   fetcher=lambda: ((FIXTURES / "sbengaged_full.ics").read_bytes(), {}),
                   scorer=SCORER)
    return json.loads((tmp_path / "data/sbengaged-events.json").read_text())


def test_mixed_zero_feed_does_not_replace_snapshot_until_confirmed(tmp_path):
    good = seed_snapshot(tmp_path)
    assert len(good["events"]) == 30
    feed = lambda: (mixed_zero_feed(), {})
    assert refresh(tmp_path, now=NOW, fetcher=feed, scorer=SCORER) is False
    assert json.loads((tmp_path / "data/sbengaged-events.json").read_text()) == good
    status = json.loads((tmp_path / "data/sbengaged-status.json").read_text())
    assert status["count_change"] == "zero_unverified_suspect"
    assert refresh(tmp_path, now=NOW, fetcher=feed, scorer=SCORER) is True
    assert json.loads((tmp_path / "data/sbengaged-events.json").read_text())["events"] == []
    status = json.loads((tmp_path / "data/sbengaged-status.json").read_text())
    assert status["count_change"] == "zero_unverified_confirmed"
