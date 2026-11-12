"""P11: versioned JSON contracts shared by refresh and dashboard consumers."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from radar.live import refresh
from radar.schema import validate_snapshot, validate_status

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
SCORER = lambda rows, root: {"recipe": "fixture scorer"}


def fixture(name):
    return (FIXTURES / name).read_bytes()


def test_stage3_freefood_radar_11_shared_json_fixture_matches_both_consumers():
    example = json.loads((FIXTURES / "schema-v2-consumer.json").read_text())
    assert example["fixture_type"] == "synthetic_consumer_contract_example"
    assert validate_snapshot(example["snapshot"]) == []
    assert validate_status(example["status"]) == []
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portfolio_ui"))
    from renderer import validate_publication_snapshot
    assert validate_publication_snapshot(example["snapshot"]) == []


def published(tmp_path, name="sbengaged_full.ics"):
    assert refresh(tmp_path, now=NOW,
                   fetcher=lambda: (fixture(name), {}), scorer=SCORER)
    snapshot = json.loads((tmp_path / "data/sbengaged-events.json").read_text())
    status = json.loads((tmp_path / "data/sbengaged-status.json").read_text())
    return snapshot, status


def test_stage3_freefood_radar_11_refresh_records_round_trip_with_versions(tmp_path):
    snapshot, status = published(tmp_path)
    assert snapshot["schema_version"] == status["schema_version"] == 2
    assert snapshot["diagnostics"]["schema_version"] == 2
    assert snapshot["model"]["schema_version"] == 2
    assert all(event["schema_version"] == 2 for event in snapshot["events"])
    assert validate_snapshot(json.loads(json.dumps(snapshot))) == []
    assert validate_status(json.loads(json.dumps(status))) == []


@pytest.mark.parametrize("previous_feed,feed,repeats,expected", [
    (None, "sbengaged_full.ics", 1, "initial"),
    ("sbengaged_full.ics", "sbengaged_empty.ics", 1, "empty_suspect"),
    ("sbengaged_full.ics", "sbengaged_empty.ics", 2, "empty_confirmed"),
    ("sbengaged_full.ics", "sbengaged_verified_empty.ics", 1, "verified_empty"),
    ("sbengaged_full.ics", "sbengaged_shrunk.ics", 1, "severe_shrink_suspect"),
    ("sbengaged_full.ics", "sbengaged_shrunk.ics", 2, "severe_shrink_confirmed"),
    ("sbengaged_full.ics", "sbengaged_moderate.ics", 1, "shrank"),
    ("sbengaged_moderate.ics", "sbengaged_full.ics", 1, "grew"),
    ("sbengaged_full.ics", "sbengaged_full.ics", 1, "stable"),
])
def test_stage3_freefood_radar_11_fr1_states_round_trip_as_consumer_fixtures(
        tmp_path, previous_feed, feed, repeats, expected):
    if previous_feed:
        published(tmp_path, previous_feed)
    status = None
    for _ in range(repeats):
        raw = fixture(feed)
        succeeded = refresh(tmp_path, now=NOW,
                            fetcher=lambda: (raw, {}), scorer=SCORER)
        status = json.loads((tmp_path / "data/sbengaged-status.json").read_text())
        if expected.endswith("suspect"):
            assert not succeeded
    snapshot = json.loads((tmp_path / "data/sbengaged-events.json").read_text())
    assert status["count_change"] == expected
    assert validate_snapshot(json.loads(json.dumps(snapshot))) == []
    assert validate_status(json.loads(json.dumps(status))) == []


def test_stage3_freefood_radar_11_not_modified_status_round_trips(tmp_path):
    published(tmp_path)
    assert refresh(tmp_path, now=NOW,
                   fetcher=lambda previous=None: (None, {"not_modified": True}),
                   scorer=SCORER)
    status = json.loads((tmp_path / "data/sbengaged-status.json").read_text())
    assert status["count_change"] == "not_modified"
    assert validate_status(json.loads(json.dumps(status))) == []


def test_stage3_freefood_radar_11_unverified_zero_states_round_trip(tmp_path, monkeypatch):
    import radar.live as live
    published(tmp_path)
    diagnostics = {
        "source_components": 1, "unique_uids": 1, "cancelled": 0,
        "invalid_records": 1, "invalid_uid_records": 1, "no_uid_records": 0,
        "unorderable_records": 0, "unsupported_recurrence": 0,
        "unknown_duration_events": 0, "out_of_window": 0, "selected_events": 0,
        "source_sha256": "a" * 64, "window_start": NOW.isoformat(),
        "window_end": "2026-10-31T12:00:00+00:00",
    }
    monkeypatch.setattr(live, "parse_calendar", lambda *args: ([], deepcopy(diagnostics)))
    raw = b"synthetic ambiguous zero; test only"
    assert not refresh(tmp_path, now=NOW, fetcher=lambda: (raw, {}), scorer=SCORER)
    first = json.loads((tmp_path / "data/sbengaged-status.json").read_text())
    assert first["count_change"] == "zero_unverified_suspect"
    assert validate_status(first) == []
    assert refresh(tmp_path, now=NOW, fetcher=lambda: (raw, {}), scorer=SCORER)
    snapshot = json.loads((tmp_path / "data/sbengaged-events.json").read_text())
    second = json.loads((tmp_path / "data/sbengaged-status.json").read_text())
    assert second["count_change"] == snapshot["diagnostics"]["count_change"] == "zero_unverified_confirmed"
    assert validate_snapshot(snapshot) == []
    assert validate_status(second) == []


def test_stage3_freefood_radar_11_renderer_rejects_malformed_versioned_snapshot(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portfolio_ui"))
    from renderer import render_html
    snapshot = {"schema_version": 2, "classification": "public_source_snapshot",
                "events": [{"schema_version": 2, "id": "seed-001", "source": "seed"}]}
    (tmp_path / "data").mkdir()
    (tmp_path / "data/sbengaged-events.json").write_text(json.dumps(snapshot))
    source = Path(__file__).resolve().parents[1] / "portfolio_ui/original-dashboard-snapshot.html"
    with pytest.raises(ValueError, match="Refusing to render"):
        render_html(tmp_path, "freefood-radar", source.read_text(encoding="utf8"))


def test_stage3_freefood_radar_11_renderer_rejects_malformed_versioned_status(tmp_path):
    example = json.loads((FIXTURES / "schema-v2-consumer.json").read_text())
    status = deepcopy(example["status"])
    status["state"] = "unknown"
    (tmp_path / "data").mkdir()
    (tmp_path / "data/sbengaged-status.json").write_text(json.dumps(status))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portfolio_ui"))
    from renderer import _production_status
    with pytest.raises(ValueError, match="Refusing to render"):
        _production_status(tmp_path)


def test_stage3_freefood_radar_11_workflow_checks_both_records():
    workflow = (Path(__file__).resolve().parents[1] /
                ".github/workflows/sbengaged.yml").read_text(encoding="utf8")
    assert "validate_snapshot(data)" in workflow
    assert "validate_status(json.loads" in workflow


def test_stage3_freefood_radar_11_malformed_records_fail_closed(tmp_path):
    snapshot, status = published(tmp_path)
    broken = deepcopy(snapshot)
    broken["events"][0]["food_probability"] = float("nan")
    assert any("finite" in error for error in validate_snapshot(broken))

    broken = deepcopy(snapshot)
    broken["events"].append(deepcopy(broken["events"][0]))
    assert any("duplicate" in error for error in validate_snapshot(broken))

    broken = deepcopy(snapshot)
    broken["diagnostics"]["selected_events"] += 1
    assert any("selected_events" in error for error in validate_snapshot(broken))

    broken = deepcopy(snapshot)
    broken["events"][0]["food_verified"] = True
    assert any("verified" in error for error in validate_snapshot(broken))

    broken_status = deepcopy(status)
    broken_status["state"] = "unknown"
    assert any("state" in error for error in validate_status(broken_status))


def test_stage3_freefood_radar_11_status_last_good_time_cannot_move_back(tmp_path):
    snapshot, status = published(tmp_path)
    earlier = deepcopy(status)
    earlier["last_attempt_at"] = "2026-10-16T12:00:00+00:00"
    earlier["last_successful_fetch_at"] = "2026-10-16T12:00:00+00:00"
    assert any("monotonic" in error for error in validate_status(
        earlier, previous_last_success=snapshot["fetched_at"]))


@pytest.mark.parametrize("has_previous", [False, True])
def test_stage3_freefood_radar_11_failed_and_stale_statuses_round_trip(tmp_path, has_previous):
    if has_previous:
        published(tmp_path)

    def fail():
        raise RuntimeError("fixture failure")

    assert not refresh(tmp_path, now=NOW, fetcher=fail, scorer=SCORER)
    status = json.loads((tmp_path / "data/sbengaged-status.json").read_text())
    assert status["state"] == ("stale" if has_previous else "failed")
    assert validate_status(json.loads(json.dumps(status))) == []
