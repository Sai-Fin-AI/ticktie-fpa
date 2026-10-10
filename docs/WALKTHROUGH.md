# Walkthrough

A 10-minute tour of the demo, with the figures you should see at each step. It works the same on the [live demo](../README.md#try-it) and on your own computer ([run it locally](../README.md#run-it-locally)). All data is synthetic: an FMCG company with three countries, cost centres for costs, profit centres and brands for sales, posted to GL codes.

## 1. The month at a glance

The app opens on August 2026, actual vs budget, analysed by **cost / profit centre** with sales and cost of sales **by brand**.

| Tile | Expected |
| --- | --- |
| Sales | INR 30,500,000 (+500,000 vs budget) |
| EBITDA | INR 4,225,000 (−265,000 vs budget), 13.9% margin |
| Net profit | INR 2,270,000 (−195,000 vs budget) |
| Lines to explain | 16 (of 51 lines) |
| Explained (confirmed) | 0% until explanations are entered |

Underneath: 28 GL codes roll up into 11 accounts, and a completeness warning names SG-COR, a cost centre in the master with no August rows.

**Profit and loss.** Sales above plan is favorable; costs above plan are unfavorable. Each line shows its *profit impact*: Cost of sales −400,000, Distribution −340,000, Consulting −130,000. Gross margin is 54.4% against 55.0% (−0.6 pts). The waterfall walks budget EBITDA (4.49m) to actual (4.22m); the dropdown switches it to Net profit, Gross margin, Sales or Costs.

Switch **Compare actuals against** to forecast: net profit becomes −97,500 vs forecast and sales +250,000.

## 2. The story the totals hide

**Sales and gross margin by brand:**

| Brand | Sales | vs budget | GM % | GM % budget |
| --- | ---: | ---: | ---: | ---: |
| Aurora | 12,350,000 | +350,000 | 53.6% | 55.0% |
| Nimbus | 9,240,000 | −260,000 | 54.3% | 55.0% |
| Breeze | 8,910,000 | +410,000 | 55.7% | 55.0% |

Aurora grew but its margin fell: input costs rose. Nimbus lost sales.

**Variance detail → View by → Account:** India Product sales is net +300,000, but its brands move 1,020,000 gross (Nimbus −360,000 from a retailer delisting; Aurora and Breeze up), so 720,000 is hidden inside a calm total, flagged ⇅ Offsetting movements. India Distribution is net +200,000 but gross 280,000 (IN-OPS +240,000, IN-SAL −40,000).

**View by → Cost / profit centre:** SG-OPS Singapore Operations is net −153,000 but gross 233,000. PC-AU Australia Commercial is net +200,000, gross 600,000.

**Where the variance sits** (heatmap): every row is a cost centre, or a profit centre and brand; outlined cells need an explanation.

## 3. Why each of the 16 lines needs an explanation

- **On its own** (INR 25,000 and 10% at line level): for example India Commercial Nimbus Product sales −360,000, India Operations Distribution +240,000.
- **Account total** (spread variance): India Employee costs is +100,000 (5%) in total, material, while no cost centre line is. All three lines (IN-SAL +60,000, IN-OPS +20,000, IN-COR +20,000) are marked "Yes (account total)", so each owner explains their share.
- **Negative balance**: India Corporate Recruitment is INR −5,000 against a 10,000 budget after an agency credit note. A line that nets below zero always needs a look, whatever its size, and cannot be marked Recurring.

Change **Sales and cost of sales by** to SKU or Category, or remove **Cost / profit centre** under **Analyse by**: lines add up to the new level and the P&L totals do not change.

## 4. Explain a line

Under **Business explanations**, the first line is India | PC-IN India Commercial | Nimbus | Product sales (INR 360,000 unfavorable, owner Rohan Iyer). Inside it:

- **SKUs in this line (2):** NIM-100 −300,000, NIM-200 −60,000
- **GL codes in this line (2):** 410100 Sales to distributors −180,000, 410200 Online sales −180,000

Enter a reason, a reference, the amount explained, a review status and the timing (one-off, recurring, timing difference). Or click **Load demo explanations** for 9 fictional notes covering every case: confirmed, partial, vague, unconfirmed, conflicting and missing. Then:

| Measure | Expected |
| --- | --- |
| Explained (confirmed) | 66% (INR 1,110,000 of 1,685,000 material movement) |
| Unexplained | INR 575,000 across 11 open items |
| Full-year view (FY2026-27, 7 months left) | Confirmed items point to INR −2,220,000 in profit terms |

Only *confirmed* notes count. India Sales Employee costs ("Higher costs", unconfirmed) and Australia Operations Distribution (conflicting explanations) stay open. The full-year view rolls Aurora's input-cost increase (recurring) forward to March; timing differences reverse; nothing unexplained is projected.

The **All lines** tab edits every line in one table; **Excel round-trip** downloads a template to send to owners and reads it back.

## 5. Chase the open items

**Ask the interviewer**: follow-up questions per line, grouped by owner, downloadable as a query list (Markdown or CSV). Fixed rules catch missing, unconfirmed and partial explanations; the AI flags vague wording such as "Higher costs".

## 6. Lock, then produce the CFO pack

1. **Lock review pack.** Figures and notes are frozen under a SHA-256 fingerprint. Change anything afterwards and the exports are withheld until you lock again.
2. Download the **Excel workbook** (summary, variances, explanations, open items, roll-ups, margins by brand, data checks) or the Markdown and JSON.
3. **Draft CFO commentary and deck.** Two agents read the same locked pack in parallel.
   - **Commentary:** opens with "Net profit was INR 195,000.00 below budget (unfavorable)". In the demo, 5 AI items fail the tick-and-tie check and are replaced by standard wording; **Reviewer details: what the AI got wrong (5)** lists why. Two of them are summary bullets that put real figures in the wrong list (India Corporate Consulting, an overspend, listed as favorable; and as unexplained, though it is fully explained).
   - **CFO deck** (12 slides): title, executive summary, P&L, EBITDA bridge, sales and margin by brand, largest movements with the owners' reasons, heatmap, what net figures hide, open items by owner, outlook, appendix. Code draws every chart, table and number; the AI writes headlines only, and any that fails the check is replaced.

## 7. Try your own data

Use synthetic data only on the live demo.

- **Sample files** in the sidebar: the expense file (by cost centre or by GL code), the sales file (profit centre × SKU), separate actuals/budget/forecast files in the shape a ledger exports them, a GL mapping, a centre master and a product master.
- Upload only the sample expense file: the review becomes expense-only. Actual INR 12,375,000 vs budget 12,010,000, +365,000, 14 lines to explain.
- **Column mapping:** any column names. "Posting Period", "Company Code", "G/L Account", "Profit Center", "Material" and "Plan" are recognised; every guess can be changed.
- **Data checks:** extra spaces are cleaned automatically. The same code spelled differently across files (510100, 0510100, "510 100", 510100.0) is offered as a match you tick. Codes Excel has corrupted (5.1E+11) stop the file.
- **Things that stop the file, on purpose:** a GL code missing from the mapping, or a SKU missing from the product master (the app offers the master back with the new codes highlighted); a SKU in two brands; a cost centre with two owners; duplicate lines; missing amounts; mixed months.

## How notes behave

Each note belongs to one line, month, comparison basis and set of figures. If the figures change, the note no longer applies. Notes saved at one level (brand) do not carry over to another (SKU); switching back restores them. Notes live in the browser session: download the explanations (JSON) to keep them, and restore them later. Reference labels are not fetched or verified, and "confirmed" is the reviewer's assertion. Amounts explained cannot exceed the variance.
