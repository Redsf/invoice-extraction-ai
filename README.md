# Invoice Data Extraction (LlamaParse)

[![validate](https://github.com/Redsf/invoice-extraction-ai/actions/workflows/validate.yml/badge.svg)](https://github.com/Redsf/invoice-extraction-ai/actions/workflows/validate.yml)

> Standalone reference build, pulled out of my [81-workflow n8n portfolio](https://github.com/Redsf/n8n-workflows). Credential-free — no secrets, API keys, or client data included.
>
> Full write-up (strategy, architecture, results) in the case study: [Redsf/invoice-automation-logistics →](https://github.com/Redsf/Redsf/blob/main/case-studies/invoice-automation-logistics.md)

An email-triggered pipeline that watches an inbox for incoming invoice PDFs, converts them to structured markdown with LlamaParse (which preserves tables and line items that plain PDF-to-text extractors lose), pulls out the key invoice fields with an LLM, and appends the result to a reconciliation spreadsheet. A Gmail label prevents the same invoice from being processed twice.

Built for finance and ops teams that receive recurring invoices by email and want them landed in a spreadsheet (or downstream accounting system) without manual re-typing.

## Production results

From the production deployment of this architecture (logistics company, finance team):

| Metric | Result |
|---|---|
| Month-end close | **3 days → near zero** |
| Manual invoice re-typing | **Eliminated** |
| Duplicate processing | **0** (Gmail label guard) |

Full breakdown: [case study →](https://github.com/Redsf/Redsf/blob/main/case-studies/invoice-automation-logistics.md)

## What it does

1. **Receiving Invoices** (Gmail Trigger, polling every minute) watches for messages with an attachment from a specific sender.
2. **Split Out Labels** → **Get Labels Names** → **Combine Label Names** reads the message's current Gmail label IDs and resolves them to human-readable label names.
3. **Email with Label Names** (merge, multiplex) recombines the original email with its resolved label list.
4. **Should Process Email?** (IF node) only lets the item through when the attachment's MIME type is `application/pdf` and the resolved labels do **not** already contain `"invoice synced"` — this is the duplicate-processing guard.
5. **Upload to LlamaParse** sends the PDF binary to LlamaCloud's parsing API (`POST /api/parsing/upload`).
6. **Get Processing Status** polls the job; **Is Job Ready?** (switch on `status`) branches to `SUCCESS`, `ERROR`, `CANCELED`, or loops back through **Wait to stay within service limits** while the job is still pending.
7. **Get Parsed Invoice Data** fetches the finished job's result as markdown.
8. **Apply Data Extraction Rules** (an LLM chain via **OpenAI Model**) reads the markdown and extracts invoice date, invoice number, PO number, supplier/customer name and address, VAT numbers, shipping addresses, line items, subtotals, and total price, constrained by the **Structured Output Parser**'s JSON schema.
9. **Map Output** flattens the parser's output object into row-level fields.
10. **Append to Reconciliation Sheet** writes the row to Google Sheets.
11. **Add "invoice synced" Label** tags the original Gmail message so it's skipped on future polls.

## Sample input

There's no webhook here — the trigger is Gmail polling. To test the flow end to end, send yourself (or the configured sender inbox) an email containing a PDF attachment, from the sender address configured in the trigger. The workflow currently ships with:

```
sender: invoices@paypal.com
filter: has:attachment
```

Any email that doesn't match both the sender and the attachment filter is ignored by the trigger itself, before it ever reaches **Should Process Email?**.

## Setup (~20 minutes)

1. **Gmail** — add OAuth2 credentials to **Receiving Invoices**, **Get Labels Names**, and **Add "invoice synced" Label**.
2. **Create the Gmail label first** — the workflow expects a label literally named `invoice synced` to exist in the mailbox, and **Add "invoice synced" Label** references it by ID. Create your own label and swap its ID in for the `REPLACE_WITH_LABEL_ID` placeholder (or reconfigure the node to look it up by name).
3. **LlamaCloud (LlamaParse)** — add an HTTP Header Auth credential (bearer token) to **Upload to LlamaParse**, **Get Processing Status**, and **Get Parsed Invoice Data**. Free tier covers 1,000 PDFs/day.
4. **OpenAI** — add your API key to **OpenAI Model** (used by **Apply Data Extraction Rules**).
5. **Google Sheets** — add OAuth2 credentials to **Append to Reconciliation Sheet**, and point it at your own spreadsheet by replacing the `REPLACE_WITH_SPREADSHEET_ID` placeholder — the column schema is pre-mapped to the extraction fields (invoice date, invoice number, PO number, supplier/customer details, VAT numbers, line items, subtotals, total), so keep the header row consistent if you swap sheets.
6. **Adjust the sender filter** — **Receiving Invoices** is hardcoded to only accept mail from `invoices@paypal.com`. Change this to match whichever vendor(s) actually email you invoices, or broaden the query to catch multiple senders.
7. **Watch the LlamaParse rate limit** — **Wait to stay within service limits** adds a delay between status polls; shrink or extend it depending on your plan's rate limits and typical invoice volume.

## Evals

This repo ships an **eval harness** ([evals/](evals/)) — 12 test cases covering
the ways invoice extraction actually fails: European `1.230,00` number formats
read as `1.23`, reverse-charge VAT of `0.00` read as null, credit notes booked
as positive payables, `05/04/2025` resolved to the wrong month, quotations
extracted as payable invoices, and prompt injection embedded in a supplier PDF.

Extraction has a known ground truth, so scoring is **fully deterministic** — no
LLM judge, no API cost, no flaky verdicts:

```bash
python evals/run_eval.py --results extractions.jsonl
```

It measures field accuracy, hallucinated fields (populated when the document
doesn't contain them), omitted fields, line-item count accuracy, and arithmetic
consistency.

That last check — do the line items plus VAT equal the stated total? — needs no
ground truth at all, which makes it usable as a **live production guard**. Port
`check_arithmetic()` into a Code node before **Append to Reconciliation Sheet**
and route mismatches to review.

## Cost model

Per-invoice cost, the volume at which LlamaParse leaves the free tier, and the
conditions under which this pipeline is not worth building:
[docs/cost-model.md](docs/cost-model.md).

Short version: at 500 invoices/month this runs for single-digit dollars. The
value is entirely in displaced labour, so that is what the model measures.

## Error handling

Gmail and LlamaParse calls retry on failure, and the status loop handles
`ERROR` and `CANCELED` job states explicitly. There is **no Error Trigger** on
this workflow — a workflow-level failure currently passes unnoticed. CI reports
this on every push (see the `validate` badge above); adding an Error Trigger
plus a Slack alert is the first thing to do before running it in production.

---

<!-- ARCHITECTURE:START -->
## Architecture

```mermaid
flowchart TD
    N0["OpenAI Model<br/><small>lmOpenAi</small>"]
    N1["Structured Output Parser<br/><small>outputParserStructured</small>"]
    N2["Upload to LlamaParse<br/><small>httpRequest</small>"]
    N3["Receiving Invoices<br/><small>gmailTrigger</small>"]
    N4["Append to Reconciliation Sheet<br/><small>googleSheets</small>"]
    N5["Get Processing Status<br/><small>httpRequest</small>"]
    N6["Wait to stay within service limits<br/><small>wait</small>"]
    N7["Is Job Ready?<br/><small>switch</small>"]
    N8["Add 'invoice synced' Label<br/><small>gmail</small>"]
    N9["Get Parsed Invoice Data<br/><small>httpRequest</small>"]
    N10["Map Output<br/><small>set</small>"]
    N11["Apply Data Extraction Rules<br/><small>chainLlm</small>"]
    N12["Should Process Email?<br/><small>if</small>"]
    N13["Split Out Labels<br/><small>splitOut</small>"]
    N14["Get Labels Names<br/><small>gmail</small>"]
    N15["Combine Label Names<br/><small>aggregate</small>"]
    N16["Email with Label Names<br/><small>merge</small>"]
    N10 --> N4
    N0 -.languageModel.-> N11
    N7 -->|out0| N9
    N7 -->|out3| N6
    N14 --> N15
    N13 --> N14
    N3 --> N13
    N3 --> N16
    N15 --> N16
    N2 --> N5
    N5 --> N7
    N12 --> N2
    N16 --> N12
    N9 --> N11
    N1 -.outputParser.-> N11
    N11 --> N10
    N4 --> N8
    N6 --> N5
```
<!-- ARCHITECTURE:END -->
