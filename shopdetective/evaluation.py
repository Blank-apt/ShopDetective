"""Keyword baseline and evaluation metrics for the labelled synthetic set.

The baseline is deliberately simple: it is the comparison point for the
model, not a product feature.

Two metric families are reported, because they answer different questions:

* ``raw_category``: is the *returned* category right? Every row is scored on
  the category the classifier returned, even if it was flagged for review or
  its quote failed the evidence check. A failed/invalid response counts as a
  miss. This measures the classifier's labelling, not what the app accepts.
* ``accepted_decisions``: what would the app accept *automatically*? A row is
  auto-accepted only when a valid prediction exists, it is not flagged
  ``needs_review``/``unclear``, and (for complaints) its quote passed the
  exact-substring check. Everything else goes to human review. All rows stay
  in the denominators; review/failure is never dropped from the sample.

Expected ``unclear`` rows: the app never auto-accepts ``unclear``, so for those
rows the correct automatic outcome is *routing to review*. They count toward
coverage and review rate, an auto-accepted decision on them is an incorrect
automatic decision, and ``expected_unclear_routed_to_review`` reports how many
were correctly routed. ``unclear`` has no auto-accepted per-category row.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .schemas import Category

KEYWORDS: dict[str, tuple[str, ...]] = {
    "sizing": (
        "too small",
        "too big",
        "too large",
        "too tight",
        "too long",
        "too short",
        "tight",
        "small",
        "large",
        "big",
        "baggy",
        "loose",
        "snug",
        "fit",
        "size up",
        "roomy",
        "huge",
        "enormous",
    ),
    "delivery": (
        "late",
        "delay",
        "never arrived",
        "never came",
        "not arrived",
        "shipping",
        "courier",
        "arrived",
        "delivery",
        "post",
        "tracking",
        "crushed",
        "days",
    ),
    "wrong_item": (
        "wrong colour",
        "wrong color",
        "instead of",
        "different",
        "wrong item",
        "wrong size",
        "got a",
        "received someone",
        "by mistake",
        "i ordered",
    ),
    "other": (
        "pilled",
        "broke",
        "stitching",
        "seam",
        "hole",
        "smell",
        "itchy",
        "faded",
        "bled",
        "quality",
        "fell off",
        "cracking",
    ),
}
POSITIVE = ("love", "great", "perfect", "happy", "good", "fine", "thanks", "thank you", "again", "well")

LABELS = [c.value for c in Category]


@dataclass
class BaselinePrediction:
    example_id: str
    category: str
    evidence_quote: str | None
    abstained: bool


def keyword_classify(example_id: str, text: str) -> BaselinePrediction:
    lower = text.lower()
    hits: dict[str, str] = {}
    for cat, words in KEYWORDS.items():
        for w in words:
            m = re.search(r"\b" + re.escape(w) + r"\b", lower)
            if m:
                hits[cat] = text[m.start() : m.end()]
                break
    if len(hits) == 1:
        cat, quote = next(iter(hits.items()))
        return BaselinePrediction(example_id, cat, quote, False)
    if len(hits) > 1:
        return BaselinePrediction(example_id, "unclear", None, True)
    if any(re.search(r"\b" + re.escape(p) + r"\b", lower) for p in POSITIVE):
        return BaselinePrediction(example_id, "no_complaint", None, False)
    return BaselinePrediction(example_id, "unclear", None, True)


AUTO_LABELS = [lab for lab in LABELS if lab != Category.UNCLEAR.value]

METRIC_DEFINITIONS = {
    "raw_category": (
        "Scores the category the classifier returned for every example, including predictions flagged "
        "for review or with an unsupported quote. No valid prediction counts as a miss. Not a measure "
        "of automatically accepted decisions."
    ),
    "accepted_decisions": (
        "A prediction is auto-accepted only if it is valid, not flagged needs_review, not 'unclear', and "
        "(for complaints) its quote is an exact substring of the message. All other rows are routed to human "
        "review and stay in every denominator. Expected 'unclear' rows are correct only when routed to review."
    ),
}


@dataclass
class Scored:
    example_id: str
    expected: str
    predicted: str | None  # None = no valid prediction returned
    abstained: bool  # flagged needs_review or "unclear"
    quote_valid: bool | None  # None when no complaint quote was required

    @property
    def auto_accepted(self) -> bool:
        """Would the app accept this prediction without human review?"""
        return (
            self.predicted is not None
            and not self.abstained
            and self.predicted != Category.UNCLEAR.value
            and self.quote_valid is not False
        )


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = _ratio(tp, tp + fp)
    rc = _ratio(tp, tp + fn)
    f1 = 2 * p * rc / (p + rc) if p is not None and rc is not None and p + rc else (0.0 if tp + fn else None)
    return {"support": tp + fn, "predicted": tp + fp, "precision": p, "recall": rc, "f1": f1}


def raw_category_metrics(rows: list[Scored]) -> dict:
    n = len(rows)
    per: dict[str, dict] = {}
    for lab in LABELS:
        tp = sum(1 for r in rows if r.predicted == lab and r.expected == lab)
        fp = sum(1 for r in rows if r.predicted == lab and r.expected != lab)
        fn = sum(1 for r in rows if r.expected == lab and r.predicted != lab)
        per[lab] = _prf(tp, fp, fn)
    f1s = [v["f1"] for v in per.values() if v["support"]]
    correct = sum(1 for r in rows if r.predicted == r.expected)
    return {
        "definition": METRIC_DEFINITIONS["raw_category"],
        "n": n,
        "accuracy": _ratio(correct, n),
        "macro_f1": sum(f1s) / len(f1s) if f1s else None,
        "per_category": per,
        "confusion": dict(Counter(f"{r.expected}->{r.predicted}" for r in rows if r.predicted != r.expected)),
    }


def accepted_decision_metrics(rows: list[Scored]) -> dict:
    n = len(rows)
    accepted = [r for r in rows if r.auto_accepted]
    correct = [r for r in accepted if r.predicted == r.expected]
    no_valid = sum(1 for r in rows if r.predicted is None)
    quote_rejected = sum(1 for r in rows if r.predicted is not None and r.quote_valid is False)
    per: dict[str, dict] = {}
    for lab in AUTO_LABELS:
        tp = sum(1 for r in accepted if r.predicted == lab and r.expected == lab)
        fp = sum(1 for r in accepted if r.predicted == lab and r.expected != lab)
        # Every expected row not correctly auto-accepted is a miss, including reviewed/failed ones.
        fn = sum(1 for r in rows if r.expected == lab) - tp
        per[lab] = _prf(tp, fp, fn)
    f1s = [v["f1"] for v in per.values() if v["support"]]
    unclear = [r for r in rows if r.expected == Category.UNCLEAR.value]
    return {
        "definition": METRIC_DEFINITIONS["accepted_decisions"],
        "n": n,
        "auto_accepted": len(accepted),
        "auto_acceptance_coverage": _ratio(len(accepted), n),
        "routed_to_review": n - len(accepted),
        "review_or_failure_rate": _ratio(n - len(accepted), n),
        "flagged_or_unclear": sum(1 for r in rows if r.predicted is not None and r.abstained),
        "quote_rejected": quote_rejected,
        "no_valid_prediction": no_valid,
        "accepted_precision": _ratio(len(correct), len(accepted)),
        "correct_auto_accepted_rate": _ratio(len(correct), n),
        "incorrect_auto_accepted": len(accepted) - len(correct),
        "macro_f1_auto": sum(f1s) / len(f1s) if f1s else None,
        "expected_unclear": len(unclear),
        "expected_unclear_routed_to_review": sum(1 for r in unclear if not r.auto_accepted),
        "per_category": per,
    }


def metrics(rows: list[Scored]) -> dict:
    quote_checks = [r.quote_valid for r in rows if r.quote_valid is not None]
    return {
        "n": len(rows),
        "raw_category": raw_category_metrics(rows),
        "accepted_decisions": accepted_decision_metrics(rows),
        "abstention_or_review_rate": _ratio(sum(1 for r in rows if r.abstained), len(rows)),
        "no_valid_prediction": sum(1 for r in rows if r.predicted is None),
        "evidence_quote_validity": _ratio(sum(quote_checks), len(quote_checks)),
        "quote_checks": len(quote_checks),
    }
