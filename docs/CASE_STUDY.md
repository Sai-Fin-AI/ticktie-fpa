# Case study: TickTie, AI commentary a CFO can rely on

## The problem

Every month-end, FP&A teams compare actuals with budget and forecast, decide which movements matter, chase owners for explanations and write commentary for the CFO, all under deadline pressure. Generative AI looks like an obvious way to speed up the writing. In practice it adds three risks finance leaders worry about:

1. **Numbers that drift.** A model restates a figure wrongly, rounds it differently or adds two numbers that should not be added.
2. **Invented causes.** The model writes a plausible reason ("driven by higher freight rates") that nobody gave.
3. **No trail.** Nobody can say which figures and which explanations the commentary was built from.

This project asks: *what would AI-assisted variance commentary need to look like before a finance controller would sign it off?*

## The approach: the AI is a writer, never a calculator

```
Upload ─► Data checks ─► Variance engine ─► Owners explain ─► LOCKED PACK ─┬─► Commentary agent ─┐
          (rules)        (exact decimals)   (humans)         (fingerprint) └─► Deck agent ────────┴─► Tick-and-tie checker ─► CFO
```

- **Figures come only from code.** Variances, materiality, subtotals, margins, the bridge, gross vs net and the full-year view are calculated in Python's `Decimal`, never by the model. The AI receives ready-made phrases ("Net profit INR 2,270,000 vs budget INR 2,465,000") and is told to copy them.
- **Causes come only from people.** Each material line gets a reviewer note: reason, reference, amount explained, status (confirmed, unconfirmed, conflicting) and timing (one-off, recurring, timing difference). Only *confirmed* amounts reduce the unexplained balance. The model rewords notes; it never adds to them.
- **One locked source of truth.** The reviewer locks the pack, which freezes figures and notes under a SHA-256 fingerprint. Excel, Markdown, the commentary and the deck all read that pack. If anything changes afterwards, exports are withheld until the pack is locked again.
- **Code checks the AI's work, the way a reviewer would tick and tie.** Every number in AI text must exist in the pack, in any format (lakh, crore, million). Each number must belong to the line named just before it. Favorable and unfavorable must match the sign. Unconfirmed notes must be called unconfirmed, and a partial explanation must not hide the unexplained gap. A line listed as "favorable" or "unexplained" must really be so. Failing text gets one retry; whatever still fails is replaced with engine-written wording, and the reviewer sees what the AI got wrong.

## Finance design decisions

| Decision | Why |
| --- | --- |
| Materiality needs **both** an amount and a percentage | INR 60,000 on a 10m line is noise; 40% on a 5,000 line is noise. A zero budget uses the amount alone. |
| **Spread variance** rule | A material account total with no material line underneath (India Employee costs +100k, no cost centre over 10%) would otherwise go unexplained. Every moving line is brought into scope so each owner explains their share. |
| **Gross vs net** next to every total | Net figures hide offsets. India sales are +300k net, but the brands move 1,020k gross (a 360k delisting offset by growth elsewhere). |
| Sign logic by P&L line | Sales above plan is favorable, costs above plan unfavorable, and every line shows its *profit impact*. |
| **Negative balances** always explained, never "Recurring" | A cost line that nets below zero is usually a reversal, a reclass or a misposting. It must be looked at whatever its size, and must never be rolled forward into the full-year view. |
| Full-year view projects **confirmed** items only | Recurring items run to the year end, timing differences reverse and one-offs happen once. Unexplained variance is shown but never projected. |
| Favorable spend is not "savings" | An underspend may only be a timing difference; the commentary says so. |
| Cost centres and profit centres are one level | Both are responsibility centres with an owner, so sales lines get owners, heatmap rows and query lists exactly like costs. |
| Stop rather than guess | An unmapped GL code or SKU, a SKU in two brands, a cost centre with two owners or a code Excel has corrupted stops the file with a plain-language reason, and where possible offers the corrected master file back. |

## Real-world data

ERP exports don't arrive tidy, so the input layer is rule-based:

- Any column names and order, with dropdowns pre-filled from common ERP headers ("Posting Period", "G/L Account", "Profit Center", "Material").
- One combined file, or separate actuals, budget and forecast files matched line by line, with a reconciliation of control totals per file.
- GL codes roll up through a mapping file; SKUs roll up through a product master to brand and category; costs post to cost centres, sales to profit centres and SKUs.
- The same code spelled differently across files (510100, 0510100, "510 100", 510100.0) is offered as a match that the reviewer must accept. Nothing is merged silently, and every decision is logged in the pack.

## How it was tested

- **166 automated tests** covering the engine, data loading, roll-ups, the review pack, the checker, both agents and the Streamlit interface. They run on every push (GitHub Actions, Windows and Ubuntu).
- **Synthetic stress testing.** Hundreds of randomly generated datasets (different countries, centres, SKU or brand-only sales, zero and negative amounts, paise, very large values, awkward names) were pushed through the whole pipeline. Every run checked that totals reconcile at every level, that every bridge walks from start to end and that every pack locks and reopens. This found nine defects, all fixed and now covered by tests. Examples: a P&L without operating expenses crashed the EBITDA tile; Excel's "510100.0" silently split a line in two; long names pushed slide tables off the page.
- **Reading the AI output as a CFO would.** That review found summary bullets that used real figures in the wrong list ("favorable variances include Consulting, INR 75,000 above budget"). It led to the list-claim rule in the checker.

## Constraints I chose

- **Free to run.** The AI runs on a local open-weight model (Ollama, `qwen3:8b` on an 8 GB laptop GPU) or on free-tier services. The public demo replays recorded answers, so it costs nothing and never shares a key.
- **Synthetic data only.** No employer data or internal methodology is used anywhere.
- **No agent framework.** Two focused agents with plain calls are easier to test and to explain than a framework.

## Limits, stated plainly

One month per file and INR only (no FX). The checker verifies figures, direction, status and list claims, not meaning, so a loosely paraphrased explanation can still pass and commentary must be read before use. "Confirmed" is the reviewer's assertion; references are not fetched. No production use or time savings are claimed.

## What I would build next

- **Price / volume / mix** for sales, from a units column.
- **Year-to-date and trend** views across months.
- A **driver taxonomy** for notes (price, volume, timing, one-off, reclass) and a variance-by-driver slide.
- An **evaluation set** of tricky cases, scored for invented figures and causes, with published results.
