"""Compare the keyword baseline with the live model on the labelled synthetic set.

Usage:
    python scripts/evaluate.py                # baseline; live run only if a key is configured
    python scripts/evaluate.py --baseline-only
    python scripts/evaluate.py --rescore reports/evaluation/latest.json   # recompute from stored predictions

Writes reports/evaluation/latest.json and latest.md. Without GEMINI_API_KEY the
live model section is recorded as "not_run"; no model metrics are invented.
Every live run stores its per-example predictions, so metrics can be
recomputed later without calling the model again. Two metric families are
reported: raw category metrics and automatically accepted decisions (see
shopdetective/evaluation.py and docs/EVALUATION.md).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shopdetective.config import PROMPT_VERSION, SCHEMA_VERSION, get_secret, load_settings  # noqa: E402
from shopdetective.evaluation import (  # noqa: E402
    METRIC_DEFINITIONS,
    Scored,
    keyword_classify,
    metrics,
)
from shopdetective.providers import GeminiProvider, ProviderError  # noqa: E402
from shopdetective.schemas import COMPLAINT_CATEGORIES, PredictionSource  # noqa: E402
from shopdetective.validation import quote_is_supported, validate_classification_response  # noqa: E402

DATA = ROOT / "data" / "evaluation" / "labelled_feedback.csv"
OUT = ROOT / "reports" / "evaluation"
COMPLAINTS = {c.value for c in COMPLAINT_CATEGORIES}
ALLOWED_LABEL_STATUSES = ("provisional_synthetic", "human_verified")


def load_examples() -> list[dict]:
    with DATA.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    bad = sorted({r["label_status"] for r in rows} - set(ALLOWED_LABEL_STATUSES))
    if bad:
        raise SystemExit(f"Unknown label_status value(s) {bad}; allowed: {', '.join(ALLOWED_LABEL_STATUSES)}")
    return rows


def label_status_counts(examples: list[dict]) -> dict[str, int]:
    counts = dict.fromkeys(ALLOWED_LABEL_STATUSES, 0)
    for e in examples:
        counts[e["label_status"]] += 1
    return counts


def scored_to_dict(r: Scored) -> dict:
    return {
        "example_id": r.example_id,
        "expected": r.expected,
        "predicted": r.predicted,
        "abstained": r.abstained,
        "quote_valid": r.quote_valid,
        "auto_accepted": r.auto_accepted,
    }


def scored_from_dict(d: dict) -> Scored:
    return Scored(d["example_id"], d["expected"], d["predicted"], d["abstained"], d["quote_valid"])


def run_baseline(examples: list[dict]) -> list[Scored]:
    rows = []
    for e in examples:
        p = keyword_classify(e["example_id"], e["text"])
        qv = quote_is_supported(p.evidence_quote, e["text"]) if p.category in COMPLAINTS else None
        rows.append(Scored(e["example_id"], e["expected_category"], p.category, p.abstained, qv))
    return rows


def run_live(examples: list[dict], provider: GeminiProvider, batch_size: int) -> tuple[list[Scored], dict]:
    texts = {e["example_id"]: e["text"] for e in examples}
    preds = {}
    errors: dict[str, int] = {}
    for start in range(0, len(examples), batch_size):
        batch = examples[start : start + batch_size]
        requested = {e["example_id"]: e["text"] for e in batch}
        for _attempt in range(3):
            if not requested:
                break
            try:
                raw = provider.classify_batch([{"message_id": k, "text": v} for k, v in requested.items()])
            except ProviderError as err:
                errors[err.code] = errors.get(err.code, 0) + 1
                if not err.retryable:
                    raise
                continue
            res = validate_classification_response(
                raw, requested, PredictionSource.LIVE_GEMINI, provider.model_id, PROMPT_VERSION, SCHEMA_VERSION
            )
            preds.update(res.accepted)
            requested = {k: texts[k] for k in res.retry_ids}
    rows = []
    for e in examples:
        p = preds.get(e["example_id"])
        if p is None:
            rows.append(Scored(e["example_id"], e["expected_category"], None, False, None))
            continue
        qv = p.quote_valid if p.category.value in COMPLAINTS else None
        abstained = p.needs_review or p.category.value == "unclear"
        rows.append(Scored(e["example_id"], e["expected_category"], p.category.value, abstained, qv))
    return rows, errors


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:.3f}"


def _per_category_table(per: dict) -> list[str]:
    lines = ["| Category | Support | Predicted | Precision | Recall | F1 |", "|---|---:|---:|---:|---:|---:|"]
    for cat, v in per.items():
        lines.append(
            f"| {cat} | {v['support']} | {v['predicted']} | {_fmt(v['precision'])} "
            f"| {_fmt(v['recall'])} | {_fmt(v['f1'])} |"
        )
    return lines


def to_markdown(report: dict) -> str:
    counts = report["label_status_counts"]
    lines = [
        "# Evaluation results (generated)",
        "",
        f"- Generated: {report['generated_at_utc']}",
        f"- Dataset: `{report['dataset']}` ({report['n_examples']} examples)",
        "- Label status: " + ", ".join(f"{k} {v}" for k, v in counts.items()),
        "- Synthetic examples only; this is not evidence of performance on real businesses.",
        "",
        "**Raw category metrics** score the category each system returned, even when the app would send "
        "that prediction to human review. **Accepted-decision metrics** score only what the app would accept "
        "automatically; reviewed, quote-rejected and failed rows stay in the denominator as not accepted.",
        "",
    ]
    for name in ("keyword_baseline", "live_model"):
        sec = report[name]
        lines.append(f"## {name.replace('_', ' ').title()}")
        lines.append("")
        if sec.get("status") != "completed":
            lines.append(f"Status: **{sec.get('status')}**: {sec.get('reason', '')}")
            lines.append("")
            continue
        m = sec["metrics"]
        raw, acc = m["raw_category"], m["accepted_decisions"]
        if "model" in sec:
            lines.append(
                f"Model: `{sec['model']}`, prompt `{sec['prompt_version']}`, schema `{sec['schema_version']}`, "
                f"provider errors {sec['provider_errors'] or 0}"
            )
            lines.append("")
        lines += [
            f"- Analysed sample size: {m['n']}",
            f"- Classifier review/abstention flags: {_fmt(m['abstention_or_review_rate'])}",
            f"- No valid prediction: {m['no_valid_prediction']}",
            f"- Evidence-quote validity: {_fmt(m['evidence_quote_validity'])} "
            f"over {m['quote_checks']} complaint predictions",
            "",
            "### Raw category metrics (returned category, before review routing)",
            "",
            f"- Raw category accuracy: {_fmt(raw['accuracy'])}",
            f"- Raw macro F1 (categories with support): {_fmt(raw['macro_f1'])}",
            "",
            *_per_category_table(raw["per_category"]),
            "",
            "### Automatically accepted decisions",
            "",
            f"- Auto-accepted: {acc['auto_accepted']}/{acc['n']} (coverage {_fmt(acc['auto_acceptance_coverage'])})",
            f"- Routed to human review or failed: {acc['routed_to_review']}/{acc['n']} "
            f"(rate {_fmt(acc['review_or_failure_rate'])}; flagged/unclear {acc['flagged_or_unclear']}, "
            f"quote rejected {acc['quote_rejected']}, no valid prediction {acc['no_valid_prediction']})",
            f"- Precision of auto-accepted decisions: {_fmt(acc['accepted_precision'])} "
            f"({acc['incorrect_auto_accepted']} incorrect)",
            f"- Correct auto-accepted decisions / all examples: {_fmt(acc['correct_auto_accepted_rate'])}",
            f"- Macro F1 over auto-acceptable categories: {_fmt(acc['macro_f1_auto'])}",
            "- Expected `unclear` routed to review: "
            f"{acc['expected_unclear_routed_to_review']}/{acc['expected_unclear']}",
            "",
            *_per_category_table(acc["per_category"]),
            "",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline-only", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT, help="output folder (default reports/evaluation)")
    ap.add_argument(
        "--rescore",
        type=Path,
        help="recompute live metrics from the predictions stored in an earlier report (no model call)",
    )
    args = ap.parse_args()
    out_dir = args.out
    examples = load_examples()
    statuses = sorted({e["label_status"] for e in examples})
    baseline_rows = run_baseline(examples)
    report = {
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset": str(DATA.relative_to(ROOT)),
        "n_examples": len(examples),
        "label_status": ", ".join(statuses),
        "label_status_counts": label_status_counts(examples),
        "metric_definitions": METRIC_DEFINITIONS,
        "keyword_baseline": {
            "status": "completed",
            "metrics": metrics(baseline_rows),
            "predictions": [scored_to_dict(r) for r in baseline_rows],
        },
    }
    settings = load_settings()
    key = get_secret("GEMINI_API_KEY")
    if args.rescore:
        old = json.loads(args.rescore.read_text(encoding="utf-8"))["live_model"]
        if old.get("status") != "completed" or "predictions" not in old:
            raise SystemExit(f"{args.rescore} has no stored live predictions to rescore.")
        expected = {e["example_id"]: e["expected_category"] for e in examples}
        rows = [scored_from_dict({**d, "expected": expected[d["example_id"]]}) for d in old["predictions"]]
        report["live_model"] = {
            **{k: v for k, v in old.items() if k not in ("metrics", "predictions")},
            "rescored_from": str(args.rescore),
            "metrics": metrics(rows),
            "predictions": [scored_to_dict(r) for r in rows],
        }
    elif args.baseline_only:
        report["live_model"] = {"status": "not_run", "reason": "--baseline-only was given."}
    elif not key:
        report["live_model"] = {"status": "not_run", "reason": "GEMINI_API_KEY is not configured."}
    else:
        provider = GeminiProvider(key, settings.provider.model, settings.provider.timeout_seconds)
        try:
            rows, errors = run_live(examples, provider, settings.provider.batch_size)
            report["live_model"] = {
                "status": "completed",
                "run_at_utc": report["generated_at_utc"],
                "model": settings.provider.model,
                "prompt_version": PROMPT_VERSION,
                "schema_version": SCHEMA_VERSION,
                "provider_errors": errors,
                "metrics": metrics(rows),
                "predictions": [scored_to_dict(r) for r in rows],
            }
        except ProviderError as err:
            report["live_model"] = {"status": "failed", "reason": f"{err.code}: {err.message}"}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out_dir / "latest.md").write_text(to_markdown(report), encoding="utf-8")
    for name in ("keyword_baseline", "live_model"):
        sec = report[name]
        if sec.get("status") != "completed":
            print(f"{name}: {sec['status']}")
            continue
        raw, acc = sec["metrics"]["raw_category"], sec["metrics"]["accepted_decisions"]
        print(
            f"{name}: n={raw['n']} raw_accuracy={_fmt(raw['accuracy'])} raw_macro_f1={_fmt(raw['macro_f1'])} "
            f"auto_coverage={_fmt(acc['auto_acceptance_coverage'])} "
            f"auto_precision={_fmt(acc['accepted_precision'])}"
        )
    print(f"wrote {out_dir / 'latest.json'} and latest.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
