"""Stage 3 P1 regression: public-source-only artifact gate.

freefood-radar P1 splits the production render mode from the explicit offline
demo mode at the generator/data-contract level. These tests assert the
acceptance fixtures semantically on the rendered evidence payload and the
publication boundary; they do not change the dashboard HTML.
"""
import json
from copy import deepcopy
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "portfolio_ui"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from radar.scrapers.sbengaged import FeedError
from renderer import render, render_html, validate_publication_snapshot
from refresh_sbengaged import run_refresh

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
SNAPSHOT_HTML = REPO_ROOT / "portfolio_ui/original-dashboard-snapshot.html"


def make_root(tmp_path):
    root = tmp_path / "root"
    for sub in ("data", "models", "docs", "portfolio_ui"):
        (root / sub).mkdir(parents=True)
    shutil.copy(REPO_ROOT / "models/metrics.json", root / "models/metrics.json")
    shutil.copy(REPO_ROOT / "data/seed_events.json", root / "data/seed_events.json")
    shutil.copy(SNAPSHOT_HTML, root / "portfolio_ui/original-dashboard-snapshot.html")
    return root


def ics(*events):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//tests//EN\r\n"
            + "".join(events) + "END:VCALENDAR\r\n").encode()


def vevent(uid="one", summary="Free pizza"):
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:20261018T160000Z\r\n"
            f"DTEND:20261018T180000Z\r\nSUMMARY:{summary}\r\n"
            f"DESCRIPTION:Free food\\nJoin us\r\nLOCATION:SAC 302\r\n"
            f"URL:https://sbengaged.stonybrook.edu/rsvp?id=1\r\nEND:VEVENT\r\n")


def good_fetch():
    return ics(vevent("one"), vevent("two")), {"fetched": "test"}


def fail_503():
    raise FeedError("HTTP 503")


def stub_scorer(rows, root):
    return {"test_model": True}


def evidence_from_html(html):
    m = re.search(r"const EVIDENCE=(.*?);\n", html, re.S)
    assert m, "EVIDENCE payload not found in rendered HTML"
    return json.loads(m.group(1).replace("<\\/", "</"))


def test_stage3_freefood_radar_1_first_run_503_is_status_only(tmp_path):
    """Fixture 1/5: no snapshot + HTTP 503 -> success=false, zero event
    listings, no seeded IDs/values in the rendered evidence payload, and the
    job signals failure (exit 1)."""
    root = make_root(tmp_path)
    rc = run_refresh(root, 14, fetcher=fail_503, scorer=stub_scorer, now=NOW)
    assert rc == 1
    status = json.loads((root / "data/sbengaged-status.json").read_text())
    assert status["success"] is False and "503" in status["error"]
    assert not (root / "data/sbengaged-events.json").exists()
    html = (root / "docs/dashboard.html").read_text(encoding="utf8")
    d = evidence_from_html(html)
    assert d.get("production_mode") is True
    assert d.get("no_snapshot") is True
    assert "sbengaged" not in d
    assert d.get("metrics") == []
    assert d.get("figures") == []
    assert d.get("sbengaged_status", {}).get("success") is False
    assert "seed-" not in html
    assert "seeded event records" not in html


def test_stage3_freefood_radar_1_failure_keeps_snapshot_byte_identical(tmp_path):
    """Fixture 2: good public snapshot + HTTP 503 -> snapshot byte-identical,
    failure shown, every listed event has source=sbengaged."""
    root = make_root(tmp_path)
    assert run_refresh(root, 14, fetcher=good_fetch, scorer=stub_scorer, now=NOW) == 0
    before = (root / "data/sbengaged-events.json").read_bytes()
    assert run_refresh(root, 14, fetcher=fail_503, scorer=stub_scorer, now=NOW) == 1
    assert (root / "data/sbengaged-events.json").read_bytes() == before
    html = (root / "docs/dashboard.html").read_text(encoding="utf8")
    d = evidence_from_html(html)
    events = d["sbengaged"]["events"]
    assert len(events) == 2
    assert all(e["source"] == "sbengaged" for e in events)
    assert d["sbengaged_status"]["success"] is False
    assert "seed-" not in html


