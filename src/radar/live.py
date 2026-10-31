"""Refresh a public-source snapshot; never replace a failed fetch with demo data."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import os

from .scrapers.sbengaged import FEED_URL, LISTING_URL, fetch_calendar, parse_calendar

# A count drop is "severe" only against a snapshot with enough events for
# the ratio to mean something; below that, small absolute changes dominate.
SHRINK_MIN_PREVIOUS = 20
SHRINK_RATIO = 0.5

# The model recipe refit on every refresh, frozen as data so its hash is
# immutable: changing any value here changes the hash, and the hash recorded
# in a snapshot always names the exact recipe that scored that snapshot.
# Values mirror classifier.build_pipeline and the training split below.
MODEL_CONFIG = {
    "recipe": "TF-IDF + logistic regression + sigmoid calibration",
    "tfidf": {"ngram_range": [1, 2], "min_df": 2, "max_df": 0.9,
              "sublinear_tf": True, "stop_words": "english"},
    "logistic_regression": {"max_iter": 2000, "C": 2.0, "class_weight": "balanced"},
    "calibration": {"method": "sigmoid", "cv": 3},
    "training_split": {"test_size": 0.25, "seed": 7, "stratify": True},
}
SCHEMA_VERSION = 1


def _canonical_hash(value) -> str:
    """SHA-256 of a canonical JSON encoding; the same value always hashes alike."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf8")
    ).hexdigest()


def model_config_hash(config=MODEL_CONFIG) -> str:
    """Immutable hash of the model configuration that scored a snapshot."""
    return _canonical_hash(config)


def _model_hash(model: dict) -> str:
    """Hash naming the model behind a snapshot.

    A scorer that reports its own config hash (score_snapshot does) is
    quoted directly; any other scorer dict is hashed canonically so the
    lineage still names exactly what ran.
    """
    if isinstance(model, dict) and model.get("config_sha256"):
        return model["config_sha256"]
    return _canonical_hash(model)


def _lineage(source_sha256, source_etag, source_last_modified, model_sha256) -> dict:
    """One explicit source, model and schema lineage record."""
    return dict(schema_version=SCHEMA_VERSION, source_url=FEED_URL,
                source_sha256=source_sha256, source_etag=source_etag,
                source_last_modified=source_last_modified, model_sha256=model_sha256)


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
    split = MODEL_CONFIG["training_split"]
    texts, _, labels, _ = train_test_split(labeled.text.tolist(), labeled.label.tolist(),
                                           test_size=split["test_size"], random_state=split["seed"],
                                           stratify=labeled.label.tolist())
    model = build_pipeline()
    model.fit(texts, labels)
    scored = score_events([Event.from_dict(e) for e in events], model)
    for row, prediction in zip(events, scored):
        row.update(food_probability=prediction.food_probability, score=prediction.score,
                   food_keywords=prediction.food_keywords)
    return dict(recipe="Repository TF-IDF + logistic regression + sigmoid calibration, cv=3",
                seed=split["seed"], training_rows=len(texts), labeled_rows=len(labeled),
                labeled_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), sklearn_version=sklearn.__version__,
                config=MODEL_CONFIG, config_sha256=model_config_hash(),
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


def _previous_source_http(previous) -> dict:
    """ETag / Last-Modified recorded on the last good snapshot, if any."""
    source = (previous or {}).get("source") or {}
    http = {}
    if source.get("etag"):
        http["etag"] = source["etag"]
    if source.get("last_modified"):
        http["last_modified"] = source["last_modified"]
    return http


def _previous_model_hash(previous):
    lineage = (previous or {}).get("lineage") or {}
    if lineage.get("model_sha256"):
        return lineage["model_sha256"]
    model = (previous or {}).get("model") or {}
    if model.get("config_sha256"):
        return model["config_sha256"]
    return None


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


def _call_fetcher(fetcher, previous_http) -> tuple[bool, object]:
    """Call a fetcher, passing the previous fetch record when it accepts one.

    Returns (accepted_previous, result). The default fetcher
    (fetch_calendar) takes `previous` as a keyword; test doubles may take
    one positional argument or none. A fetcher that accepts neither is
    called with no arguments, preserving the old call contract.
    """
    try:
        params = inspect.signature(fetcher).parameters
    except (TypeError, ValueError):
        return False, fetcher()
    if "previous" in params:
        return True, fetcher(previous=previous_http)
    positional = [p for p in params.values()
                  if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    if positional or any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params.values()):
        return True, fetcher(previous_http)
    return False, fetcher()


