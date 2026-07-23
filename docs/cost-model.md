# Cost model

What this pipeline costs to run, and the volume at which it stops being worth
running. The formula is the durable part; the unit prices are a snapshot and
should be re-checked against current vendor pricing before you quote anyone.

## Cost per invoice

```
cost_per_invoice
  = llamaparse_pages × page_rate
  + (extraction_input_tokens + extraction_output_tokens) × token_rate
  + n8n_executions × execution_rate
```

Three executions run per invoice on average: the trigger poll batch, the parse
status loop, and the extraction chain. The status loop is the variable one — a
slow LlamaParse job costs more polls.

### Worked example

Assumptions, all stated so you can replace them:

| Input | Value | Source |
|---|---|---|
| Invoices/month | 500 | Volume assumption |
| Pages per invoice | 1.4 | Mixed single and multi-page |
| Parsed markdown size | ~1,800 tokens | Measured from the eval fixtures |
| Extraction output | ~400 tokens | Structured JSON, 14 fields plus line items |
| LlamaParse | Free tier covers 1,000 pages/day | Vendor free tier |
| Extraction model | Small-tier model (e.g. `gpt-4o-mini` class) | Workflow's OpenAI node |
| n8n | Self-hosted | Marginal execution cost ≈ 0 |

At 500 invoices/month and 1.4 pages each, that is 700 pages/month — comfortably
inside the LlamaParse free tier, so parsing is **$0**.

Token cost dominates:

```
500 invoices × (1,800 input + 400 output) tokens = 1.1M tokens/month
```

At small-tier pricing that lands in the **low single-digit dollars per month**.
Re-run the arithmetic with current per-million-token rates for a real number.

**The honest conclusion: at this volume the pipeline is effectively free to run.**
Infrastructure is not the constraint. Which is the whole point of the next section.

## Where the money actually is

The pipeline's cost is negligible. The value is entirely in displaced labour, so
that is what the model should measure:

```
monthly_saving = invoices × minutes_saved_per_invoice / 60 × loaded_hourly_rate
payback_period = build_cost / (monthly_saving − monthly_running_cost)
```

For the logistics deployment behind this build: month-end close went from **3 days
to near zero**. At 500 invoices/month and roughly 3 minutes of manual re-typing
and reconciliation each, that is ~25 hours/month recovered.

Running cost stays in single-digit dollars. The build is a one-off. Payback is
measured in **weeks, not quarters**, and the sensitivity that matters is invoice
volume and hourly rate — not token price.

## When this stops being worth it

Be able to say when the answer is no:

| Condition | Why it breaks |
|---|---|
| **Under ~50 invoices/month** | Build and maintenance time exceeds the labour saved. Use a shared inbox and a checklist. |
| **Every invoice needs human review anyway** | If a regulated process requires sign-off on all 500, you have automated the typing but not the approval. Saving shrinks to keystrokes. |
| **Above ~5,000 pages/month** | LlamaParse leaves the free tier and page cost becomes the dominant term. Re-derive from the formula rather than assuming the numbers above still hold. |
| **Highly variable invoice formats** | Extraction accuracy drops, review rate climbs, and the labour comes back. Run [the eval suite](../evals/) against a real sample of your invoices before committing. |

## Scaling the cost

| Volume/month | Pages | LlamaParse | Tokens | Dominant cost |
|---|---|---|---|---|
| 100 | 140 | Free tier | 0.2M | Nothing meaningful |
| 500 | 700 | Free tier | 1.1M | Tokens (still trivial) |
| 2,000 | 2,800 | Free tier | 4.4M | Tokens |
| 10,000 | 14,000 | **Paid** | 22M | Page parsing |

The break in the model is at the free-tier boundary, not at any token threshold.
Anyone quoting this pipeline at enterprise volume should price parsing first.

## Measuring it in production

Log per-execution token counts from the OpenAI node and page counts from the
LlamaParse response to a metrics sheet. Two things then become visible that a
spreadsheet estimate cannot show you: cost drift after a prompt change, and the
review rate — the percentage of invoices a human still has to touch, which is
the number that actually determines the saving.
