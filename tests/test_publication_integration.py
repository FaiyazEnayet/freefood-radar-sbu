"""Stage 3 P13: production failure-injection and CI integration suite.

freefood-radar P13 covers the break between refresh, renderer, and
workflow, which isolated-function tests do not reach. These are
temporary-directory, no-network full pipeline tests: every case runs
scripts/refresh_sbengaged.py run_refresh end to end (fetch, parse,
score, snapshot, status, render) and asserts data provenance in both
the JSON snapshot and the rendered dashboard, plus the standalone
artifact validator that CI calls before any generated artifact is
committed. Fixture ICS is used only inside the test run, never as
published data.
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "portfolio_ui"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from radar.scrapers.sbengaged import FeedError
from refresh_sbengaged import run_refresh

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SNAPSHOT_HTML = REPO_ROOT / "portfolio_ui/original-dashboard-snapshot.html"
VALIDATOR = REPO_ROOT / "scripts" / "validate_publication.py"
MODEL_HASH = "ab" * 32


def make_root(tmp_path):
    root = tmp_path / "root"
    for sub in ("data", "models", "docs", "portfolio_ui"):
        (root / sub).mkdir(parents=True)
    shutil.copy(REPO_ROOT / "models/metrics.json", root / "models/metrics.json")
    shutil.copy(REPO_ROOT / "data/seed_events.json", root / "data/seed_events.json")
    shutil.copy(SNAPSHOT_HTML, root / "portfolio_ui" / "original-dashboard-snapshot.html")
    return root


def fixture(name):
    return (FIXTURES / name).read_bytes()


def stub_scorer(rows, root):
    return {"recipe": "fixture scorer", "config_sha256": MODEL_HASH}


def good_fetch():
    return fixture("sbengaged_full.ics"), {}


def fail_503():
    raise FeedError("HTTP 503")


def status_of(root):
    return json.loads((root / "data/sbengaged-status.json").read_text())


def snapshot_of(root):
    return json.loads((root / "data/sbengaged-events.json").read_text())


def evidence_from_html(html):
    m = re.search(r"const EVIDENCE=(.*?);\n", html, re.S)
    assert m, "EVIDENCE payload not found in rendered HTML"
    return json.loads(m.group(1).replace("<\\/", "</"))


def rendered(root):
    html = (root / "docs/dashboard.html").read_text(encoding="utf8")
    assert "seed-" not in html, "seeded marker leaked into public output"
    return html, evidence_from_html(html)


def assert_json_and_render_agree(root, success=True, state="fresh"):
    """Both public artifacts name the same snapshot and the same events."""
    snapshot = snapshot_of(root)
    html, d = rendered(root)
    assert d.get("production_mode") is True
    assert d.get("sbengaged", {}).get("fetched_at") == snapshot["fetched_at"]
    assert [e["id"] for e in d["sbengaged"]["events"]] == [e["id"] for e in snapshot["events"]]
    assert all(e["source"] == "sbengaged" for e in d["sbengaged"]["events"])
    assert d["sbengaged_status"]["success"] is success
    assert d["sbengaged_status"]["state"] == state
    assert d["provenance"]["files"]["data/sbengaged-events.json"] == hashlib.sha256(
        (root / "data/sbengaged-events.json").read_bytes()).hexdigest()
    return snapshot, d


def run_validator(root, cwd):
    """Run the standalone validator command from an unrelated cwd."""
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(root)],
        cwd=str(cwd), capture_output=True, text=True, timeout=60)
    return proc


def good_root(tmp_path):
    root = make_root(tmp_path)
    assert run_refresh(root, 14, fetcher=good_fetch, scorer=stub_scorer, now=NOW) == 0
    assert len(snapshot_of(root)["events"]) == 30
    return root


def test_stage3_freefood_radar_13_missing_feed_zero_prior_is_status_only(tmp_path):
    """Injected case 1/5: missing feed with zero prior snapshot. Exit 1,
    typed failed state, a status-only dashboard, and the validator accepts
    that legitimate failure state."""
    root = make_root(tmp_path)
    assert run_refresh(root, 14, fetcher=fail_503, scorer=stub_scorer, now=NOW) == 1
    assert not (root / "data/sbengaged-events.json").exists()
    status = status_of(root)
    assert status["success"] is False and status["state"] == "failed"
    assert "503" in status["error"]
    assert status["last_successful_fetch_at"] is None
    html, d = rendered(root)
    assert d.get("production_mode") is True
    assert d.get("no_snapshot") is True
    assert "sbengaged" not in d
    assert d.get("metrics") == [] and d.get("figures") == []
    assert d["sbengaged_status"]["success"] is False
    proc = run_validator(root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_stage3_freefood_radar_13_missing_feed_existing_snapshot_is_stale(tmp_path):
    """Injected case 5/5 (stale snapshot): missing feed with a retained
    snapshot. Snapshot byte-identical, typed stale state, dashboard still
    renders the retained snapshot with matching provenance, validator OK."""
    root = good_root(tmp_path)
    before = (root / "data/sbengaged-events.json").read_bytes()
    assert run_refresh(root, 14, fetcher=fail_503, scorer=stub_scorer, now=NOW) == 1
    assert (root / "data/sbengaged-events.json").read_bytes() == before
    status = status_of(root)
    assert status["success"] is False and status["state"] == "stale"
    assert "503" in status["error"]
    assert status["previous_selected_events"] == 30
    snapshot, d = assert_json_and_render_agree(root, success=False, state="stale")
    assert len(snapshot["events"]) == 30
    proc = run_validator(root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_stage3_freefood_radar_13_empty_feed_suspect_then_confirmed(tmp_path):
    """Injected case 2/5: empty feed. First sight is empty_suspect and the
    snapshot is kept; a repeated identical observation confirms it, the
    empty snapshot publishes, and JSON and rendered provenance agree."""
    root = good_root(tmp_path)
    before = (root / "data/sbengaged-events.json").read_bytes()
    empty = lambda: (fixture("sbengaged_empty.ics"), {})
    assert run_refresh(root, 14, fetcher=empty, scorer=stub_scorer, now=NOW) == 1
    assert (root / "data/sbengaged-events.json").read_bytes() == before
    first = status_of(root)
    assert first["count_change"] == "empty_suspect"
    assert first["pending_count_change"]["state"] == "empty_suspect"
    assert first["state"] == "stale"
    assert_json_and_render_agree(root, success=False, state="stale")
    proc = run_validator(root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert run_refresh(root, 14, fetcher=empty, scorer=stub_scorer, now=NOW) == 0
    snapshot = snapshot_of(root)
    assert snapshot["events"] == []
    assert snapshot["diagnostics"]["count_change"] == "empty_confirmed"
    assert status_of(root)["count_change"] == "empty_confirmed"
    html, d = rendered(root)
    assert d["sbengaged"]["events"] == []
    assert d["sbengaged"]["fetched_at"] == snapshot["fetched_at"]
    proc = run_validator(root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_d3_first_run_empty_feed_is_status_only_then_confirmed(tmp_path):
    """D3: an empty feed on the first run publishes nothing until confirmed.

    First sight is empty_suspect with a failed status and a status-only
    dashboard; a repeated identical observation confirms it, the empty
    snapshot publishes, and the validator accepts both states."""
    root = make_root(tmp_path)
    empty = lambda: (fixture("sbengaged_empty.ics"), {})
    assert run_refresh(root, 14, fetcher=empty, scorer=stub_scorer, now=NOW) == 1
    assert not (root / "data/sbengaged-events.json").exists()
    first = status_of(root)
    assert first["count_change"] == "empty_suspect"
    assert first["pending_count_change"]["state"] == "empty_suspect"
    assert first["state"] == "failed"
    html, d = rendered(root)
    assert d.get("no_snapshot") is True
    assert "sbengaged" not in d
    assert d["sbengaged_status"]["success"] is False
    proc = run_validator(root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert run_refresh(root, 14, fetcher=empty, scorer=stub_scorer, now=NOW) == 0
    snapshot = snapshot_of(root)
    assert snapshot["events"] == []
    assert snapshot["diagnostics"]["count_change"] == "empty_confirmed"
    assert status_of(root)["count_change"] == "empty_confirmed"
    html, d = rendered(root)
    assert d["sbengaged"]["events"] == []
    assert d["sbengaged"]["fetched_at"] == snapshot["fetched_at"]
    proc = run_validator(root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_d3_first_run_verified_empty_feed_publishes(tmp_path):
    """D3: a proven empty feed on the first run publishes at once.

    Every event is demonstrably cancelled or out of window, so the empty
    snapshot is a verified result, and JSON and rendered provenance agree."""
    root = make_root(tmp_path)
    verified = lambda: (fixture("sbengaged_verified_empty.ics"), {})
    assert run_refresh(root, 14, fetcher=verified, scorer=stub_scorer, now=NOW) == 0
    snapshot = snapshot_of(root)
    assert snapshot["events"] == []
    assert snapshot["diagnostics"]["count_change"] == "verified_empty"
    assert status_of(root)["count_change"] == "verified_empty"
    html, d = rendered(root)
    assert d["sbengaged"]["events"] == []
    assert d["sbengaged"]["fetched_at"] == snapshot["fetched_at"]
    proc = run_validator(root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_stage3_freefood_radar_13_malformed_ics_keeps_snapshot(tmp_path):
    """Injected case 3/5: malformed ICS. The fetch fails closed, the prior
    snapshot is kept byte-identical, the typed state names the failure,
    and the rendered dashboard still matches the retained snapshot."""
    root = good_root(tmp_path)
    before = (root / "data/sbengaged-events.json").read_bytes()
    garbage = lambda: (b"this is not a calendar feed", {})
    assert run_refresh(root, 14, fetcher=garbage, scorer=stub_scorer, now=NOW) == 1
    assert (root / "data/sbengaged-events.json").read_bytes() == before
    status = status_of(root)
    assert status["success"] is False and status["state"] == "stale"
    assert "FeedError" in status["error"] or "calendar" in status["error"].lower()
    assert_json_and_render_agree(root, success=False, state="stale")
    proc = run_validator(root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_stage3_freefood_radar_13_malformed_status_json_fails_closed(tmp_path, capsys):
    """Malformed status JSON: the refresh is rejected with a typed message,
    the prior snapshot and dashboard are untouched, and the validator
    rejects the corrupt health record."""
    root = good_root(tmp_path)
    snapshot_before = (root / "data/sbengaged-events.json").read_bytes()
    dashboard_before = (root / "docs/dashboard.html").read_bytes()
    (root / "data/sbengaged-status.json").write_text("{not json")
    assert run_refresh(root, 14, fetcher=good_fetch, scorer=stub_scorer, now=NOW) == 1
    assert "Invalid previous refresh status" in capsys.readouterr().out
    assert (root / "data/sbengaged-events.json").read_bytes() == snapshot_before
    assert (root / "docs/dashboard.html").read_bytes() == dashboard_before
    proc = run_validator(root, tmp_path)
    assert proc.returncode != 0
    assert "status" in (proc.stdout + proc.stderr).lower()


def test_stage3_freefood_radar_13_malformed_snapshot_json_fails_closed(tmp_path, capsys):
    """Malformed snapshot JSON: same fail-closed shape as a malformed
    status record, with a typed message naming the snapshot."""
    root = good_root(tmp_path)
    dashboard_before = (root / "docs/dashboard.html").read_bytes()
    (root / "data/sbengaged-events.json").write_text("[1, 2, 3")
    assert run_refresh(root, 14, fetcher=good_fetch, scorer=stub_scorer, now=NOW) == 1
    assert "Invalid previous public snapshot" in capsys.readouterr().out
    assert (root / "docs/dashboard.html").read_bytes() == dashboard_before
    proc = run_validator(root, tmp_path)
    assert proc.returncode != 0


def test_stage3_freefood_radar_13_model_exception_keeps_snapshot(tmp_path):
    """Model failure: a scorer that raises leaves the prior snapshot
    untouched, records a typed stale state naming the failure, and the
    rendered dashboard still matches the retained snapshot."""
    root = good_root(tmp_path)
    before = (root / "data/sbengaged-events.json").read_bytes()

    def boom(rows, root):
        raise RuntimeError("model blew up")

    assert run_refresh(root, 14, fetcher=good_fetch, scorer=boom, now=NOW) == 1
    assert (root / "data/sbengaged-events.json").read_bytes() == before
    status = status_of(root)
    assert status["success"] is False and status["state"] == "stale"
    assert "RuntimeError: model blew up" in status["error"]
    assert_json_and_render_agree(root, success=False, state="stale")
    proc = run_validator(root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_stage3_freefood_radar_13_corrupt_model_provenance_rejected(tmp_path, capsys):
    """Corrupt model provenance: a prior snapshot whose lineage model hash
    no longer matches the model config hash is rejected, never rendered
    over, and the validator rejects it too."""
    root = good_root(tmp_path)
    snapshot = snapshot_of(root)
    snapshot["lineage"]["model_sha256"] = "00" * 32
    (root / "data/sbengaged-events.json").write_text(json.dumps(snapshot))
    dashboard_before = (root / "docs/dashboard.html").read_bytes()
    assert run_refresh(root, 14, fetcher=good_fetch, scorer=stub_scorer, now=NOW) == 1
    assert "Invalid previous public snapshot" in capsys.readouterr().out
    assert (root / "docs/dashboard.html").read_bytes() == dashboard_before
    proc = run_validator(root, tmp_path)
    assert proc.returncode != 0
    assert "model_sha256" in proc.stdout + proc.stderr


def test_stage3_freefood_radar_13_corrupt_scorer_provenance_rejected(tmp_path):
    """A scorer that reports a model hash that is not a SHA-256 digest
    fails the snapshot contract: no snapshot is written, the typed state
    is failed, and the status-only dashboard carries no seeded data."""
    root = make_root(tmp_path)

    def bad_hash(rows, root):
        return {"recipe": "fixture scorer", "config_sha256": "not-a-hash"}

    assert run_refresh(root, 14, fetcher=good_fetch, scorer=bad_hash, now=NOW) == 1
    assert not (root / "data/sbengaged-events.json").exists()
    status = status_of(root)
    assert status["success"] is False and status["state"] == "failed"
    html, d = rendered(root)
    assert d.get("no_snapshot") is True
    proc = run_validator(root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_stage3_freefood_radar_13_renderer_failure_is_interrupted_publish(tmp_path, monkeypatch, capsys):
    """Injected case 4/5: interrupted publish. The refresh writes a new
    snapshot and status, then rendering fails. The run exits 1 with a
    typed message, the stale dashboard is left untouched, and the
    validator rejects the mismatch so no commit can publish it."""
    import renderer
    root = good_root(tmp_path)
    dashboard_before = (root / "docs/dashboard.html").read_bytes()

    def boom(*args, **kwargs):
        raise RuntimeError("render boom")

    monkeypatch.setattr(renderer, "render", boom)
    assert run_refresh(root, 14, fetcher=good_fetch, scorer=stub_scorer, now=NOW) == 1
    assert "Render failed" in capsys.readouterr().out
    assert (root / "docs/dashboard.html").read_bytes() == dashboard_before
    assert status_of(root)["success"] is True
    proc = run_validator(root, tmp_path)
    assert proc.returncode != 0
    assert "dashboard" in (proc.stdout + proc.stderr).lower()


def test_stage3_freefood_radar_13_workflow_commit_requires_validation():
    """Commit-step decision: the refresh workflow validates with the
    standalone validator, commits only when validation succeeded, and
    re-validates before git add; a failed refresh is surfaced as a
    failure instead of being committed silently."""
    workflow = (REPO_ROOT / ".github/workflows/sbengaged.yml").read_text(encoding="utf8")
    assert "python scripts/validate_publication.py" in workflow
    validate_step = workflow.index("Validate publication artifacts")
    commit_step = workflow.index("Commit only generated public artifacts")
    assert "python scripts/validate_publication.py" in workflow[validate_step:commit_step]
    commit_body = workflow[commit_step:]
    assert "steps.validate.outcome == 'success'" in commit_body
    assert commit_body.index("python scripts/validate_publication.py") < commit_body.index("git add")
    assert "steps.refresh.outcome == 'failure'" in workflow
    assert "exit 1" in workflow[workflow.index("Surface a failed source refresh"):]


def test_stage3_freefood_radar_13_ci_runs_publication_validator():
    """CI runs the standalone validator on the committed artifacts, so a
    pull request that strips source proof fails the check."""
    ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf8")
    assert "python scripts/validate_publication.py" in ci


def test_stage3_freefood_radar_13_validator_rejects_missing_source_proof(tmp_path):
    """CI rejects intentionally missing source proof: each tampered root
    fails the standalone validator command, while the good root passes."""
    root = good_root(tmp_path)
    proc = run_validator(root, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "public-source events" in proc.stdout or "OK" in proc.stdout

    def tampered(mutate):
        other = make_root(tmp_path / ("case" + str(tampered.n)))
        tampered.n += 1
        shutil.copytree(root, other, dirs_exist_ok=True)
        snapshot = snapshot_of(other)
        mutate(snapshot)
        (other / "data/sbengaged-events.json").write_text(json.dumps(snapshot))
        return other

    tampered.n = 0
    cases = [
        ("lineage source proof removed",
         lambda s: s["lineage"].pop("source_sha256")),
        ("lineage removed entirely", lambda s: s.pop("lineage")),
        ("diagnostics source hash no longer matches lineage",
         lambda s: s["diagnostics"].__setitem__("source_sha256", "00" * 32)),
        ("model hash no longer matches lineage",
         lambda s: s["model"].__setitem__("config_sha256", "00" * 32)),
        ("seeded record in the snapshot",
         lambda s: s["events"][0].update(id="seed-001", source="seed")),
    ]
    for label, mutate in cases:
        bad = tampered(mutate)
        proc = run_validator(bad, tmp_path)
        assert proc.returncode != 0, label
        assert proc.stdout.strip() or proc.stderr.strip(), label
    # A dashboard whose evidence names a different fetch than the snapshot
    # is rejected even when both JSON records are valid.
    other = make_root(tmp_path / "dashboard-case")
    shutil.copytree(root, other, dirs_exist_ok=True)
    html = (other / "docs/dashboard.html").read_text(encoding="utf8")
    d = evidence_from_html(html)
    d["sbengaged"]["fetched_at"] = "2020-01-01T00:00:00+00:00"
    match = re.search(r"const EVIDENCE=.*?;\n", html, flags=re.S)
    new_html = html.replace(
        match.group(0),
        "const EVIDENCE=" + json.dumps(d).replace("</", "<\\/") + ";\n", 1)
    (other / "docs/dashboard.html").write_text(new_html, encoding="utf8")
    proc = run_validator(other, tmp_path)
    assert proc.returncode != 0
    assert "fetched_at" in proc.stdout + proc.stderr


def test_stage3_freefood_radar_13_fixture_ics_never_mutates_committed_data(tmp_path):
    """The fixture ICS lives only in the test run: a full pipeline run
    against fixture feeds leaves the committed public data untouched."""
    committed = REPO_ROOT / "data/sbengaged-events.json"
    committed_status = REPO_ROOT / "data/sbengaged-status.json"
    before = (hashlib.sha256(committed.read_bytes()).hexdigest(),
              hashlib.sha256(committed_status.read_bytes()).hexdigest())
    root = good_root(tmp_path)
    assert "full-01" in {e["uid"] for e in snapshot_of(root)["events"]}
    after = (hashlib.sha256(committed.read_bytes()).hexdigest(),
             hashlib.sha256(committed_status.read_bytes()).hexdigest())
    assert before == after
    assert "fixtures" not in (REPO_ROOT / "scripts/refresh_sbengaged.py").read_text()
    assert "fixtures" not in (REPO_ROOT / ".github/workflows/sbengaged.yml").read_text()
