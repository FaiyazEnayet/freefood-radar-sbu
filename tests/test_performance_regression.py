"""P14: operational runtime/performance profiling and incremental computation.

The refresh refits the classifier on every run and serializes a large
self-contained document, but no stage was ever measured. These tests pin:

- a cached fitted model scores identically to a fresh refit;
- the cache misses when the labeled data or the model config changes;
- a corrupt cache artifact or manifest is never used (refit, then repair);
- the benchmark report measures real stages from real runs only, with no
  speedup claim;
- production HTML carries no dormant demo payload (no seeded file
  reference, no seeded records), while the explicit demo mode is untouched.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from radar.live import (  # noqa: E402
    MODEL_CONFIG,
    model_config_hash,
    model_cache_manifest,
    score_snapshot,
)
import benchmark_pipeline  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SNAPSHOT = json.loads((REPO / "data/sbengaged-events.json").read_text())
SMALL_EVENTS = deepcopy(SNAPSHOT["events"][:25])
TOLERANCE = 1e-12
SCORE_FIELDS = ("food_probability", "score", "offer_likelihood")
LIST_FIELDS = ("food_keywords", "evidence_categories", "access_caveats")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_root(tmp_path, events=None):
    """A throwaway repo root with real labeled data and a snapshot copy."""
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    shutil.copy(REPO / "data/labeled.csv", root / "data/labeled.csv")
    snap = deepcopy(SNAPSHOT)
    snap["events"] = deepcopy(events) if events is not None else deepcopy(SNAPSHOT["events"])
    (root / "data/sbengaged-events.json").write_text(json.dumps(snap), encoding="utf8")
    shutil.copy(REPO / "data/sbengaged-status.json", root / "data/sbengaged-status.json")
    return root


def score_fields(rows):
    """The comparable per-event score fields, in event order."""
    out = []
    for row in rows:
        out.append({k: row[k] for k in SCORE_FIELDS}
                   | {k: list(row[k]) for k in LIST_FIELDS}
                   | {"eligibility": row["eligibility"],
                      "evidence_status": row["evidence_status"]})
    return out


def assert_same_scores(left, right):
    assert len(left) == len(right)
    for a, b in zip(score_fields(left), score_fields(right)):
        for field in SCORE_FIELDS:
            assert abs(a[field] - b[field]) <= TOLERANCE, field
        for field in LIST_FIELDS + ("eligibility", "evidence_status"):
            assert a[field] == b[field], field


class FitCounter:
    """Counts build_pipeline calls to prove a cache hit skips the refit."""

    def __init__(self, monkeypatch):
        import radar.classifier as classifier_mod
        self.calls = 0
        real = classifier_mod.build_pipeline

        def counting():
            self.calls += 1
            return real()

        monkeypatch.setattr(classifier_mod, "build_pipeline", counting)


def test_stage3_freefood_radar_14_cached_model_scores_match_fresh_refit(tmp_path):
    root = make_root(tmp_path, SMALL_EVENTS)
    fresh_rows = deepcopy(SMALL_EVENTS)
    fresh = score_snapshot(fresh_rows, root, use_cache=False)
    assert fresh["config_sha256"] == model_config_hash()
    cached_rows = deepcopy(SMALL_EVENTS)
    cached = score_snapshot(cached_rows, root, use_cache=True)
    assert cached["config_sha256"] == fresh["config_sha256"]
    assert cached["training_rows"] == fresh["training_rows"]
    assert cached["labeled_sha256"] == fresh["labeled_sha256"]
    first_rows = deepcopy(SMALL_EVENTS)
    score_snapshot(first_rows, root, use_cache=False)
    second_rows = deepcopy(SMALL_EVENTS)
    score_snapshot(second_rows, root, use_cache=True)
    assert_same_scores(first_rows, second_rows)


def test_stage3_freefood_radar_14_cache_hit_skips_refit(tmp_path, monkeypatch):
    root = make_root(tmp_path, SMALL_EVENTS)
    counter = FitCounter(monkeypatch)
    score_snapshot(deepcopy(SMALL_EVENTS), root, use_cache=True)
    assert counter.calls == 1
    score_snapshot(deepcopy(SMALL_EVENTS), root, use_cache=True)
    assert counter.calls == 1


def test_stage3_freefood_radar_14_cache_miss_on_labeled_data_change(tmp_path, monkeypatch):
    root = make_root(tmp_path, SMALL_EVENTS)
    counter = FitCounter(monkeypatch)
    score_snapshot(deepcopy(SMALL_EVENTS), root, use_cache=True)
    assert counter.calls == 1
    old_sha = sha256_file(root / "data/labeled.csv")
    labeled = (root / "data/labeled.csv").read_text(encoding="utf8")
    labeled = labeled.replace("Free boba for the first 50 attendees",
                              "Free boba for the first 25 attendees", 1)
    (root / "data/labeled.csv").write_text(labeled, encoding="utf8")
    assert sha256_file(root / "data/labeled.csv") != old_sha
    score_snapshot(deepcopy(SMALL_EVENTS), root, use_cache=True)
    assert counter.calls == 2
    manifest = model_cache_manifest(root)
    assert manifest["labeled_sha256"] == sha256_file(root / "data/labeled.csv")


def test_stage3_freefood_radar_14_cache_miss_on_config_change(tmp_path, monkeypatch):
    root = make_root(tmp_path, SMALL_EVENTS)
    counter = FitCounter(monkeypatch)
    score_snapshot(deepcopy(SMALL_EVENTS), root, use_cache=True)
    assert counter.calls == 1
    changed = deepcopy(MODEL_CONFIG)
    changed["logistic_regression"]["C"] = 1.0
    assert model_config_hash(changed) != model_config_hash()
    score_snapshot(deepcopy(SMALL_EVENTS), root, use_cache=True, config=changed)
    assert counter.calls == 2
    manifest = model_cache_manifest(root, config=changed)
    assert manifest["config_sha256"] == model_config_hash(changed)


def test_stage3_freefood_radar_14_no_cache_use_after_corrupt_artifact(tmp_path, monkeypatch):
    root = make_root(tmp_path, SMALL_EVENTS)
    counter = FitCounter(monkeypatch)
    score_snapshot(deepcopy(SMALL_EVENTS), root, use_cache=True)
    assert counter.calls == 1
    cache_dir = root / ".cache" / "freefood-radar" / "model"
    artifact = cache_dir / "model.joblib"
    assert artifact.exists()
    blob = bytearray(artifact.read_bytes())
    blob[len(blob) // 2] ^= 0xFF
    artifact.write_bytes(bytes(blob))
    fresh_rows = deepcopy(SMALL_EVENTS)
    score_snapshot(fresh_rows, root, use_cache=False)
    repaired_rows = deepcopy(SMALL_EVENTS)
    score_snapshot(repaired_rows, root, use_cache=True)
    assert counter.calls == 3
    assert_same_scores(fresh_rows, repaired_rows)
    # The corrupt artifact was replaced by a valid one: the next run is a hit.
    score_snapshot(deepcopy(SMALL_EVENTS), root, use_cache=True)
    assert counter.calls == 3


def test_stage3_freefood_radar_14_no_cache_use_after_corrupt_manifest(tmp_path, monkeypatch):
    root = make_root(tmp_path, SMALL_EVENTS)
    counter = FitCounter(monkeypatch)
    score_snapshot(deepcopy(SMALL_EVENTS), root, use_cache=True)
    assert counter.calls == 1
    cache_dir = root / ".cache" / "freefood-radar" / "model"
    (cache_dir / "manifest.json").write_text("{not json", encoding="utf8")
    fresh_rows = deepcopy(SMALL_EVENTS)
    score_snapshot(fresh_rows, root, use_cache=False)
    repaired_rows = deepcopy(SMALL_EVENTS)
    score_snapshot(repaired_rows, root, use_cache=True)
    assert counter.calls == 3
    assert_same_scores(fresh_rows, repaired_rows)
    manifest = model_cache_manifest(root)
    assert manifest["config_sha256"] == model_config_hash()


def test_stage3_freefood_radar_14_benchmark_report_measures_real_stages(tmp_path):
    root = make_root(tmp_path)
    snapshot_path = root / "data/sbengaged-events.json"
    report = benchmark_pipeline.profile_pipeline(
        snapshot_path, root,
        ics_path=FIXTURES / "sbengaged_full.ics",
        fetcher=lambda: ((FIXTURES / "sbengaged_full.ics").read_bytes(), {}),
    )
    assert report["inputs"]["event_count"] == len(SNAPSHOT["events"])
    assert report["inputs"]["snapshot_sha256"] == sha256_file(snapshot_path)
    assert report["inputs"]["schema_version"] == SNAPSHOT["schema_version"]
    stages = {stage["name"]: stage for stage in report["stages"]}
    for name in ("load_snapshot", "parse", "classifier_training", "scoring",
                 "serialization", "rendering", "fetch"):
        assert name in stages, name
        assert stages[name]["status"] == "measured", name
        assert stages[name]["wall_seconds"] >= 0
        assert stages[name]["peak_rss_mb"] > 0
    assert report["artifacts"]["snapshot_bytes"] == stages["serialization"]["serialized_bytes"]
    assert report["artifacts"]["snapshot_bytes"] == len(
        json.dumps(SNAPSHOT, ensure_ascii=False, indent=2).encode("utf8"))
    assert report["artifacts"]["rendered_html_bytes"] > 0
    assert report["claims"]["measured_only"] is True
    assert report["claims"]["speedup_claim"] is None


def test_stage3_freefood_radar_14_benchmark_fetch_not_measured_without_fetcher(tmp_path):
    root = make_root(tmp_path)
    report = benchmark_pipeline.profile_pipeline(
        root / "data/sbengaged-events.json", root)
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert stages["fetch"]["status"] == "not_measured"
    assert "reason" in stages["fetch"]
    assert stages["parse"]["status"] == "not_measured"


def test_stage3_freefood_radar_14_production_html_has_no_demo_payload(tmp_path):
    root = make_root(tmp_path)
    sys.path.insert(0, str(REPO / "portfolio_ui"))
    from renderer import render_html
    raw = (REPO / "portfolio_ui/original-dashboard-snapshot.html").read_text(encoding="utf8")
    html, payload = render_html(root, "freefood-radar", raw, mode="production")
    assert "data/seed_events.json" not in html
    assert "seed-" not in html
    assert payload["design"]["sources"] == [
        source for source in payload["design"]["sources"]
        if source != "data/seed_events.json"]
    assert "data/seed_events.json" not in payload["provenance"]["files"]
    assert "north" not in payload["design"]
    assert "layout" not in payload["design"]
    titles = [figure.get("layout", {}).get("title", {}).get("text", "")
              for figure in payload["figures"] if not figure.get("redacted")]
    assert any(title.startswith("Classifier report") for title in titles)
    assert any(title.startswith("Calibration") for title in titles)
    assert any(figure.get("redacted") for figure in payload["figures"])
    assert payload["sbengaged"]["events"]
    assert payload["production_mode"] is True


def test_stage3_freefood_radar_14_demo_mode_still_renders_demo_content():
    sys.path.insert(0, str(REPO / "portfolio_ui"))
    from renderer import render_html
    raw = (REPO / "portfolio_ui/original-dashboard-snapshot.html").read_text(encoding="utf8")
    html, payload = render_html(REPO, "freefood-radar", raw, mode="demo")
    assert "seeded event records" in html
    assert "data/seed_events.json" in payload["design"]["sources"]


def test_stage3_freefood_radar_14_refresh_uses_cached_model(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from radar.live import refresh
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    shutil.copy(REPO / "data/labeled.csv", root / "data/labeled.csv")
    counter = FitCounter(monkeypatch)
    fetcher = lambda: ((FIXTURES / "sbengaged_full.ics").read_bytes(), {})
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    assert refresh(root, now=now, fetcher=fetcher) is True
    assert counter.calls == 1
    assert refresh(root, now=now, fetcher=fetcher) is True
    assert counter.calls == 1
    snapshot = json.loads((root / "data/sbengaged-events.json").read_text())
    assert snapshot["events"]
    assert all("food_probability" in event for event in snapshot["events"])
