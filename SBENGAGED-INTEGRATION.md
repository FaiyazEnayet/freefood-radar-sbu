# Public SB Engaged integration

The event page at https://sbengaged.stonybrook.edu/events advertises the public
ICS feed used by this integration. No NetID, password, browser cookies, private
mobile API or paid API is used. The source redirects its calendar to the official
CampusGroups static host. The old CampusGroups HTML scraper remains a legacy
adapter; it is not the production source for this dashboard.

## Run locally

```sh
python -m pip install -r requirements.txt
python scripts/refresh_sbengaged.py --days 14
python -m pytest tests/test_sbengaged.py -q
```

The refresh writes `data/sbengaged-events.json`, `data/sbengaged-status.json`
and the self-contained `docs/dashboard.html`. Open the HTML directly. A downloaded
copy is a dated snapshot and does not update itself.

The daily GitHub Actions workflow runs at **11:17 UTC**, approximately 7:17 a.m.
New York during daylight saving time and 6:17 a.m. in standard time. GitHub can
delay or disable scheduled jobs, including inactive public-repository schedules.
It also runs on integration-code updates and supports Run workflow. The workflow
must be enabled on the default branch and allowed to write repository contents.
It does not deploy a server or change Pages configuration. A repository already
configured to serve docs/ may publish the new HTML through its existing settings.

## Data contract

- Includes not-ended public occurrences starting before a rolling 14-day cutoff.
- Uses UTC/TZID timestamps and displays America/New_York. All-day events retain
  their date semantics. Floating times, if received, are flagged as assumed NY.
- Deduplicates by source UID, retaining the newest revision. A
  cancelled newest record removes its older occurrence. The precedence
  rule follows iTIP message sequencing (RFC 5546 section 2.1.5): within
  one identity, the highest SEQUENCE obsoletes every lower revision,
  because SEQUENCE is the revision counter (RFC 5545 section 3.8.7.4),
  incremented on every significant change, cancellations included
  (RFC 5546 section 2.1.4); so a cancellation with a higher SEQUENCE
  wins even when its LAST-MODIFIED is older than the active record's.
  LAST-MODIFIED is the secondary key and the first occurrence wins an
  exact tie, so a later exact duplicate cannot override an earlier
  record. (iTIP names DTSTAMP as its own final tie-break; the
  publisher stamps DTSTAMP at feed export time rather than per
  revision, so this parser keeps LAST-MODIFIED as its secondary key.)
  Event identity is the source UID: a changed UID is a distinct listing,
  and it cannot resurrect a cancelled listing under the old UID. A
  component carrying RECURRENCE-ID forms a distinct instance identity
  (UID plus RECURRENCE-ID, RFC 5546 section 2.1.5 rule 1) and is never
  merged with its master record during deduplication; the failure that
  a recurrence-set property triggers names that instance.
- Public locations are retained. “Sign in to download the location” becomes an
  explicit withheld-location state. No coordinates or rooms are inferred.
- Enforces calendar content type, response size and timeouts. Every hop of
  a redirect chain must stay on an approved HTTPS host without embedded
  credentials, and a redirect loop fails immediately instead of being
  retried. The origin robots policy must be retrievable and permit the
  feed; the host a redirect lands on is checked against its own robots
  policy when it publishes one, and a landing path on the origin host is
  rechecked against the origin policy. Cookies set during a fetch are
  never retained; only public metadata is recorded.
- The current feed expands recurring occurrences. If an RRULE, RECURRENCE-ID,
  RDATE or EXDATE property appears, refresh fails visibly rather than
  silently losing or inventing occurrences.
- End semantics follow RFC 5545. A date DTEND is an exclusive end and must
  be strictly later than the DTSTART date; a date DTSTART with no DTEND or
  DURATION lasts one day. A valid DURATION sets the end as elapsed time
  (added in UTC, so a daylight saving transition neither stretches nor
  shrinks it); an invalid DURATION, or DTEND together with DURATION, makes
  that event an invalid record, and a malformed-event share above the
  threshold fails the refresh loudly. A timed event with neither DTEND nor
  DURATION gets no invented duration: it is selected on its start only and
  carries `duration_unknown: true`.
- Parse diagnostics count every newest UID in exactly one reject stage
  (selected, cancelled, out of window, invalid), so those four counts always
  sum to the unique UID count. The total invalid count additionally covers
  components without a UID and components whose revision cannot be ordered.
- A failed fetch, parse or scoring step preserves the last successful snapshot
  and writes a failed status. Demonstration events never replace public data.
- Every refresh classifies the selected-event count against the last good
  snapshot (`initial`, `stable`, `grew`, `shrank`, `verified_empty`,
  `empty_suspect`, `severe_shrink_suspect`, `zero_unverified_suspect`, plus
  `_confirmed` variants) and records the state and both counts in the status
  file and the snapshot diagnostics. A feed whose components are all
  cancelled, expired or outside the window is a verified zero and may
  replace the snapshot. A zero selected count that includes any invalid or
  ambiguous record is never a verified zero: it is
  `zero_unverified_suspect` first, and a second identical observation
  records it as `zero_unverified_confirmed`, still not as verified. A feed
  with no components at all, or a drop below half of a snapshot that had at
  least 20 events, is treated as a suspected source failure: the refresh
  fails, the last good snapshot is kept, and the new result is accepted
  only when a second, identical observation (same count, same source hash)
  confirms it.
