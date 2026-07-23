"""Run the extraction eval suite.

Two modes:

1. Live mode — re-run the workflow's extraction prompt against each fixture's
   parsed markdown using the OpenAI API, then score the result.
       python evals/run_eval.py --live
   Requires OPENAI_API_KEY. Uses the same model and schema as the workflow's
   "Apply Data Extraction Rules" node, so a prompt change here is a fair
   proxy for a prompt change there.

2. Offline mode — score extractions already produced by a live n8n run.
       python evals/run_eval.py --results extractions.jsonl
   extractions.jsonl format: one {"id": "...", "extracted": {...}} per line.

Outputs scores.json and report.md next to this file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from score import score_case

EVAL_DIR = Path(__file__).parent
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MODEL = os.environ.get("EXTRACTION_MODEL", "gpt-4o-mini")

EXTRACTION_PROMPT = """Extract the invoice fields from the document below.

Return ONLY a JSON object with these keys:
invoice_date, invoice_number, po_number, supplier_name, supplier_address,
customer_name, customer_address, supplier_vat_number, customer_vat_number,
shipping_address, line_items, subtotal, vat_amount, total_price

line_items is an array of {{description, quantity, unit_price, amount}}.
Use null for any field the document does not contain. Never guess a value.

Document:
{document}
"""


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def extract_live(document: str, api_key: str) -> dict:
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": EXTRACTION_PROMPT.format(document=document)}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        OPENAI_URL,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.load(resp)
    return json.loads(payload["choices"][0]["message"]["content"])


def collect(cases: list[dict], args: argparse.Namespace) -> dict[str, dict]:
    if args.results:
        return {row["id"]: row["extracted"] for row in load_jsonl(Path(args.results))}

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY is not set; live mode needs it.")

    extractions = {}
    for case in cases:
        print(f"  extracting {case['id']} ...", file=sys.stderr)
        try:
            extractions[case["id"]] = extract_live(case["document"], api_key)
        except Exception as exc:  # noqa: BLE001 - a failed call is a failed extraction
            print(f"    failed: {exc}", file=sys.stderr)
            extractions[case["id"]] = {}
    return extractions


def summarize(results: list[dict]) -> dict:
    accuracies = [r["score"]["field_accuracy"] for r in results if r["score"]["field_accuracy"] is not None]
    arithmetic = [r["score"]["arithmetic_consistent"] for r in results if r["score"]["arithmetic_consistent"] is not None]
    counts = [r["score"]["line_item_count_correct"] for r in results if r["score"]["line_item_count_correct"] is not None]
    hallucinated = sum(len(r["score"]["hallucinated_fields"]) for r in results)
    omitted = sum(len(r["score"]["omitted_fields"]) for r in results)

    return {
        "total_cases": len(results),
        "field_accuracy": round(sum(accuracies) / len(accuracies), 3) if accuracies else None,
        "arithmetic_consistency": round(sum(arithmetic) / len(arithmetic), 3) if arithmetic else None,
        "line_item_count_accuracy": round(sum(counts) / len(counts), 3) if counts else None,
        "hallucinated_field_count": hallucinated,
        "omitted_field_count": omitted,
    }


def write_report(results: list[dict], summary: dict) -> None:
    def pct(value: float | None) -> str:
        return "—" if value is None else f"{value:.0%}"

    lines = [
        "# Invoice Extraction Eval Report",
        "",
        f"- **Cases:** {summary['total_cases']}",
        f"- **Field accuracy:** {pct(summary['field_accuracy'])}",
        f"- **Arithmetic consistency:** {pct(summary['arithmetic_consistency'])}",
        f"- **Line-item count accuracy:** {pct(summary['line_item_count_accuracy'])}",
        f"- **Hallucinated fields:** {summary['hallucinated_field_count']}",
        f"- **Omitted fields:** {summary['omitted_field_count']}",
        "",
        "| Case | Type | Field accuracy | Arithmetic | Hallucinated | Omitted |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        s = r["score"]
        arithmetic = "—" if s["arithmetic_consistent"] is None else ("✅" if s["arithmetic_consistent"] else "❌")
        lines.append(
            f"| {r['id']} | {r['type']} | {pct(s['field_accuracy'])} | {arithmetic} "
            f"| {', '.join(s['hallucinated_fields']) or '—'} "
            f"| {', '.join(s['omitted_fields']) or '—'} |"
        )
    (EVAL_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", action="store_true", help="re-run extraction via the OpenAI API")
    group.add_argument("--results", help="score pre-collected extractions (JSONL)")
    args = parser.parse_args()

    cases = load_jsonl(EVAL_DIR / "test_cases.jsonl")
    extractions = collect(cases, args)

    results = []
    for case in cases:
        extracted = extractions.get(case["id"])
        if extracted is None:
            print(f"  skipping {case['id']}: no extraction provided", file=sys.stderr)
            continue
        results.append({
            "id": case["id"],
            "type": case["type"],
            "extracted": extracted,
            "score": score_case(case, extracted),
        })

    summary = summarize(results)
    (EVAL_DIR / "scores.json").write_text(
        json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8"
    )
    write_report(results, summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
