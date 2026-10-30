# Changelog

## Unreleased

## [v1.0.0] - 2026-10-30

### Docs
- Centered the README header: uniform dark badge labels and three action buttons in the dashboard palette (Open Dashboard, Explore Method, Run Locally).

### Fixed
- P2: standards-aware ICS validation and a zero-selection proof for the SB
  Engaged calendar feed (`src/radar/scrapers/sbengaged.py`,
  `src/radar/live.py`, documented in `SBENGAGED-INTEGRATION.md`):
  - A date DTEND is treated as an exclusive end and must be strictly later
    than the DTSTART date; a date event with no DTEND or DURATION lasts one
    day. A valid DURATION sets the end as elapsed time (added in UTC, so a
    daylight saving transition does not stretch or shrink it); an invalid
    DURATION, or DTEND together with DURATION, is an invalid record. A timed
    event with neither gets no invented duration and carries
    `duration_unknown: true`.
  - RRULE, RECURRENCE-ID, RDATE and EXDATE now all fail the refresh
    visibly instead of silently dropping or inventing occurrences.
  - UID precedence is frozen: SEQUENCE first, LAST-MODIFIED second, first
    occurrence wins an exact tie. A changed UID is a distinct listing and
    cannot resurrect a cancelled listing under the old UID.
  - Parse diagnostics count every newest UID in exactly one stage
    (selected, cancelled, out of window, invalid), and a zero selected
    count is `verified_empty` only when every UID is cancelled or out of
    window with no invalid records; otherwise it is
    `zero_unverified_suspect` (then `zero_unverified_confirmed` on a second
    identical observation), never a verified zero.
  - New regression test `test_stage3_freefood_radar_2` (26 cases) pins the
    acceptance fixtures, including the DST fall-back, floating-time,
    malformed-percentage and FR1-compatible verified-zero cases. Full suite:
    74 tests passing.
- P1: the production dashboard can no longer carry seeded demonstration data.
  `portfolio_ui/renderer.py` now splits production render mode from the
  explicit offline demo mode at the generator/data-contract level:
  - Production (default) requires a valid `public_source_snapshot` payload
    (classification, schema version and per-event source validated at the
    publication boundary) or renders a marked no-snapshot status-only state
    with zero event listings. It never reads `data/seed_events.json`, never
    packages seeded event records, and replaces seeded demonstration charts
    with explicit redaction markers while keeping the separately labeled
    historical evaluation figures (classifier report, calibration) in place.
  - A snapshot containing seeded records is rejected loudly and never
    rendered (`scripts/refresh_sbengaged.py` publication check).
  - Demo generation is now explicit only: `scripts/make_dashboard.py --demo`
    writes to a non-production path (default `docs/demo-dashboard.html`) and
    refuses the production artifact path; `renderer.py --mode demo` likewise.
    Without `--demo`, `make_dashboard.py` refuses to build when no public
    snapshot exists instead of silently falling back to seeded content.
  - New regression test `test_stage3_freefood_radar_1` (8 cases) pins the
    acceptance fixtures. Full suite: 48 tests passing.
  - Note: the dashboard-side dispatch that routes a no-snapshot production
    render to the marked status-only view (instead of the demo fallback) and
    the workflow publication gate are tracked as follow-ups; no dashboard
    HTML or workflow YAML was changed in this item.
- FR1: a syntactically valid but empty feed, or a severe drop in the
  selected-event count, can no longer silently replace the last good SB
  Engaged snapshot as a successful refresh. `refresh` in `src/radar/live.py`
  now classifies every count change (`initial`, `stable`, `grew`, `shrank`,
  `verified_empty`, `empty_suspect`, `severe_shrink_suspect`, and
  `_confirmed` variants) and records the state plus previous/current counts
  in `data/sbengaged-status.json` and the snapshot diagnostics.
  - A feed whose components are all cancelled, expired or outside the
    window is a verified zero and replaces the snapshot immediately.
  - A feed with zero components, or a drop below half of a snapshot with
    at least 20 selected events, fails the refresh and keeps the last good
    snapshot; it is accepted only when a second, identical observation
    (same selected count, same source hash) confirms it.
  - `parse_calendar` diagnostics now also report `out_of_window`, so a
    verified zero can be told apart from a contract problem.
  - New fixtures (`tests/fixtures/sbengaged_*.ics`) and 8 refresh-level
    tests in `tests/test_feed_guards.py` cover both failure modes.
    Full suite: 40 tests passing.
