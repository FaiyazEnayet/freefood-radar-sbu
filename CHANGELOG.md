# Changelog

## Unreleased

### Added
- FR7: an operations runbook now closes `SBENGAGED-INTEGRATION.md`. It
  covers the six things a maintainer needs without reading the workflow
  source: the daily 11:17 UTC refresh schedule, how to read a failed run
  (the scheduled refresh of Thu Oct 29, 2026 is recorded there as failed
  and not yet investigated), where the last good snapshots live and when a
  suspect result may replace one, how source schema changes are handled,
  the feed's data rights (public ingestion, withheld locations stay
  withheld, plus the wording-based detector's known limitation), and the
  model retraining decision rule (reviewed recipe changes only, never on
  the held-out labels). New tests (`test_stage3_freefood_radar_7`, 9
  cases) pin the runbook's coverage of all six topics, prove a fixture
  refresh completes with networking blocked, that importing the
  integration performs no fetch, and that the published JSON artifacts
  carry no authentication-shaped data. Full suite: 143 tests passing.

### Changed
- P3: event scoring now separates the questions the old single signal
  conflated (`src/radar/features.py`, `src/radar/digest.py`,
  `src/radar/models.py`, `src/radar/live.py`):
  - A structured evidence layer reads each title/description into
    categories: explicit complimentary offer, indirect mention, priced
    sale, bring-your-own, negated offer, restricted eligibility, RSVP
    prerequisite, limited quantity, and insufficient evidence.
    Deterministic high-precision patterns decide the explicit
    categories; the trained classifier remains one candidate signal,
    combined under the evidence and never treated as proof on its own.
  - The ranking probability is now the likelihood of a complimentary
    offer. Access and quality caveats travel as separate neutral flags
    on the event and in snapshot rows, and eligibility is reported as
    general or restricted instead of being baked into the score.
  - "While supplies last", "limited" and "RSVP" no longer add quality
    points in `quality_score`; they are caveats, not extra value.
  - When positive and negative evidence conflict, the event is marked
    uncertain and held out of the ranked digest for review instead of
    being guessed at. A bare food mention stays below the ranking
    threshold however confident the classifier is.
  - Scores are described everywhere as unverified model estimates.
    The historical labeled-data metrics stay untouched for comparison;
    a label-based ranking comparison is still deferred until vouched
    held-out labels exist, and no improvement is claimed.
  - New fixture tests (`test_stage3_freefood_radar_3`, 14 cases) pin
    the eight adversarial phrases, the conflict abstention, the
    differing eligibility flags, and the no-extra-value caveats.
    Full suite: 134 tests passing.

### Fixed
- FR2: calendar revision precedence is now documented against its
  standards source and recurrence instances carry an explicit identity
  (`src/radar/scrapers/sbengaged.py`, `SBENGAGED-INTEGRATION.md`):
  - The precedence follows iTIP message sequencing (RFC 5546 section
    2.1.5): within one identity the highest SEQUENCE obsoletes every
    lower revision (RFC 5545 section 3.8.7.4), so a cancellation with a
    higher SEQUENCE wins even when its LAST-MODIFIED is older than the
    active record's, and the reverse conflict does not. LAST-MODIFIED
    remains the secondary key; the integration contract records why
    DTSTAMP is not used as the tie-break for this publisher.
  - Deduplication identity is the UID plus RECURRENCE-ID for a
    recurrence instance, so an override instance is never merged with
    its master record. Recurrence expansion is still unsupported (the
    publisher feed is already expanded), and the failure a
    recurrence-set property triggers now names the offending instance.
  - New regression tests (`test_stage3_freefood_radar_2`, 5 cases in
    `tests/test_sbengaged.py`) pin the conflicting-revision cancellations
    in both feed orders, the winning revision's content, the named
    recurrence failure, and the failed-refresh behavior that keeps the
    last good snapshot. Full suite: 120 tests passing.

### Fixed
- P12: the SB Engaged fetch boundary now fails closed on the whole HTTP
  chain (`src/radar/scrapers/sbengaged.py`):
  - A slow or failed origin robots read (timeout, connection error) raises
    a feed error instead of surfacing a raw transport error, and the
    robots read itself must stay on approved HTTPS hosts.
  - Every redirect hop must stay on an approved HTTPS host without
    embedded credentials; a redirect loop is a contract failure and is no
    longer retried as a transient error.
  - The robots policy of the host a redirect lands on is enforced: a
    same-host landing path is rechecked against the origin policy, and the
    CampusGroups static host's own robots file is parsed and enforced when
    it publishes one (an unavailable check fails the fetch; no published
    file means no restrictions to enforce).
  - Cookies set by any response during a fetch are dropped before the
    fetch returns; only public metadata is recorded.
  - New mock-only regression tests in `tests/test_sbengaged_http.py`
    (`test_stage3_freefood_radar_12`, 30 cases) cover redirect statuses,
    cross-host and credentialed redirect URLs, HTTPS downgrade, content
    type rejection, oversized streams, robots failures, redirect loops
    and cookie retention. Full suite: 115 tests passing.

### Fixed
- FR4: refreshes are now conditional, versioned and inspectable
  (`src/radar/scrapers/sbengaged.py`, `src/radar/live.py`):
  - `fetch_calendar` sends back the last good snapshot's ETag and
    Last-Modified as If-None-Match and If-Modified-Since. A 304 response
    returns no body and the refresh keeps the snapshot file byte for byte
    while moving health forward (last attempt and last successful fetch,
    state fresh, not_modified recorded).
  - Transient fetch failures (429 and 5xx, or a transport error) are
    retried up to 3 attempts with exponential backoff; other failures
    raise immediately. Retries refetch the whole feed, so UIDs are still
    deduplicated by the parser and no partial result is kept.
  - The model recipe is frozen as `MODEL_CONFIG` in `src/radar/live.py`
    with an immutable SHA-256 config hash. Every snapshot and status file
    now carries one lineage record (schema version, source URL, source
    SHA-256, source ETag and Last-Modified, model hash), and the status
    file carries a state of fresh, stale or failed plus the model hash,
    so a status view can mark stale and failure states and the model
    hash without reading the snapshot. Atomic last-good behavior is
    unchanged: a failed or suspect refresh never replaces the last good
    snapshot.
  - New regression test `test_stage3_freefood_radar_4` (11 cases) pins
    the acceptance fixtures, including the 304 byte-identity case, the
    bounded-retry case and the stale and failed state cases. Full suite:
    85 tests passing.
  - Note: no dashboard HTML or generator output was changed in this
    item; the status and snapshot fields above are the data a status
    view reads, and any dashboard marking that needs new HTML is a
    dashboard-batch follow-up.

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
