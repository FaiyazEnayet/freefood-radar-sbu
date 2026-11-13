"""Profile the freefood-radar pipeline stages on real snapshot data.

Measures, from actual runs only, the wall time and peak memory of each
stage (snapshot load, feed parse, classifier training, scoring,
serialization, rendering) plus the artifact bytes of the snapshot and
the rendered production HTML. The input is a controlled local copy of a
real previously published snapshot (data/sbengaged-events.json); no
synthetic public events are used. The network fetch stage is opt-in
(--include-fetch) and otherwise reported as not measured, so this runs
offline and in CI.

Usage:
    PYTHONPATH=src python scripts/benchmark_pipeline.py [--snapshot PATH]
        [--root PATH] [--ics PATH] [--include-fetch] [--output PATH]

The report is JSON on stdout (and in --output when given). Every figure
in it comes from the run that produced it; the report carries no speedup
claim, because this is a measurement, not an optimization result.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "portfolio_ui"))

from radar.live import MODEL_CONFIG, model_config_hash  # noqa: E402


def _peak_rss_mb():
    """Process peak RSS in MiB (high-water mark), or None if unavailable."""
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except (ImportError, OSError, ValueError):
        return None


def _timed(fn):
    """Run fn, returning (result, wall_seconds, peak_rss_mb after the run)."""
    start = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - start, _peak_rss_mb()


def _stage(name, wall_seconds, peak_rss_mb, status="measured", **extra):
    stage = {"name": name, "status": status,
             "wall_seconds": wall_seconds, "peak_rss_mb": peak_rss_mb}
    stage.update(extra)
    return stage


def _not_measured(name, reason):
    return {"name": name, "status": "not_measured",
            "wall_seconds": None, "peak_rss_mb": _peak_rss_mb(), "reason": reason}


def _render_root(snapshot_path: Path, root: Path):
    """A root render_html can read: the snapshot's own root when it holds
    it, else a temporary root holding a copy of the snapshot and status."""
    snapshot_path = Path(snapshot_path)
    root = Path(root)
    if snapshot_path.resolve() == (root / "data/sbengaged-events.json").resolve():
        return root, None
    tmp = Path(tempfile.mkdtemp(prefix="freefood-radar-benchmark-"))
    data = tmp / "data"
    data.mkdir()
    (data / "sbengaged-events.json").write_bytes(snapshot_path.read_bytes())
    status = root / "data/sbengaged-status.json"
    if status.exists():
        (data / "sbengaged-status.json").write_bytes(status.read_bytes())
    return tmp, tmp


def profile_pipeline(snapshot_path, root, ics_path=None, fetcher=None,
                     source_html=None):
    """Profile the pipeline on a local snapshot copy. Returns the report."""
    from radar.classifier import build_pipeline, load_labeled
    from radar.digest import score_events
    from radar.models import Event
    from sklearn.model_selection import train_test_split

    snapshot_path = Path(snapshot_path)
    root = Path(root)
    stages = []

    def load_snapshot():
        raw = snapshot_path.read_bytes()
        return raw, json.loads(raw.decode("utf8"))

    (raw_bytes, payload), load_wall, load_rss = _timed(load_snapshot)
    events = payload.get("events") or []
    stages.append(_stage("load_snapshot", load_wall, load_rss,
                         event_count=len(events),
                         snapshot_bytes=len(raw_bytes)))

    if ics_path is not None:
        from radar.scrapers.sbengaged import parse_calendar
        fetched_at = payload.get("fetched_at") or datetime.now(timezone.utc).isoformat()
        now = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))

        def parse_feed():
            raw_ics = Path(ics_path).read_bytes()
            return parse_calendar(raw_ics, now, 14)

        (parsed, diagnostics), parse_wall, parse_rss = _timed(parse_feed)
        stages.append(_stage("parse", parse_wall, parse_rss,
                             parsed_events=len(parsed),
                             source="test fixture %s" % Path(ics_path).name))
    else:
        stages.append(_not_measured(
            "parse", "no raw ICS input provided; pass --ics to measure parsing"))

    labeled_path = root / "data/labeled.csv"
    labeled = load_labeled(str(labeled_path))
    split = MODEL_CONFIG["training_split"]
    texts, _, labels, _ = train_test_split(
        labeled.text.tolist(), labeled.label.tolist(),
        test_size=split["test_size"], random_state=split["seed"],
        stratify=labeled.label.tolist())

    def train():
        model = build_pipeline()
        model.fit(texts, labels)
        return model

    model, train_wall, train_rss = _timed(train)
    stages.append(_stage("classifier_training", train_wall, train_rss,
                         training_rows=len(texts),
                         labeled_rows=len(labeled)))

    def score():
        return score_events([Event.from_dict(e) for e in events], model)

    scored, score_wall, score_rss = _timed(score)
    stages.append(_stage("scoring", score_wall, score_rss,
                         scored_events=len(scored)))

    def serialize():
        return json.dumps(payload, ensure_ascii=False, indent=2)

    serialized, serialize_wall, serialize_rss = _timed(serialize)
    snapshot_bytes = len(serialized.encode("utf8"))
    stages.append(_stage("serialization", serialize_wall, serialize_rss,
                         serialized_bytes=snapshot_bytes))

    render_root, tmp_root = _render_root(snapshot_path, root)
    try:
        from renderer import render_html
        source = Path(source_html) if source_html else ROOT / "portfolio_ui/original-dashboard-snapshot.html"
        raw_html = source.read_text(encoding="utf8")

        def render():
            return render_html(render_root, "freefood-radar", raw_html, mode="production")

        (html, _rendered_payload), render_wall, render_rss = _timed(render)
        rendered_bytes = len(html.encode("utf8"))
    finally:
        if tmp_root is not None:
            import shutil
            shutil.rmtree(tmp_root, ignore_errors=True)
    stages.append(_stage("rendering", render_wall, render_rss,
                         rendered_html_bytes=rendered_bytes))

    if fetcher is None:
        stages.append(_not_measured(
            "fetch", "network fetch is opt-in; pass --include-fetch to measure it"))
    else:
        try:
            (fetched, _http), fetch_wall, fetch_rss = _timed(fetcher)
            stages.append(_stage("fetch", fetch_wall, fetch_rss,
                                 fetched_bytes=len(fetched) if fetched else 0))
        except Exception as exc:  # noqa: BLE001 - record the failure, do not raise
            stages.append({"name": "fetch", "status": "failed",
                           "wall_seconds": None, "peak_rss_mb": _peak_rss_mb(),
                           "error": "%s: %s" % (type(exc).__name__, exc)})

    import sklearn
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "snapshot_path": snapshot_path.name,
            "snapshot_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "event_count": len(events),
            "schema_version": payload.get("schema_version"),
            "classification": payload.get("classification"),
            "labeled_sha256": hashlib.sha256(labeled_path.read_bytes()).hexdigest(),
            "labeled_rows": len(labeled),
            "config_sha256": model_config_hash(),
            "sklearn_version": sklearn.__version__,
            "python_version": "%d.%d.%d" % sys.version_info[:3],
        },
        "stages": stages,
        "artifacts": {
            "snapshot_bytes": snapshot_bytes,
            "rendered_html_bytes": rendered_bytes,
        },
        "claims": {
            "measured_only": True,
            "speedup_claim": None,
            "note": ("Every wall time, memory and artifact byte figure in this "
                     "report comes from the measured runs above; no speedup is "
                     "claimed."),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", default=None,
                        help="snapshot copy to profile (default: <root>/data/sbengaged-events.json)")
    parser.add_argument("--root", default=str(ROOT),
                        help="repo root holding data/labeled.csv (default: repo root)")
    parser.add_argument("--ics", default=None,
                        help="raw ICS file to measure parsing with")
    parser.add_argument("--include-fetch", action="store_true",
                        help="measure a live feed fetch (needs network)")
    parser.add_argument("--output", default=None,
                        help="also write the JSON report to this path")
    args = parser.parse_args(argv)
    root = Path(args.root)
    snapshot = Path(args.snapshot) if args.snapshot else root / "data/sbengaged-events.json"
    fetcher = None
    if args.include_fetch:
        from radar.scrapers.sbengaged import fetch_calendar
        fetcher = fetch_calendar
    report = profile_pipeline(snapshot, root, ics_path=args.ics, fetcher=fetcher)
    text = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
