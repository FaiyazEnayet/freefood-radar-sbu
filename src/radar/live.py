"""Refresh a public-source snapshot; never replace a failed fetch with demo data."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import os

from .scrapers.sbengaged import FEED_URL, LISTING_URL, fetch_calendar, parse_calendar

# A count drop is "severe" only against a snapshot with enough events for
# the ratio to mean something; below that, small absolute changes dominate.
SHRINK_MIN_PREVIOUS = 20
SHRINK_RATIO = 0.5


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    os.replace(temp, path)


def score_snapshot(events: list[dict], root: Path) -> dict:
    """Refit the existing model recipe on its original seed-7 training split.

    No old model artifact is shipped in the repo. Do not overwrite historical
    metrics.json or claim its held-out accuracy measures these live texts.
    """
    import sklearn
    from sklearn.model_selection import train_test_split
    from .classifier import build_pipeline, load_labeled
    from .models import Event
    from .digest import score_events
    path = root / "data/labeled.csv"
    labeled = load_labeled(str(path))
    texts, _, labels, _ = train_test_split(labeled.text.tolist(), labeled.label.tolist(),
                                         test_size=.25, random_state=7, stratify=labeled.label.tolist())
    model = build_pipeline()
    model.fit(texts, labels)
    scored = score_events([Event.from_dict(e) for e in events], model)
    for row, prediction in zip(events, scored):
        row.update(food_probability=prediction.food_probability, score=prediction.score,
                   food_keywords=prediction.food_keywords)
    return dict(recipe="Repository TF-IDF + logistic regression + sigmoid calibration, cv=3",
                seed=7, training_rows=len(texts), labeled_rows=len(labeled),
                labeled_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), sklearn_version=sklearn.__version__,
                validation="Historical labeled-text evaluation is not validated live-feed precision/recall")


def _previous_selected(previous) -> int | None:
    """Selected-event count of the last good snapshot, if one exists."""
    if not previous:
        return None
    diagnostics = previous.get("diagnostics") or {}
    if isinstance(diagnostics.get("selected_events"), int):
        return diagnostics["selected_events"]
    events = previous.get("events")
    return len(events) if isinstance(events, list) else None


def _count_change(previous_count, diagnostics) -> tuple[str, bool]:
    """Classify a parsed feed against the last good snapshot.

    Returns (state, suspect). A suspect state (empty_suspect,
    severe_shrink_suspect, zero_unverified_suspect) must not replace the
    snapshot until a second, identical observation confirms it.
    """
    new = diagnostics["selected_events"]
    if previous_count is None:
        return "initial", False
    if new == 0:
        # Zero selected events is a verified zero ONLY when every newest
        # UID is demonstrably cancelled or out of window and no record was
        # rejected. A zero that includes an invalid or ambiguous component
        # could be hiding an upcoming event, so it is never verified_empty:
        # it is zero_unverified_suspect until a second identical observation
        # confirms it, and even then it is recorded as confirmed-unverified,
        # not as a verified zero. A feed with no components at all breaks
        # the contract the published feed has always satisfied, so it is
        # only a suspicion. Diagnostics missing the proof fields fail closed
        # to the unverified state.
        if diagnostics["source_components"] == 0:
            return "empty_suspect", True
        invalid = diagnostics.get("invalid_records")
        unique = diagnostics.get("unique_uids")
        cancelled = diagnostics.get("cancelled")
        out_of_window = diagnostics.get("out_of_window")
        if (invalid == 0 and unique is not None and unique > 0
                and cancelled is not None and out_of_window is not None
                and cancelled + out_of_window == unique):
            return "verified_empty", False
        return "zero_unverified_suspect", True
    if previous_count >= SHRINK_MIN_PREVIOUS and new < previous_count * SHRINK_RATIO:
        return "severe_shrink_suspect", True
    if new < previous_count:
        return "shrank", False
    if new > previous_count:
        return "grew", False
    return "stable", False


def refresh(root: Path, days: int = 14, now=None, fetcher=fetch_calendar, scorer=score_snapshot) -> bool:
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    snapshot_path = root / "data/sbengaged-events.json"
    status_path = root / "data/sbengaged-status.json"
    previous = json.loads(snapshot_path.read_text(encoding="utf8")) if snapshot_path.exists() else None
    previous_status = json.loads(status_path.read_text(encoding="utf8")) if status_path.exists() else {}
    previous_count = _previous_selected(previous)
    status = dict(last_attempt_at=now.isoformat(), success=False,
                  last_successful_fetch_at=previous.get("fetched_at") if previous else None,
                  count_change=None, previous_selected_events=previous_count,
                  selected_events=None,
                  pending_count_change=previous_status.get("pending_count_change"))
    try:
        raw, http = fetcher()
        events, diagnostics = parse_calendar(raw, now, days)
        status["selected_events"] = diagnostics["selected_events"]
        state, suspect = _count_change(previous_count, diagnostics)
        if suspect:
            pending = dict(state=state, selected_events=diagnostics["selected_events"],
                           source_sha256=diagnostics["source_sha256"])
            if status["pending_count_change"] == pending:
                # A second, identical observation confirms the change is
                # real rather than a one-off source glitch.
                state = state.replace("_suspect", "_confirmed")
                status["pending_count_change"] = None
            else:
                status.update(
                    count_change=state, pending_count_change=pending,
                    error=(f"Suspected source problem: {state} ({previous_count} -> "
                           f"{diagnostics['selected_events']} selected events); keeping the "
                           "last good snapshot until a repeat observation confirms it"))
                atomic_json(status_path, status)
                return False
        model = scorer(events, root)
        diagnostics = {**diagnostics, "count_change": state,
                       "previous_selected_events": previous_count}
        payload = dict(schema_version=1, classification="public_source_snapshot", fetched_at=now.isoformat(),
                       source={"name": "SB Engaged public calendar", "url": FEED_URL, "listing_url": LISTING_URL, **http},
                       coverage="Public calendar only; private events and withheld locations are unavailable",
                       model=model, diagnostics=diagnostics, events=events)
        atomic_json(snapshot_path, payload)
        status.update(success=True, last_successful_fetch_at=now.isoformat(), error=None,
                      count_change=state, pending_count_change=None)
    except Exception as exc:
        # Deliberately keep the last good source data, with explicit failed status.
        status["error"] = f"{type(exc).__name__}: {exc}"
    atomic_json(status_path, status)
    return status["success"]
