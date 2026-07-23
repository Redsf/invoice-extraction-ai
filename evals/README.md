# Extraction evals

12 test cases covering the ways invoice extraction actually fails in production —
not the happy path.

Extraction has a known ground truth, so scoring is **fully deterministic**: no
LLM judge, no API cost, no flaky verdicts. A run is free and repeatable.

## What is measured

| Metric | Why it matters |
|---|---|
| **Field accuracy** | Per-field match against the expected value, normalized for punctuation and currency formatting. |
| **Hallucination count** | Fields populated when the document does not contain them. A confidently invented PO number is worse than a blank one — it reconciles against nothing and nobody notices. |
| **Omission count** | Fields left blank when the document does contain them. |
| **Line-item count accuracy** | Silent truncation of a multi-line invoice is the failure that quietly under-reports spend. |
| **Arithmetic consistency** | Do line items plus VAT equal the stated total? This needs no ground truth, so it works as a **live production guard**, not just an offline eval. |

## The cases

| Type | Cases | What it catches |
|---|---|---|
| `clean` | 1 | Baseline. |
| `multi_line` | 1 | Line-item truncation. |
| `missing_field` | 1 | Inventing a PO number that isn't there. |
| `eu_number_format` | 1 | `1.230,00` read as `1.23`. Silent 1000× corruption. |
| `zero_vat` | 1 | Reverse-charge VAT of `0.00` read as null. |
| `credit_note` | 1 | Negative amounts booked as positive payables. |
| `ambiguous_date` | 1 | `05/04/2025` in a UK document is 5 April, not 4 May. |
| `shipping_address` | 1 | Ship-to collapsed into bill-to. |
| `adversarial_injection` | 1 | Prompt injection embedded in a supplier PDF. An inbox-triggered extractor processes attachments from anyone who can email you. |
| `adversarial_lookalike` | 1 | A quotation extracted as a payable invoice. |
| `degraded_ocr` | 1 | Poor scan quality — partial extraction is fine, confident wrong values are not. |
| `not_an_invoice` | 1 | A delivery note reaching the inbox. |

## Running it

Live mode re-runs the extraction prompt against each fixture through the OpenAI
API, using the same model and schema as the workflow's **Apply Data Extraction
Rules** node:

```bash
OPENAI_API_KEY=sk-... python evals/run_eval.py --live
```

Offline mode grades extractions already produced by a real n8n run — export the
**Map Output** node's results as JSONL (`{"id": "...", "extracted": {...}}` per
line):

```bash
python evals/run_eval.py --results extractions.jsonl
```

Both write `scores.json` and `report.md` to this directory. Run before and after
any prompt, model, or parser change, and compare the two reports.

## Taking the arithmetic check to production

`check_arithmetic()` in [score.py](score.py) needs no ground truth. Port it into
a Code node between **Map Output** and **Append to Reconciliation Sheet** and
route inconsistent invoices to a review queue instead of straight to the sheet.
That converts an offline eval into a live guardrail on the same logic.