def refresh(root: Path, days: int = 14, now=None, fetcher=fetch_calendar, scorer=score_snapshot) -> bool:
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    snapshot_path = root / "data/sbengaged-events.json"
    status_path = root / "data/sbengaged-status.json"
    previous = json.loads(snapshot_path.read_text(encoding="utf8")) if snapshot_path.exists() else None
    previous_status = json.loads(status_path.read_text(encoding="utf8")) if status_path.exists() else {}
    previous_count = _previous_selected(previous)
    previous_http = _previous_source_http(previous)
    previous_lineage = (previous or {}).get("lineage") or {}
    # Status etag / last_modified always describe the snapshot on disk
    # (the last good one), never a suspect or failed fetch.
    status = dict(last_attempt_at=now.isoformat(), success=False,
                  last_successful_fetch_at=previous.get("fetched_at") if previous else None,
                  count_change=None, previous_selected_events=previous_count,
                  selected_events=None,
                  pending_count_change=previous_status.get("pending_count_change"),
                  state="failed" if previous is None else "stale",
                  not_modified=False, conditional_request=False,
                  etag=previous_http.get("etag"), last_modified=previous_http.get("last_modified"),
                  attempts=None, lineage=previous_lineage,
                  model_sha256=_previous_model_hash(previous))
    try:
        accepted_previous, fetched = _call_fetcher(fetcher, previous_http)
        raw, http = fetched
        http = http or {}
        if "conditional_request" in http:
            status["conditional_request"] = bool(http.get("conditional_request"))
        else:
            status["conditional_request"] = bool(previous_http) and accepted_previous
        status["attempts"] = http.get("attempts")
        if raw is None or http.get("not_modified"):
            # 304: the source is unchanged, so the snapshot file is not
            # rewritten at all; only health moves forward. A pending suspect
            # is cleared because the source now matches the last good fetch.
            if previous is None:
                status.update(success=False, state="failed", not_modified=True,
                              error="Not-modified response with no previous snapshot to preserve")
                atomic_json(status_path, status)
                return False
            status.update(success=True, state="fresh", not_modified=True,
                          last_successful_fetch_at=now.isoformat(),
                          selected_events=previous_count, count_change="not_modified",
                          pending_count_change=None, error=None,
                          etag=http.get("etag") or previous_http.get("etag"),
                          last_modified=http.get("last_modified") or previous_http.get("last_modified"),
                          lineage=previous_lineage or _lineage(
                              (previous.get("diagnostics") or {}).get("source_sha256"),
                              http.get("etag") or previous_http.get("etag"),
                              http.get("last_modified") or previous_http.get("last_modified"),
                              _previous_model_hash(previous)),
                          model_sha256=_previous_model_hash(previous))
            atomic_json(status_path, status)
            return True
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
                    state="stale" if previous is not None else "failed",
                    error=(f"Suspected source problem: {state} ({previous_count} -> "
                           f"{diagnostics['selected_events']} selected events); keeping the "
                           "last good snapshot until a repeat observation confirms it"))
                atomic_json(status_path, status)
                return False
        model = scorer(events, root)
        model_hash = _model_hash(model)
        diagnostics = {**diagnostics, "count_change": state,
                       "previous_selected_events": previous_count}
        lineage = _lineage(diagnostics["source_sha256"], http.get("etag"),
                           http.get("last_modified"), model_hash)
        payload = dict(schema_version=SCHEMA_VERSION, classification="public_source_snapshot",
                       fetched_at=now.isoformat(),
                       source={"name": "SB Engaged public calendar", "url": FEED_URL, "listing_url": LISTING_URL, **http},
                       coverage="Public calendar only; private events and withheld locations are unavailable",
                       model=model, diagnostics=diagnostics, events=events, lineage=lineage)
        atomic_json(snapshot_path, payload)
        status.update(success=True, state="fresh", last_successful_fetch_at=now.isoformat(), error=None,
                      count_change=state, pending_count_change=None,
                      etag=http.get("etag"), last_modified=http.get("last_modified"),
                      lineage=lineage, model_sha256=model_hash)
    except Exception as exc:
        # Deliberately keep the last good source data, with explicit failed status.
        status["error"] = f"{type(exc).__name__}: {exc}"
        status["state"] = "stale" if previous is not None else "failed"
    atomic_json(status_path, status)
    return status["success"]
