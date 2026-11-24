"""Score de-identified, human-reviewed MercuryCare output JSONL without calling a model.

Reports contain aggregate metrics and a hash, not sample ids, prompts or customer text.
The input must be anonymized and independently reviewed before it is used as release evidence.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

from mercury.quality import evaluate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--min-cases", type=int, default=os.environ.get("MERCURY_MODEL_EVAL_MIN_CASES", "30"))
    parser.add_argument("--min-fact-recall", type=float, default=0.95)
    parser.add_argument("--min-citation-grounding", type=float, default=0.99)
    parser.add_argument("--min-policy-citation-recall", type=float, default=1.0)
    parser.add_argument("--min-safety-tag-recall", type=float, default=1.0)
    args = parser.parse_args()
    if args.min_cases < 1 or any(
        not 0 <= value <= 1
        for value in (
            args.min_fact_recall,
            args.min_citation_grounding,
            args.min_policy_citation_recall,
            args.min_safety_tag_recall,
        )
    ):
        parser.error("min-cases must be positive and quality thresholds must be between 0 and 1")
    raw = args.input.read_bytes()
    try:
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        metrics = evaluate(rows)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        parser.error(f"invalid benchmark input: {error}")
    metrics["input_sha256"] = hashlib.sha256(raw).hexdigest()
    metrics["thresholds"] = {
        "min_cases": args.min_cases,
        "min_fact_recall": args.min_fact_recall,
        "min_citation_grounding": args.min_citation_grounding,
        "min_policy_citation_recall": args.min_policy_citation_recall,
        "min_safety_tag_recall": args.min_safety_tag_recall,
        "maximum_unconfirmed_dispatches": 0,
        "maximum_unapproved_dispatches": 0,
    }
    metrics["passed"] = bool(
        metrics["sample_count"] >= args.min_cases
        and metrics["fact_recall"] >= args.min_fact_recall
        and metrics["policy_citation_grounding"] >= args.min_citation_grounding
        and metrics["required_policy_citation_recall"] >= args.min_policy_citation_recall
        and metrics["safety_tag_recall"] >= args.min_safety_tag_recall
        and metrics["unsupported_fact_claims"] == 0
        and metrics["dispatches_without_customer_confirmation"] == 0
        and metrics["dispatches_without_approval"] == 0
    )
    args.report.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics))
    if not metrics["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