- Records source URL, fetch time, source-body hash, window, row diagnostics,
  training-data hash, model recipe, seed and inference library version.

## Prediction limitations

The repository does not ship its classifier binary. Refresh therefore refits the
existing TF-IDF/logistic/sigmoid recipe on the original seed-7, 75% training split
of data/labeled.csv. It does not overwrite historical evaluation metrics or use
the held-out labels for this fit. Probabilities and ranking scores are computed
model outputs, not verified food offers. Historical labeled-text precision,
recall and calibration do not establish accuracy on actual SB Engaged text.
Training dependency versions are recorded; they can affect the rebuilt model.

The top-level original seeded digest remains an offline demo. The new public
dashboard uses its own public-source snapshot and never calls it a live stream.
Upcoming work: human-reviewed real-feed labels, time-split validation, cancelled
event monitoring, and source freshness alerts.

## Authentication

Public ingestion needs no login. A browser session is not permanent and is not
transferred to GitHub Actions. Withheld fields can be viewed on the official
event page after university sign-in. Automating private access requires a
university-approved interface or service identity; do not store personal
credentials or session cookies in this repository.

## Operations runbook

Day-to-day operation of the feed: what runs when, what a failure means, and
where the state lives. Everything here can be exercised offline: the test
suite and a fixture refresh need no network, and the only network operation
in the project is the explicit feed fetch.

### Daily schedule

The workflow `.github/workflows/sbengaged.yml` (Refresh public SB Engaged
events) runs daily at 11:17 UTC, about 7:17 a.m. New York in daylight time
and 6:17 a.m. in standard time. It also runs on pushes that touch the
integration code and on demand through Run workflow. Each run executes the
feed contract tests, refreshes the rolling 14-day snapshot, validates the
publication artifacts, and commits only those artifacts. GitHub can delay
or disable scheduled jobs, including on inactive repositories; if snapshots
stop advancing, first check that the workflow is still enabled.

### When a run fails

The refresh step may fail without immediately failing the job, so validation
and the last good state still run; the final step then fails the run and
points at `data/sbengaged-status.json`. Read a failed run in this order:

1. The closing step confirms the refresh itself failed.
2. The status file (the committed copy on the default branch is the newest
   record) carries the failure state and the row diagnostics.
3. The refresh step log carries the feed error: robots policy, a redirect
   hop, response size, or the parse stage that rejected the feed.

A failed refresh never publishes demonstration events and never overwrites
the last good snapshot. Known open item: the scheduled refresh on the
evening of Thu Oct 29, 2026 (America/New_York) failed and has not yet been
investigated. Work it by the steps above and record the cause here once it
is known.

### Last good snapshots

`data/sbengaged-events.json` is the last good snapshot and
`data/sbengaged-status.json` is its companion status: source URL, fetch
time, source-body hash, window, count classification and diagnostics.
`docs/dashboard.html` is rendered from the two of them. Last good means the
newest snapshot that passed validation. A suspect or failed result is
recorded in the status file only; it replaces the snapshot solely after a
second identical observation confirms it, a verified-empty feed excepted.

### Source schema changes

The parser is deliberately strict about schema surprises. A component
carrying RRULE, RDATE, EXDATE or RECURRENCE-ID fails the refresh visibly,
because the publisher feed arrives already expanded; so does a malformed
share above the contract threshold. If the publisher changes the feed
shape: read the feed error, identify the contract clause it tripped, add a
fixture under `tests/fixtures/` reproducing the new shape, and only then
change parser and contract together. Event identity is the source UID,
plus RECURRENCE-ID for a recurrence instance; a changed UID is a new
listing by design, not a parsing bug.

### Data rights

The feed is public, advertised on the SB Engaged events page, and ingestion
uses no login. No credentials, session cookies or private tokens are stored
in this repository or in any artifact it publishes. Locations the publisher
withholds behind sign-in stay withheld: the snapshot records the explicit
withheld state, and rooms or coordinates are never inferred. One known
limitation: withholding is detected from the publisher's sign-in wording in
the location text, so a withheld location phrased differently would read as
public until the detector learns the new wording. Do not work around a
withheld field; viewing it is a signed-in human action on the official
event page.

### Model retraining decisions

The repository ships no classifier binary. Every refresh refits the
existing TF-IDF/logistic/sigmoid recipe on the original seed-7, 75%
training split of `data/labeled.csv`, and records the training-data hash,
recipe, seed and inference library version in the snapshot diagnostics. The
refit never trains on the held-out labels and never overwrites the
historical evaluation metrics; scores on real feed text remain unverified
model estimates. Retraining decisions follow from that split: the recipe
changes only through a reviewed code change, never as part of a refresh,
and only with new human-reviewed labels or a demonstrated regression behind
it, evaluated on the held-out split before adoption.