def test_stage3_freefood_radar_1_successful_refresh_is_public_only(tmp_path):
    """Fixture 3: successful public refresh -> embedded payload contains only
    public event records plus separately labeled historical evaluation, zero
    demo records/figures."""
    root = make_root(tmp_path)
    assert run_refresh(root, 14, fetcher=good_fetch, scorer=stub_scorer, now=NOW) == 0
    payload = json.loads((root / "data/sbengaged-events.json").read_text())
    assert validate_publication_snapshot(payload) == []
    html = (root / "docs/dashboard.html").read_text(encoding="utf8")
    d = evidence_from_html(html)
    assert d.get("production_mode") is True
    assert "no_snapshot" not in d
    events = d["sbengaged"]["events"]
    assert len(events) == 2 and all(e["source"] == "sbengaged" for e in events)
    assert "seed-" not in html
    assert d["metrics"] == []
    figs = d["figures"]
    assert len(figs) == 6
    for i in (0, 1, 4, 5):
        assert figs[i].get("redacted") is True, f"figure {i} must be redacted in production"
    assert "Classifier report" in figs[2]["layout"]["title"]["text"]
    assert "Calibration" in figs[3]["layout"]["title"]["text"]
    assert not any("seed-" in json.dumps(f) for f in figs)


def test_stage3_freefood_radar_1_workflow_never_references_seed():
    """Fixture 4: no path from the workflow calls scripts/make_seed.py or
    seed_events.json."""
    text = (REPO_ROOT / ".github/workflows/sbengaged.yml").read_text(encoding="utf8")
    assert "make_seed" not in text
    assert "seed_events" not in text


def test_stage3_freefood_radar_1_rejects_seeded_snapshot(tmp_path):
    """A snapshot containing seeded records is rejected at the publication
    boundary: exit 1 and no dashboard render."""
    root = make_root(tmp_path)
    bad = {"classification": "public_source_snapshot", "schema_version": 1,
           "events": [{"id": "seed-000", "source": "corq", "title": "x"}]}
    (root / "data/sbengaged-events.json").write_text(json.dumps(bad))
    rc = run_refresh(root, 14, fetcher=fail_503, scorer=stub_scorer, now=NOW)
    assert rc == 1
    assert not (root / "docs/dashboard.html").exists()


def test_stage3_freefood_radar_1_validate_publication_contract():
    good = json.loads((REPO_ROOT / "tests/fixtures/schema-v2-consumer.json").read_text())["snapshot"]
    assert validate_publication_snapshot(good) == []
    seeded = deepcopy(good)
    seeded["events"][0].update(id="seed-001", source="instagram")
    assert validate_publication_snapshot(seeded)
    wrong_class = deepcopy(good)
    wrong_class["classification"] = "demo"
    assert validate_publication_snapshot(wrong_class)
    wrong_schema = deepcopy(good)
    wrong_schema["schema_version"] = 3
    assert validate_publication_snapshot(wrong_schema)
    assert validate_publication_snapshot("not a dict")
    assert validate_publication_snapshot(None)


def test_stage3_freefood_radar_1_demo_mode_is_explicit_and_separate(tmp_path):
    """Demo generation stays behind the explicit non-production command path;
    it keeps seeded content and refuses the production artifact path."""
    root = make_root(tmp_path)
    raw = SNAPSHOT_HTML.read_text(encoding="utf8")
    html, d = render_html(root, "freefood-radar", raw, mode="demo")
    assert "production_mode" not in d
    assert d["metrics"] and d["metrics"][0][1] == "seeded event records"
    assert len(d["figures"]) == 6
    assert not any(isinstance(f, dict) and f.get("redacted") for f in d["figures"])
    with pytest.raises(ValueError):
        render(root, "freefood-radar", SNAPSHOT_HTML,
               root / "docs/dashboard.html", mode="demo")
    out = root / "docs/demo-dashboard.html"
    render(root, "freefood-radar", SNAPSHOT_HTML, out, mode="demo")
    assert out.exists()


def test_stage3_freefood_radar_1_make_dashboard_no_silent_fallback(tmp_path):
    """scripts/make_dashboard.py in production mode refuses to build when no
    public snapshot exists instead of silently falling back to seeded demo
    content; the demo refuses the production path."""
    root = make_root(tmp_path)
    from make_dashboard import main as build_main
    assert build_main(root=root, demo=False) == 1
    assert not (root / "docs/dashboard.html").exists()
    assert build_main(root=root, demo=True,
                      output=str(root / "docs/dashboard.html")) == 1
