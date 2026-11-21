"""Validate the published public-source artifacts of one repo root.

    python scripts/validate_publication.py [root]

Exit 0 when the snapshot, the refresh status, and the rendered
dashboard agree; exit 1 with the problems printed otherwise. This is
the check the refresh workflow runs before committing generated
artifacts, and CI runs on the committed artifacts, so a snapshot with
missing source proof is rejected instead of published.

Checks, all offline and read-only:

- data/sbengaged-status.json exists, parses, and satisfies the
  versioned status contract, with a typed state and an error message
  on any failed state.
- data/sbengaged-events.json, when present, satisfies the snapshot
  contract and the publication contract, and its lineage names the
  exact source and model that produced it: the lineage source hash
  must match the diagnostics source hash, and the lineage model hash
  must match the model config hash. A missing snapshot is accepted
  only with a failed status, which is the legitimate status-only
  state of a first run whose feed never arrived.
- The status and the snapshot agree: a fresh or stale status needs
  the snapshot it describes, and a status model hash must match the
  snapshot lineage model hash.
- docs/dashboard.html exists, carries a production-mode evidence
  payload with no seeded records, and names the same snapshot the
  JSON files name: same fetch time, same event IDs, and the same
  SHA-256 for the snapshot and status files it lists as provenance.

Source-file hashes in the dashboard provenance are deliberately not
checked here: editing source code must not invalidate an already
published artifact. The validator guards publication data proof, and
a refresh regenerates the dashboard when the data changes.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "portfolio_ui"))

from radar.schema import validate_snapshot, validate_status  # noqa: E402
from radar.scrapers.sbengaged import FEED_URL  # noqa: E402
from renderer import validate_publication_snapshot  # noqa: E402


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_json(path, problems, label):
    try:
        return json.loads(Path(path).read_text(encoding="utf8"))
    except FileNotFoundError:
        problems.append(f"missing {label} {path}")
    except ValueError as exc:
        problems.append(f"{label} {path} is not valid JSON ({exc})")
    return None


def _evidence_from_html(html, problems):
    match = re.search(r"const EVIDENCE=(.*?);\n", html, re.S)
    if not match:
        problems.append("docs/dashboard.html carries no EVIDENCE payload")
        return None
    try:
        return json.loads(match.group(1).replace("<\\/", "</"))
    except ValueError as exc:
        problems.append(f"docs/dashboard.html EVIDENCE payload is not valid JSON ({exc})")
        return None


def validate(root):
    """Return a list of problems with the published artifacts under root."""
    root = Path(root)
    problems = []
    snapshot_path = root / "data/sbengaged-events.json"
    status_path = root / "data/sbengaged-status.json"
    dashboard_path = root / "docs/dashboard.html"

    status = _load_json(status_path, problems, "refresh status")
    if status is not None:
        for problem in validate_status(status):
            problems.append(f"refresh status: {problem}")
        if status.get("state") == "failed" and not status.get("error"):
            problems.append("refresh status: a failed state needs an error message")
    snapshot = None
    if snapshot_path.exists():
        snapshot = _load_json(snapshot_path, problems, "publication snapshot")
    if snapshot is not None:
        for problem in validate_snapshot(snapshot):
            problems.append(f"publication snapshot: {problem}")
        for problem in validate_publication_snapshot(snapshot):
            problems.append(f"publication snapshot: {problem}")
        lineage = snapshot.get("lineage") if isinstance(snapshot, dict) else None
        diagnostics = snapshot.get("diagnostics") if isinstance(snapshot, dict) else None
        model = snapshot.get("model") if isinstance(snapshot, dict) else None
        if not isinstance(lineage, dict):
            problems.append("publication snapshot: missing source proof (lineage)")
        else:
            if not lineage.get("source_sha256"):
                problems.append("publication snapshot: missing source proof (lineage.source_sha256)")
            elif isinstance(diagnostics, dict) and lineage.get("source_sha256") != diagnostics.get("source_sha256"):
                problems.append("publication snapshot: lineage.source_sha256 does not match diagnostics.source_sha256")
            if not lineage.get("model_sha256"):
                problems.append("publication snapshot: missing source proof (lineage.model_sha256)")
            elif (isinstance(model, dict) and model.get("config_sha256") is not None
                    and lineage.get("model_sha256") != model.get("config_sha256")):
                problems.append("publication snapshot: lineage.model_sha256 does not match model.config_sha256")
        if isinstance(snapshot, dict):
            source = snapshot.get("source") or {}
            if source.get("url") != FEED_URL:
                problems.append("publication snapshot: source.url is not the approved calendar")
    if snapshot is None and status is not None and status.get("state") != "failed":
        problems.append("refresh status is %r but no publication snapshot exists"
                        % status.get("state"))
    if snapshot is not None and status is not None:
        if status.get("state") in ("fresh", "stale") and not snapshot_path.exists():
            problems.append("refresh status is %r but no publication snapshot exists"
                            % status.get("state"))
        status_model = status.get("model_sha256")
        lineage = snapshot.get("lineage") if isinstance(snapshot, dict) else None
        if status_model is not None and isinstance(lineage, dict):
            if lineage.get("model_sha256") != status_model:
                problems.append("refresh status: model_sha256 does not match snapshot lineage.model_sha256")

    if not dashboard_path.exists():
        problems.append("missing rendered dashboard docs/dashboard.html")
        return problems
    html = dashboard_path.read_text(encoding="utf8")
    if "seed-" in html:
        problems.append("rendered dashboard contains seeded records")
    evidence = _evidence_from_html(html, problems)
    if evidence is not None:
        if evidence.get("production_mode") is not True:
            problems.append("rendered dashboard is not a production-mode artifact")
        provenance = evidence.get("provenance") or {}
        if snapshot is None:
            if evidence.get("no_snapshot") is not True:
                problems.append("rendered dashboard names no snapshot but no status-only state is marked")
            if "sbengaged" in evidence:
                problems.append("rendered dashboard lists events but no publication snapshot exists")
            if (evidence.get("sbengaged_status") or {}).get("success") is not False:
                problems.append("rendered dashboard status-only state does not record the failed refresh")
        else:
            classification = provenance.get("classification", "")
            if "Public SB Engaged" not in classification:
                problems.append("rendered dashboard provenance does not name the public SB Engaged source")
            rendered_snapshot = evidence.get("sbengaged")
            if not isinstance(rendered_snapshot, dict):
                problems.append("rendered dashboard carries no snapshot payload")
            else:
                if rendered_snapshot.get("fetched_at") != snapshot.get("fetched_at"):
                    problems.append("rendered dashboard fetched_at does not match the publication snapshot")
                rendered_ids = [e.get("id") for e in rendered_snapshot.get("events", [])]
                snapshot_ids = [e.get("id") for e in snapshot.get("events", [])]
                if rendered_ids != snapshot_ids:
                    problems.append("rendered dashboard event IDs do not match the publication snapshot")
            files = provenance.get("files") or {}
            for name in ("data/sbengaged-events.json", "data/sbengaged-status.json"):
                listed = files.get(name)
                if listed is not None and (root / name).exists():
                    if listed != _sha256(root / name):
                        problems.append(f"rendered dashboard provenance hash for {name} does not match the file")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".",
                        help="repo root holding data/ and docs/ (default: current directory)")
    args = parser.parse_args(argv)
    problems = validate(args.root)
    if problems:
        for problem in problems:
            print("REJECTED publication artifacts: " + problem)
        return 1
    root = Path(args.root)
    snapshot_path = root / "data/sbengaged-events.json"
    if snapshot_path.exists():
        snapshot = json.loads(snapshot_path.read_text(encoding="utf8"))
        print(f"OK: {len(snapshot.get('events', []))} public-source events")
    else:
        print("OK: no public source snapshot exists; status-only dashboard expected, "
              "no silent fallback.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
