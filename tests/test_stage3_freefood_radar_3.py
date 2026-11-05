"""Stage 3 P3: structured evidence inference for free-food scoring.

The eight adversarial phrases below are hand-written fixtures, not
published events. A food noun is not an offer, an offer is not proof of
general eligibility, and caveats (limited quantity, RSVP) carry no extra
food value. The trained classifier is one candidate signal; deterministic
evidence decides the explicit categories, and conflicting evidence
abstains into an explicit uncertain state.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from radar import digest as digest_mod
from radar import features
from radar.models import Event


class StubModel:
    """Classifier stand-in with a fixed free-food probability."""

    def __init__(self, prob):
        self.prob = prob

    def predict_proba(self, texts):
        p = np.full(len(texts), self.prob)
        return np.column_stack([1 - p, p])


# --- The eight frozen adversarial fixtures -------------------------------


def test_stage3_freefood_radar_3_food_for_thought_is_mention_only():
    a = features.analyze_evidence("Seminar: food for thought")
    assert "indirect_mention" in a.categories
    assert "explicit_complimentary_offer" not in a.categories
    assert "insufficient_evidence" in a.categories
    assert a.status == "insufficient_evidence"
    # A mention alone must not read as a complimentary offer.
    assert a.offer_likelihood <= 0.3


def test_stage3_freefood_radar_3_free_admission_snacks_sold_is_priced():
    a = features.analyze_evidence("Free admission; snacks sold at the table")
    assert "priced_sale" in a.categories
    assert "explicit_complimentary_offer" not in a.categories
    assert "priced_sale" in a.caveats
    assert a.offer_likelihood <= 0.2


def test_stage3_freefood_radar_3_officers_only_is_restricted():
    a = features.analyze_evidence("Pizza for officers only")
    assert "restricted_eligibility" in a.categories
    assert a.eligibility == "restricted"
    assert "restricted_eligibility" in a.caveats


def test_stage3_freefood_radar_3_bring_your_own_lunch():
    a = features.analyze_evidence("Team lunch: bring your own lunch")
    assert "bring_your_own" in a.categories
    assert "explicit_complimentary_offer" not in a.categories
    assert a.offer_likelihood <= 0.2


def test_stage3_freefood_radar_3_no_refreshments_is_negated():
    a = features.analyze_evidence("Guest lecture. No refreshments.")
    assert "negated_offer" in a.categories
    assert "explicit_complimentary_offer" not in a.categories
    assert a.offer_likelihood <= 0.2


def test_stage3_freefood_radar_3_free_pizza_for_all_is_explicit_general():
    a = features.analyze_evidence("Free pizza for all")
    assert "explicit_complimentary_offer" in a.categories
    assert a.eligibility == "general"
    assert a.status == "scored"
    assert a.offer_likelihood >= 0.8
    # An offer is evidence, never a categorical guarantee.
    assert a.offer_likelihood < 1.0


def test_stage3_freefood_radar_3_rsvp_is_caveat_without_extra_value():
    a = features.analyze_evidence("Club social. RSVP required.")
    assert "rsvp_prerequisite" in a.categories
    assert "rsvp_prerequisite" in a.caveats
    with_rsvp, _ = features.quality_score("Free pizza. RSVP required.")
    without, _ = features.quality_score("Free pizza.")
    assert with_rsvp == without


def test_stage3_freefood_radar_3_while_supplies_last_is_caveat_only():
    a = features.analyze_evidence("Free pizza while supplies last")
    assert "limited_quantity" in a.categories
    assert "limited_quantity" in a.caveats
    assert "explicit_complimentary_offer" in a.categories
    with_limit, _ = features.quality_score("Free pizza while supplies last")
    without, _ = features.quality_score("Free pizza")
    assert with_limit == without
    limited_word, _ = features.quality_score("Free pizza (limited)")
    assert limited_word == without


# --- Abstention, flags, and the ML-as-one-signal rule ---------------------


def test_stage3_freefood_radar_3_conflicting_evidence_abstains():
    a = features.analyze_evidence(
        "Free pizza for all! Extra slices sold at the door.")
    assert "explicit_complimentary_offer" in a.categories
    assert "priced_sale" in a.categories
    assert a.status == "uncertain"


def test_stage3_freefood_radar_3_eligible_and_restricted_flags_differ():
    general = features.analyze_evidence("Free pizza for all")
    restricted = features.analyze_evidence("Free pizza for members only")
    assert general.eligibility == "general"
    assert restricted.eligibility == "restricted"
    assert general.eligibility != restricted.eligibility
    assert "restricted_eligibility" in restricted.caveats
    assert "restricted_eligibility" not in general.caveats


def test_stage3_freefood_radar_3_mention_alone_never_crosses_threshold():
    # Even a maximally confident classifier cannot turn a bare mention
    # into a ranked free-food event on its own.
    a = features.analyze_evidence("Study group with pizza")
    combined = features.combine_offer_likelihood(0.99, a)
    assert combined < 0.5


def test_stage3_freefood_radar_3_classifier_is_not_proof_either_way():
    # Explicit evidence outweighs a skeptical classifier...
    a = features.analyze_evidence("Free pizza for all")
    assert features.combine_offer_likelihood(0.05, a) >= 0.5
    # ...and an explicit negation outweighs a confident one.
    n = features.analyze_evidence("No food served at this meeting")
    assert features.combine_offer_likelihood(0.99, n) < 0.5


# --- Digest integration ----------------------------------------------------


def test_stage3_freefood_radar_3_score_events_attaches_evidence():
    events = [
        Event(title="Pizza Party", description="Free pizza for everyone!"),
        Event(title="Budget Meeting", description="No food served."),
        Event(title="Mixed Signals", description=(
            "Free pizza for all! Extra slices sold at the door.")),
    ]
    scored = digest_mod.score_events(events, StubModel(0.9))
    by_title = {e.title: e for e in scored}

    pizza = by_title["Pizza Party"]
    assert pizza.evidence_status == "scored"
    assert "explicit_complimentary_offer" in pizza.evidence_categories
    assert pizza.eligibility == "general"
    assert pizza.food_probability >= 0.5

    budget = by_title["Budget Meeting"]
    assert "negated_offer" in budget.evidence_categories
    assert budget.food_probability < 0.5

    mixed = by_title["Mixed Signals"]
    assert mixed.evidence_status == "uncertain"
    assert "priced_sale" in mixed.access_caveats

    ranked = digest_mod.rank(scored)
    titles = [e.title for e in ranked]
    assert titles == ["Pizza Party"]  # uncertain abstains from the ranking


def test_stage3_freefood_radar_3_event_fields_round_trip():
    e = Event(title="Old snapshot row")
    loaded = Event.from_dict(e.to_dict())
    assert loaded.eligibility == "unspecified"
    assert loaded.evidence_status == "unscored"
    assert loaded.evidence_categories == []
    assert loaded.access_caveats == []
