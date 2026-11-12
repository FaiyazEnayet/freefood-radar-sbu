"""Versioned contracts for public snapshots and refresh health records."""
from __future__ import annotations

from datetime import datetime
import hashlib
import math
import re
from copy import deepcopy

SCHEMA_VERSION = 2
SNAPSHOT_CLASSIFICATION = "public_source_snapshot"
EVENT_SOURCE = "sbengaged"
STATUS_STATES = {"fresh", "stale", "failed"}
COUNT_STATES = {
    "initial", "empty_suspect", "empty_confirmed", "verified_empty",
    "zero_unverified_suspect", "zero_unverified_confirmed",
    "severe_shrink_suspect", "severe_shrink_confirmed", "shrank", "grew",
    "stable", "not_modified",
}
LOCATION_VISIBILITY = {"public", "not_listed", "withheld_by_source"}
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _date(value, label, errors, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        errors.append(f"{label} must be an ISO timestamp" if not nullable else
                      f"{label} must be an ISO timestamp or null")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{label} must be an ISO timestamp")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(f"{label} must include a timezone")
        return None
    return parsed


def _v2_record(value, label, errors):
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
    elif value.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{label}.schema_version must be {SCHEMA_VERSION}")


def _nonnegative_int(value, label, errors, *, nullable=False):
    if nullable and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(f"{label} must be a non-negative integer" if not nullable else
                      f"{label} must be a non-negative integer or null")


def _event(event, index, errors):
    label = f"events[{index}]"
    _v2_record(event, label, errors)
    if not isinstance(event, dict):
        return
    for key in ("id", "uid", "title", "description", "club", "location", "url", "source"):
        if not isinstance(event.get(key), str):
            errors.append(f"{label}.{key} must be a string")
    uid, event_id = event.get("uid"), event.get("id")
    if isinstance(uid, str) and uid and isinstance(event_id, str):
        if event_id != hashlib.sha256(uid.encode("utf8")).hexdigest()[:20]:
            errors.append(f"{label}.id is not the canonical UID identifier")
    elif not uid:
        errors.append(f"{label}.uid must not be empty")
    if event.get("source") != EVENT_SOURCE or str(event.get("id", "")).startswith("seed-"):
        errors.append(f"{label} has a non-public or demonstration source")
    if event.get("location_visibility") not in LOCATION_VISIBILITY:
        errors.append(f"{label}.location_visibility is not recognized")
    for key in ("all_day", "floating_time_assumed", "duration_unknown", "food_verified"):
        if not isinstance(event.get(key), bool):
            errors.append(f"{label}.{key} must be boolean")
    if event.get("food_verified") is True:
        errors.append(f"{label}.food_verified cannot be asserted by a classifier")
    for key in ("food_probability", "score", "offer_likelihood"):
        number = event.get(key)
        if number is not None and (isinstance(number, bool) or not isinstance(number, (int, float))
                                   or not math.isfinite(number)):
            errors.append(f"{label}.{key} must be finite or null")
        elif number is not None and not 0 <= number <= 1:
            errors.append(f"{label}.{key} must be between 0 and 1")
    for key in ("food_keywords", "evidence_categories", "access_caveats"):
        if key in event and (not isinstance(event[key], list) or
                             any(not isinstance(item, str) for item in event[key])):
            errors.append(f"{label}.{key} must be an array of strings")
    if "eligibility" in event and event["eligibility"] not in {"general", "restricted", "unspecified"}:
        errors.append(f"{label}.eligibility is not recognized")
    if "evidence_status" in event and event["evidence_status"] not in {
            "scored", "uncertain", "insufficient_evidence", "unscored"}:
        errors.append(f"{label}.evidence_status is not recognized")
    starts = _date(event.get("starts_at"), f"{label}.starts_at", errors)
    ends = _date(event.get("ends_at"), f"{label}.ends_at", errors, nullable=True)
    if starts is not None and ends is not None and ends < starts:
        errors.append(f"{label}.ends_at precedes starts_at")
    _date(event.get("source_updated_at"), f"{label}.source_updated_at", errors,
          nullable=True)


def validate_snapshot(snapshot):
    """Validate current snapshots and structurally migrate complete v1 records."""
    if isinstance(snapshot, dict) and snapshot.get("schema_version") == 1:
        legacy = deepcopy(snapshot)
        legacy["schema_version"] = SCHEMA_VERSION
        for key in ("diagnostics", "model", "lineage"):
            if isinstance(legacy.get(key), dict):
                legacy[key]["schema_version"] = SCHEMA_VERSION
        for event in legacy.get("events", []) if isinstance(legacy.get("events"), list) else []:
            if isinstance(event, dict):
                event["schema_version"] = SCHEMA_VERSION
        return _validate_snapshot_v2(legacy)
    return _validate_snapshot_v2(snapshot)


def _validate_snapshot_v2(snapshot):
    """Return contract errors for a v2 snapshot."""
    errors = []
    if not isinstance(snapshot, dict):
        return ["snapshot must be an object"]
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        return [f"snapshot.schema_version must be {SCHEMA_VERSION}"]
    if snapshot.get("classification") != SNAPSHOT_CLASSIFICATION:
        errors.append("snapshot.classification is not public_source_snapshot")
    _date(snapshot.get("fetched_at"), "snapshot.fetched_at", errors)
    source = snapshot.get("source")
    if not isinstance(source, dict):
        errors.append("snapshot.source must be an object")
    elif source.get("url") != "https://sbengaged.stonybrook.edu/ical/stonybrook/ical_stonybrook.ics":
        errors.append("snapshot.source.url is not the approved calendar")
    diagnostics = snapshot.get("diagnostics")
    _v2_record(diagnostics, "diagnostics", errors)
    if isinstance(diagnostics, dict):
        for key in ("source_components", "unique_uids", "cancelled", "invalid_records",
                    "invalid_uid_records", "no_uid_records", "unorderable_records",
                    "unsupported_recurrence", "unknown_duration_events", "out_of_window",
                    "selected_events"):
            _nonnegative_int(diagnostics.get(key), f"diagnostics.{key}", errors)
        for key in ("source_sha256",):
            if not isinstance(diagnostics.get(key), str) or not HEX_64.fullmatch(diagnostics[key]):
                errors.append(f"diagnostics.{key} must be a SHA-256 hex digest")
        start = _date(diagnostics.get("window_start"), "diagnostics.window_start", errors)
        end = _date(diagnostics.get("window_end"), "diagnostics.window_end", errors)
        if start is not None and end is not None and end <= start:
            errors.append("diagnostics.window_end must follow window_start")
        if diagnostics.get("count_change") not in COUNT_STATES:
            errors.append("diagnostics.count_change is not recognized")
        _nonnegative_int(diagnostics.get("previous_selected_events"),
                         "diagnostics.previous_selected_events", errors, nullable=True)
    model = snapshot.get("model")
    _v2_record(model, "model", errors)
    if isinstance(model, dict):
        config_hash = model.get("config_sha256")
        if config_hash is not None and (not isinstance(config_hash, str) or not HEX_64.fullmatch(config_hash)):
            errors.append("model.config_sha256 must be a SHA-256 hex digest or null")
        for key in ("training_rows", "labeled_rows", "seed"):
            if key in model:
                _nonnegative_int(model[key], f"model.{key}", errors)
    lineage = snapshot.get("lineage")
    _v2_record(lineage, "lineage", errors)
    if isinstance(lineage, dict):
        for key in ("source_sha256", "model_sha256"):
            value = lineage.get(key)
            if not isinstance(value, str) or not HEX_64.fullmatch(value):
                errors.append(f"lineage.{key} must be a SHA-256 hex digest")
        if isinstance(diagnostics, dict) and lineage.get("source_sha256") != diagnostics.get("source_sha256"):
            errors.append("lineage.source_sha256 must match diagnostics.source_sha256")
        if isinstance(model, dict) and model.get("config_sha256") is not None and lineage.get("model_sha256") != model.get("config_sha256"):
            errors.append("lineage.model_sha256 must match model.config_sha256")
    events = snapshot.get("events")
    if not isinstance(events, list):
        errors.append("snapshot.events must be an array")
    else:
        ids = set()
        for index, event in enumerate(events):
            _event(event, index, errors)
            if isinstance(event, dict) and isinstance(event.get("id"), str):
                if event["id"] in ids:
                    errors.append(f"events[{index}] has a duplicate canonical ID")
                ids.add(event["id"])
        if isinstance(diagnostics, dict) and diagnostics.get("selected_events") != len(events):
            errors.append("diagnostics.selected_events must equal the event-array length")
    return errors


def validate_status(status, *, previous_last_success=None):
    """Validate current health records and complete pre-v2 refresh records."""
    if isinstance(status, dict) and status.get("schema_version") != SCHEMA_VERSION:
        legacy = deepcopy(status)
        if legacy.get("schema_version") not in (None, 1):
            return [f"status.schema_version must be {SCHEMA_VERSION}"]
        legacy["schema_version"] = SCHEMA_VERSION
        if isinstance(legacy.get("lineage"), dict):
            legacy["lineage"]["schema_version"] = SCHEMA_VERSION
        status = legacy
    return _validate_status_v2(status, previous_last_success=previous_last_success)


def _validate_status_v2(status, *, previous_last_success=None):
    """Return contract errors for a refresh health record."""
    errors = []
    _v2_record(status, "status", errors)
    if not isinstance(status, dict):
        return errors
    attempt = _date(status.get("last_attempt_at"), "status.last_attempt_at", errors)
    success_at = _date(status.get("last_successful_fetch_at"),
                       "status.last_successful_fetch_at", errors, nullable=True)
    if status.get("state") not in STATUS_STATES:
        errors.append("status.state is not recognized")
    if not isinstance(status.get("success"), bool):
        errors.append("status.success must be boolean")
    elif status.get("success") != (status.get("state") == "fresh"):
        errors.append("status.success must match the fresh state")
    for key in ("not_modified", "conditional_request"):
        if not isinstance(status.get(key), bool):
            errors.append(f"status.{key} must be boolean")
    if status.get("error") is not None and not isinstance(status.get("error"), str):
        errors.append("status.error must be a string or null")
    if status.get("count_change") is not None and status.get("count_change") not in COUNT_STATES:
        errors.append("status.count_change is not recognized")
    for key in ("selected_events", "previous_selected_events", "attempts"):
        _nonnegative_int(status.get(key), f"status.{key}", errors, nullable=True)
    if status.get("pending_count_change") is not None:
        pending = status["pending_count_change"]
        if not isinstance(pending, dict) or pending.get("state") not in {
                "empty_suspect", "severe_shrink_suspect", "zero_unverified_suspect"}:
            errors.append("status.pending_count_change is not a recognized suspect state")
    if attempt is not None and success_at is not None and success_at > attempt:
        errors.append("status last-good time must not follow the refresh attempt")
    if previous_last_success is not None and success_at is not None:
        previous = _date(previous_last_success, "previous last-good time", errors)
        if previous is not None and success_at < previous:
            errors.append("status last-good time must be monotonic")
    if status.get("lineage") is not None:
        _v2_record(status["lineage"], "status.lineage", errors)
        if isinstance(status["lineage"], dict):
            lineage_hash = status["lineage"].get("model_sha256")
            if status.get("model_sha256") is not None and lineage_hash != status.get("model_sha256"):
                errors.append("status.model_sha256 must match status.lineage.model_sha256")
    return errors
