"""Food-quality signals for expected-value ranking.

expected_value = P(free food) x quality_score

quality_score blends: how good the food sounds (cuisine keywords) and
how much of it there is (meal vs snack words). All weights are
transparent and tunable in one place. Scarcity and RSVP phrases are
NOT quality signals: they are access caveats, handled by the evidence
layer below and reported as neutral flags, never as extra value.

The evidence layer (analyze_evidence) reads the same text separately
from the quality score. It answers a different question than the
trained classifier: what does the text itself actually say about a
complimentary offer, who may take it, and what could prevent that.
The classifier probability stays one candidate signal; deterministic
evidence decides the explicit categories, and the two are combined in
combine_offer_likelihood.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Cuisine / item keywords -> desirability weight. Higher = better spread.
CUISINE_WEIGHTS = {
    "pizza": 1.00,
    "bbq": 0.95,
    "barbecue": 0.95,
    "tacos": 0.95,
    "sushi": 0.90,
    "wings": 0.90,
    "chipotle": 0.90,
    "boba": 0.85,
    "bubble tea": 0.85,
    "burgers": 0.85,
    "shawarma": 0.85,
    "biryani": 0.85,
    "dumplings": 0.80,
    "ramen": 0.80,
    "sandwiches": 0.75,
    "subs": 0.75,
    "bagels": 0.70,
    "donuts": 0.70,
    "doughnuts": 0.70,
    "ice cream": 0.70,
    "pasta": 0.70,
    "fried chicken": 0.80,
    "street cart": 0.85,
    "empanadas": 0.75,
    "samosas": 0.70,
    "spring rolls": 0.65,
    "cookies": 0.55,
    "brownies": 0.55,
    "cupcakes": 0.55,
    "popcorn": 0.45,
    "chips": 0.45,
    "soda": 0.40,
    "coffee": 0.50,
}

MEAL_WORDS = {"dinner": 0.25, "lunch": 0.25, "brunch": 0.20, "catered": 0.25,
              "full meal": 0.25, "buffet": 0.25, "feast": 0.25}
SNACK_WORDS = {"light refreshments": -0.25, "snacks": -0.15,
               "refreshments will be served": -0.10, "light snacks": -0.20}

FREE_FOOD_PATTERNS = [
    "free food", "free pizza", "free lunch", "free dinner", "free brunch",
    "free snacks", "free boba", "free coffee", "free donuts", "free ice cream",
    "food will be provided", "food provided", "lunch provided",
    "dinner provided", "refreshments provided", "pizza will be served",
    "free wings",
]

FOOD_RE = re.compile("|".join(sorted(CUISINE_WEIGHTS, key=len, reverse=True)))
MEAL_RE = re.compile("|".join(sorted(MEAL_WORDS, key=len, reverse=True)))
SNACK_RE = re.compile("|".join(sorted(SNACK_WORDS, key=len, reverse=True)))
FREE_RE = re.compile("|".join(sorted(FREE_FOOD_PATTERNS, key=len, reverse=True)))


def quality_score(text: str) -> tuple[float, list[str]]:
    """Return (score in [0, 1.5], matched keywords)."""
    t = text.lower()
    score = 0.45  # prior: an average free-food mention
    hits: list[str] = []
    for m in FOOD_RE.finditer(t):
        w = CUISINE_WEIGHTS[m.group(0)]
        score = max(score, 0.35 + 0.65 * w)
        hits.append(m.group(0))
    for m in MEAL_RE.finditer(t):
        score += MEAL_WORDS[m.group(0)]
        hits.append(m.group(0))
    for m in SNACK_RE.finditer(t):
        score += SNACK_WORDS[m.group(0)]
        hits.append(m.group(0))
    return min(max(score, 0.05), 1.5), sorted(set(hits))


def free_food_keywords(text: str) -> list[str]:
    t = text.lower()
    return sorted({m.group(0) for m in FREE_RE.finditer(t)})


def expected_value(probability: float, text: str) -> tuple[float, float, list[str]]:
    """Return (expected_value, quality, keywords)."""
    q, kw = quality_score(text)
    return probability * q, q, kw


# ---------------------------------------------------------------------------
# Structured evidence inference.
#
# Categories are derived solely from the event title/description text.
# The patterns are deliberately narrow (high precision): each one names
# an explicit statement a reader could point at, not a vibe. A food noun
# by itself is only an indirect mention, and a mention is insufficient
# evidence of a complimentary offer.
# ---------------------------------------------------------------------------

EXPLICIT_COMPLIMENTARY = "explicit_complimentary_offer"
INDIRECT_MENTION = "indirect_mention"
PRICED_SALE = "priced_sale"
BRING_YOUR_OWN = "bring_your_own"
NEGATED_OFFER = "negated_offer"
RESTRICTED_ELIGIBILITY = "restricted_eligibility"
RSVP_PREREQUISITE = "rsvp_prerequisite"
LIMITED_QUANTITY = "limited_quantity"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"

# Categories that travel with an event as neutral access/quality flags.
CAVEAT_CATEGORIES = (
    PRICED_SALE,
    BRING_YOUR_OWN,
    NEGATED_OFFER,
    RESTRICTED_ELIGIBILITY,
    RSVP_PREREQUISITE,
    LIMITED_QUANTITY,
)
# Categories that directly contradict a complimentary offer.
HARD_NEGATIVE_CATEGORIES = (PRICED_SALE, BRING_YOUR_OWN, NEGATED_OFFER)

_FOOD_NOUN = (r"(food|pizza|lunch|dinner|brunch|breakfast|snacks?|"
              r"refreshments|coffee|drinks?|beverages|bagels|donuts)")

EXPLICIT_OFFER_RE = re.compile(
    r"\bcomplimentary\b[^.;]{0,40}\b" + _FOOD_NOUN + r"\b"
    r"|\b" + _FOOD_NOUN + r"\b[^.;]{0,25}\bon (us|the house)\b",
)
PRICED_SALE_RE = re.compile(
    r"\bsold\b|\bfor sale\b|\bfor purchase\b|\bavailable for purchase\b"
    r"|\$\s?\d|\bcash only\b",
)
BRING_YOUR_OWN_RE = re.compile(
    r"\bbring your own\b|\bbyob?\b|\bpack your own\b"
    r"|\bbring a (bagged )?lunch\b",
)
NEGATED_OFFER_RE = re.compile(
    r"\bno (food|refreshments|snacks?|pizza|lunch|dinner|breakfast|"
    r"drinks?|beverages|meals?)\b"
    r"|\b(food|refreshments|snacks?|lunch|dinner)\b[^.;]{0,20}"
    r"\bnot (be )?(served|provided|included|available)\b",
)
_ROLE = (r"(members?|officers?|majors?|seniors?|juniors?|sophomores?|"
         r"first[- ]years?|grad(uate)? students?|faculty|staff|residents?|"
         r"e[- ]?board|board members?|volunteers?|club members?)")
RESTRICTED_ELIGIBILITY_RE = re.compile(
    r"\b" + _ROLE + r"\s+only\b"
    r"|\bonly (open )?to\b|\brestricted to\b|\bmembers[- ]only\b"
    r"|\bopen exclusively to\b|\bexclusively for\b"
    r"|\bmust be a (member|major)\b",
)
RSVP_PREREQUISITE_RE = re.compile(
    r"\brsvp\b[^.;]{0,25}\b(required|needed|necessary)\b"
    r"|\bmust rsvp\b|\bregistration (is )?required\b|\bmust register\b"
    r"|\bsign[- ]?up required\b|\bpre[- ]?registration required\b",
)
LIMITED_QUANTITY_RE = re.compile(
    r"\bwhile supplies last\b|\bwhile it lasts\b|\bsupplies are limited\b"
    r"|\blimited (quantities|quantity|availability|supplies)\b"
    r"|\bfirst \d+\b|\bfirst come\b"
    r"|\bonly \d+ (spots|seats|servings|portions)\b",
)
GENERIC_FOOD_RE = re.compile(
    r"\b(food|refreshments|snacks?|lunch|dinner|breakfast|brunch|meals?|"
    r"drinks?|beverages|coffee)\b",
)

# Evidence-only likelihoods. An explicit offer is strong evidence but
# never 1.0: the text alone cannot guarantee food is complimentary or
# still available when a student arrives.
_LIKELIHOOD_EXPLICIT = 0.9
_LIKELIHOOD_NEGATED = 0.05
_LIKELIHOOD_MENTION_ONLY = 0.2
_LIKELIHOOD_NO_EVIDENCE = 0.1
_LIKELIHOOD_ABSTAIN = 0.5
# With insufficient evidence, no classifier confidence may push the
# combined likelihood across the digest's 0.5 ranking threshold.
_INSUFFICIENT_CAP = 0.45
_EVIDENCE_WEIGHT = 0.65


@dataclass(frozen=True)
class EvidenceAssessment:
    """What the event text itself says, kept apart from the classifier."""

    categories: tuple[str, ...]
    caveats: tuple[str, ...]
    eligibility: str  # "general" | "restricted" | "unspecified"
    status: str  # "scored" | "uncertain" | "insufficient_evidence"
    offer_likelihood: float


def analyze_evidence(text: str) -> EvidenceAssessment:
    """Classify the offer evidence in an event title/description.

    Positive and negative evidence together is a conflict: the result
    abstains with status "uncertain" instead of picking a side.
    Eligibility is a neutral flag, not a score: a restricted offer is
    still an offer, and a general one still needs the evidence above.
    """
    t = text.lower()
    found: list[str] = []
    if FREE_RE.search(t) or EXPLICIT_OFFER_RE.search(t):
        found.append(EXPLICIT_COMPLIMENTARY)
    if PRICED_SALE_RE.search(t):
        found.append(PRICED_SALE)
    if BRING_YOUR_OWN_RE.search(t):
        found.append(BRING_YOUR_OWN)
    if NEGATED_OFFER_RE.search(t):
        found.append(NEGATED_OFFER)
    if RESTRICTED_ELIGIBILITY_RE.search(t):
        found.append(RESTRICTED_ELIGIBILITY)
    if RSVP_PREREQUISITE_RE.search(t):
        found.append(RSVP_PREREQUISITE)
    if LIMITED_QUANTITY_RE.search(t):
        found.append(LIMITED_QUANTITY)
    food_mentioned = bool(FOOD_RE.search(t) or GENERIC_FOOD_RE.search(t))
    if food_mentioned and EXPLICIT_COMPLIMENTARY not in found:
        found.append(INDIRECT_MENTION)

    caveats = tuple(c for c in found if c in CAVEAT_CATEGORIES)
    positive = EXPLICIT_COMPLIMENTARY in found
    negative = any(c in found for c in HARD_NEGATIVE_CATEGORIES)

    if RESTRICTED_ELIGIBILITY in found:
        eligibility = "restricted"
    elif positive:
        eligibility = "general"
    else:
        eligibility = "unspecified"

    if positive and negative:
        status, likelihood = "uncertain", _LIKELIHOOD_ABSTAIN
    elif positive:
        status, likelihood = "scored", _LIKELIHOOD_EXPLICIT
    elif negative:
        status, likelihood = "scored", _LIKELIHOOD_NEGATED
    else:
        found.append(INSUFFICIENT_EVIDENCE)
        status = "insufficient_evidence"
        likelihood = (_LIKELIHOOD_MENTION_ONLY if INDIRECT_MENTION in found
                      else _LIKELIHOOD_NO_EVIDENCE)

    order = (EXPLICIT_COMPLIMENTARY, INDIRECT_MENTION, PRICED_SALE,
             BRING_YOUR_OWN, NEGATED_OFFER, RESTRICTED_ELIGIBILITY,
             RSVP_PREREQUISITE, LIMITED_QUANTITY, INSUFFICIENT_EVIDENCE)
    categories = tuple(sorted(set(found), key=order.index))
    return EvidenceAssessment(categories=categories, caveats=caveats,
                              eligibility=eligibility, status=status,
                              offer_likelihood=likelihood)


def combine_offer_likelihood(ml_probability: float,
                             assessment: EvidenceAssessment) -> float:
    """Combine the classifier signal with the text evidence.

    The classifier is one candidate signal, weighted below the
    deterministic evidence and never treated as proof: explicit
    evidence carries the result when the two disagree, a conflict
    abstains at the neutral midpoint, and a bare mention stays under
    the ranking threshold however confident the classifier is.
    """
    ml = min(max(float(ml_probability), 0.0), 1.0)
    if assessment.status == "uncertain":
        return _LIKELIHOOD_ABSTAIN
    combined = _EVIDENCE_WEIGHT * assessment.offer_likelihood + (1 - _EVIDENCE_WEIGHT) * ml
    if assessment.status == "insufficient_evidence":
        combined = min(combined, _INSUFFICIENT_CAP)
    return round(min(max(combined, 0.01), 0.99), 6)
