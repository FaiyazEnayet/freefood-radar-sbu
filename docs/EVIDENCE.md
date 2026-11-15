# Evidence and validation record

How every public claim in this repository is grounded, and what is
still unmeasured. Every metric in this record carries a provenance
class; 0 metrics are missing one.

Provenance classes:

- **observed**: measured on real data, with the measurement named.
- **constructed**: built from fixed fixtures, or a property of the
  algorithm as implemented.
- **inferred**: a reasoned reading of observed or constructed
  material, stated as a judgment rather than a measurement.
- **unverified**: no measurement behind it yet.

## Claim provenance

| Claim | Recorded value | Provenance | Source |
| :--- | :--- | :--- | :--- |
| Production source is the public SB Engaged calendar; no login required | Public ICS feed advertised on the SB Engaged events page | observed | SBENGAGED-INTEGRATION.md |
| Daily refresh at 11:17 UTC, plus manual refresh | Workflow schedule and run history | observed | .github/workflows/sbengaged.yml |
| Historical labeled-text test, 40 held-out examples | Precision 89.5%, recall 85.0%, accuracy 87.5% | observed | models/metrics.json (seed 7; 120 train, 40 test of 160 labeled) |
| Calibration curve (predicted probability vs observed rate, cross-validated on the labeled set) | Observed on labeled text only; does not establish calibration on feed text | observed | models/calibration.npz |
| Classifier outputs are sigmoid-calibrated probabilities | Platt scaling fitted on the labeled-text training split | constructed | src/radar/classifier.py |
| A score of 0.8 means food four times out of five | No such rate is claimed; the frequency reading was removed | unverified | Removed claim; see src/radar/classifier.py |
| Ranking score shows how worthwhile an event is | Heuristic ranking score: offer likelihood times food-quality weight; utility not empirically verified | inferred | src/radar/features.py, src/radar/digest.py |
| Seeded demo digest and demo dashboard figures | Built from the fixed fixture data/seed_events.json (week of Oct 12-18, 2026), offline only | constructed | scripts/build_digest.py, scripts/make_dashboard.py --demo |
| Food availability for any live-feed event | No confirmed offers; 0 unverified events rendered as confirmed | unverified | No live outcome labels recorded yet |
| Calibration of the classifier on live-feed text | No measurement yet | unverified | No live outcomes recorded yet |

Calibrated algorithm, unmeasured feed calibration: the classifier
applies sigmoid (Platt) scaling fitted on the labeled-text training
split, and the calibration curve shows predicted probability against
observed rate on held-out labeled text only. That does not establish
calibration on feed text, where no food outcomes have been recorded
yet. A score of 0.8 is a model estimate, not a measured four-in-five
rate.

## Error taxonomy

The evidence layer in `src/radar/features.py` classifies what the
event text itself says. Each category is a narrow pattern a reader
can point at in the text, not a vibe:

| Category | Meaning |
| :--- | :--- |
| explicit_complimentary_offer | Text states food is complimentary |
| indirect_mention | A food noun is mentioned with no offer statement |
| priced_sale | Food is sold, not complimentary |
| bring_your_own | Attendees supply their own food |
| negated_offer | Text states no food is served |
| restricted_eligibility | Offer limited to a named group |
| rsvp_prerequisite | Attendance requires RSVP or registration |
| limited_quantity | Food is limited or first-come |
| insufficient_evidence | No category above matched |

Rules: positive and negative evidence together abstain with status
`uncertain` instead of picking a side; with insufficient evidence
the combined likelihood is capped at 0.45 and never crosses the 0.5
ranking threshold, however confident the classifier is.

The refresh in `src/radar/live.py` classifies each selected-event
count against the last good snapshot: `initial`, `stable`, `grew`,
`shrank`, `verified_empty`, `empty_suspect`,
`severe_shrink_suspect`, `zero_unverified_suspect`, plus `_confirmed`
variants. A suspect state never replaces the last good snapshot
until a second identical observation confirms it; only a verified
empty feed may replace it outright.

## Validation record

Validated so far:

- Held-out labeled-text test (seed 7): 160 labeled blurbs, 120
  train / 40 test; precision 89.5%, recall 85.0%, accuracy 87.5%
  (models/metrics.json).
- Offline fixture refresh: the contract suite runs with no network
  (tests/test_sbengaged.py and the other fixture tests).
- Schema contracts: snapshot and status files validate against
  versioned schemas (src/radar/schema.py).
- Refresh profiling: cached and fresh scores identical to 1e-12 on
  the controlled local copy of the published snapshot
  (scripts/benchmark_pipeline.py).

Not yet validated:

- Food availability on the feed: no human-reviewed real-feed labels
  yet.
- Calibration on feed text: no outcomes recorded yet.
- Cancelled-event monitoring and source freshness alerts: listed as
  upcoming work in SBENGAGED-INTEGRATION.md.

## How the artifacts relate

| Artifact | Generated from | Generator |
| :--- | :--- | :--- |
| data/sbengaged-events.json | Public SB Engaged ICS feed, rolling 14-day window | scripts/refresh_sbengaged.py (daily workflow) |
| data/sbengaged-status.json | Same refresh run | scripts/refresh_sbengaged.py |
| docs/dashboard.html | The two files above | portfolio_ui/renderer.py |
| digest.md | Fixed fixture data/seed_events.json, offline only | scripts/build_digest.py |
| docs/demo.gif | Fixed seeded fixture, offline only | scripts/make_demo_gif.py |
| docs/results.png | Committed public feed snapshot data/sbengaged-events.json | scripts/make_results.py |

The digest and dashboard present scores as unverified model
estimates, never as confirmed listings, and every event links to its
official source listing.
