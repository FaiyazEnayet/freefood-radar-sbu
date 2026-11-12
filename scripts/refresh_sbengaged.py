"""Fetch public SB Engaged events and rebuild the self-contained dashboard.

    python scripts/refresh_sbengaged.py --days 14

Exit 1 on fetch/model failure, retaining the last good snapshot. No authentication
or model downloads are needed. This script does not push or deploy anything.

The dashboard is always rendered in production mode: only a validated
public_source_snapshot payload is published, or a marked no-snapshot status
state with zero event listings. Seeded demonstration data is never published;
see scripts/make_dashboard.py --demo for the explicit offline demo path.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from radar.live import refresh


def run_refresh(root, days, fetcher=None, scorer=None, now=None):
    """Run one production refresh; return the process exit code.

    Publication boundary: a snapshot containing seeded records is rejected
    loudly and never rendered.
    """
    kw = {"now": now}
    if fetcher is not None:
        kw["fetcher"] = fetcher
    if scorer is not None:
        kw["scorer"] = scorer
    try:
        succeeded = refresh(root, days, **kw)
    except ValueError as exc:
        print(f"Refresh rejected: {exc}")
        return 1
    root = Path(root)
    sys.path.insert(0, str(root / "portfolio_ui"))
    from renderer import render, validate_publication_snapshot
    snapshot_path = root / "data/sbengaged-events.json"
    if snapshot_path.exists():
        payload = json.loads(snapshot_path.read_text(encoding="utf8"))
        problems = validate_publication_snapshot(payload)
        if problems:
            print("REJECTED publication artifact data/sbengaged-events.json: "
                  + "; ".join(problems))
            return 1
    render(root, "freefood-radar",
           root / "portfolio_ui/original-dashboard-snapshot.html",
           root / "docs/dashboard.html", mode="production")
    if succeeded:
        print("SB Engaged snapshot refreshed")
    else:
        print("Refresh failed; previous source snapshot retained. "
              "Inspect data/sbengaged-status.json.")
    return 0 if succeeded else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14, choices=range(1, 91), metavar="1..90")
    args = parser.parse_args()
    return run_refresh(ROOT, args.days)


if __name__ == "__main__":
    raise SystemExit(main())
