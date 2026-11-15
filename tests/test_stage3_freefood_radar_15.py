"""P15: evidence-quality documentation / neutral API contracts.

Every public classifier/evaluation claim must carry a provenance class
(observed, constructed, inferred, unverified), the unproven
"0.8 means 4 out of 5" frequency claim must be gone, the ranking score
must be named a heuristic ranking score, seeded demo scripts must be
marked offline only, and the committed offline digest must match a
regeneration from its fixed fixture.
"""

from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from radar import digest as digest_mod
from radar import features
from radar.classifier import __doc__ as CLASSIFIER_DOC
from radar.models import Event

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE_CLASSES = ("observed", "constructed", "inferred", "unverified")


def _stub_model(probability: float):
    class Stub:
        def predict_proba(self, texts):
            return np.array([[1.0 - probability, probability]] * len(texts))

    return Stub()


def _ranked_digest_text() -> str:
    event = Event(
        title="Pizza Party",
        description="Free pizza for everyone!",
        club="Test Club",
        location="SAC",
        starts_at=datetime(2026, 10, 12, 12, 0),
        source="test",
    )
    scored = digest_mod.score_events([event], _stub_model(0.8))
    ranked = digest_mod.rank(scored)
    assert len(ranked) == 1
    return digest_mod.render_digest(ranked, day=date(2026, 10, 12))


def test_stage3_freefood_radar_15_classifier_docstring_has_no_unproven_frequency_claim():
    assert "four times out of five" not in CLASSIFIER_DOC
    assert "4 out of 5" not in CLASSIFIER_DOC
    assert "four out of five" not in CLASSIFIER_DOC


def test_stage3_freefood_radar_15_classifier_docstring_distinguishes_calibrated_algorithm_from_live_calibration():
    doc = CLASSIFIER_DOC.lower()
    assert "calibrat" in doc
    assert "live" in doc
    assert "unmeasured" in doc or "unverified" in doc or "not been measured" in doc


def test_stage3_freefood_radar_15_render_digest_uses_heuristic_ranking_score():
    text = _ranked_digest_text()
    assert "heuristic ranking score" in text
    assert "expected value" not in text.lower()
    assert "ranking score" in text
    assert "unverified" in text
    assert "confirmed food" not in text.lower()
    assert "verified offer" not in text.lower()


def test_stage3_freefood_radar_15_heuristic_ranking_score_known_answer():
    assert hasattr(features, "heuristic_ranking_score")
    value, quality, keywords = features.heuristic_ranking_score(
        0.8, "Free pizza for everyone!")
    assert value == pytest.approx(0.8, abs=1e-12)
    assert quality == pytest.approx(1.0, abs=1e-12)
    assert "pizza" in keywords


def test_stage3_freefood_radar_15_pyproject_description_names_single_public_source():
    text = (ROOT / "pyproject.toml").read_text()
    description = [line for line in text.splitlines()
                   if line.startswith("description = ")][0]
    assert "scrape" not in description.lower()
    assert "SB Engaged" in description


def test_stage3_freefood_radar_15_evidence_record_classifies_every_claim():
    record = ROOT / "docs" / "EVIDENCE.md"
    assert record.exists()
    text = record.read_text()
    for cls in PROVENANCE_CLASSES:
        assert cls in text
    section = text.split("## Claim provenance", 1)[1].split("## ", 1)[0]
    rows = [line for line in section.splitlines()
            if line.startswith("|") and "---" not in line
            and "Claim" not in line]
    assert len(rows) >= 6
    for row in rows:
        assert any(cls in row for cls in PROVENANCE_CLASSES), row
    live_rows = [row for row in rows if "live" in row.lower()]
    assert live_rows
    assert all("unverified" in row for row in live_rows), live_rows
    assert "error taxonomy" in text.lower()
    assert "validation record" in text.lower()


def test_stage3_freefood_radar_15_demo_scripts_marked_offline_only():
    for name in ("make_seed.py", "build_digest.py", "make_demo_gif.py"):
        source = (ROOT / "scripts" / name).read_text()
        assert "offline" in source.lower(), name
    for name in ("make_dashboard.py", "make_demo_gif.py"):
        source = (ROOT / "scripts" / name).read_text()
        assert "expected value" not in source.lower(), name
        assert "live dashboard" not in source.lower(), name


def test_stage3_freefood_radar_15_committed_digest_matches_fixed_fixture_regeneration():
    from build_digest import load_seed_events
    from radar.classifier import load_model

    events = load_seed_events()
    model = load_model()
    text, ranked = digest_mod.build_digest(
        events, model, day=date(2026, 10, 12))
    assert ranked, "fixed fixture must still rank events"
    assert text == (ROOT / "digest.md").read_text()


def test_stage3_freefood_radar_15_readme_links_evidence_record():
    readme = (ROOT / "README.md").read_text()
    assert "docs/EVIDENCE.md" in readme
    assert "Provenance" in readme
    assert "unverified" in readme.lower()
