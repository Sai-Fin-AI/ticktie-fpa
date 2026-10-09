# FP&A Variance Assistant

[![tests](https://github.com/casaikiran93-maker/fpa-variance-assistant/actions/workflows/tests.yml/badge.svg)](https://github.com/casaikiran93-maker/fpa-variance-assistant/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12 | 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)

**Month-end variance analysis that never invents a cause.** Upload actuals, budget and forecast. The tool validates the data, calculates variances in exact decimal arithmetic and flags material movements. It then asks the reviewer to explain each one. Figures come only from the engine; explanations come only from people and are labelled as such.

Version 0.7: a full P&L from sales to net profit (or an expense-only review), cost centres, owners and any extra dimension, plus an AI interviewer and AI CFO commentary, with every AI figure tick-and-tied to a locked review pack. Runs free, on a local model or free-tier services. It also builds a CFO PowerPoint deck in parallel with the commentary (see the [roadmap](docs/ROADMAP.md)).

<!-- Add a screenshot: save it as docs/screenshot.png and uncomment the line below. -->
<!-- ![App screenshot](docs/screenshot.png) -->

## Why it exists

During monthly reporting, FP&A teams reconcile actuals against budget or the latest forecast, pick out material movements and chase explanations. All of it is done under deadline pressure. The risk is commentary that sounds confident but restates numbers wrongly or guesses at causes. This project makes each step explicit, repeatable and reviewable.

## What it does

- **Validates before it calculates.** Missing values, duplicate keys, wrong currency, negative or malformed amounts and mixed months stop the analysis. Nothing is assumed.
- **Variance vs budget or forecast**, ranked by size, filterable by country.
- **Full P&L, sales to net profit.** Add an optional *Sales and other P&L lines* file next to the expense file (sales, cost of sales, depreciation, interest, tax). Each account sits on a P&L line, read from a `pl_line` column or from the account name ("Product sales" → Sales, "Cost of goods sold" → Cost of sales). The statement shows Gross margin, EBITDA, EBIT, Profit before tax and Net profit, with gross, EBITDA and net margins. **Sign logic flips for sales**: sales above plan is favorable, costs above plan unfavorable, and every line shows its *profit impact* (+ helps profit). Without a sales file it stays an expense review, exactly as before.
- **Waterfall for any measure.** A dropdown walks budget to actual for EBITDA, Net profit, Gross margin, Sales or Costs, account by account; the CFO deck uses the same choice.
- **Cost centres and any extra dimension.** Files can carry a `cost_center` column, cost centre details (name, function, owner) and extra columns such as project or region. **Analyse by** chooses the level; removing a level adds its rows up. The demo's 23 cost centre lines reconcile exactly to the 15-row account file.
- **Cost centre master (optional).** Unknown cost centres and contradicting details stop the file. Cost centres with no data are listed as a completeness check.
- **Configurable materiality.** The amount **and** percentage thresholds must both be met; zero baselines use the amount only. Cost centre lines and account totals have separate thresholds.
- **Spread variances.** An account total that is material while no single cost centre is brings every moving line into scope, so each owner explains their share.
- **Gross vs net.** Roll-ups by account, cost centre, function and any extra dimension show gross movement next to net and flag offsets, e.g. Singapore Employee costs: net INR 0, gross INR 180,000.
- **Heatmap** of cost centre × account variance, with lines needing explanation outlined.
- **Reviewer explanations** per material line: reason, reference, amount explained, review status and timing (one-off, recurring or timing difference). Three ways in: one line at a time, an **editable table of every line** with one save, or an **Excel round-trip** (download a pre-filled template with status/timing dropdowns, send it to owners, upload it back). Every row goes through the same checks; a bad row is reported and never blocks the others.
- **Full-year view.** Confirmed recurring items roll forward to the financial-year end (start month configurable, default April), timing differences reverse and one-offs happen once; unexplained variance is never projected. Shown on screen, in the Excel summary, the commentary and the deck.
- **Number style.** International (12,380,000 · 340k · 12.38m) or Indian (1,23,80,000 · 3.40 L · 1.24 Cr) across the screen, commentary and deck. The figures and the fingerprint do not change.
- **Explained vs unexplained.** Only *confirmed* notes reduce the unexplained balance. Unconfirmed and conflicting notes stay open.
- **Downloadable management commentary** that keeps verified figures separate from reviewer-supplied claims.
- **Locked review pack.** One click freezes figures and explanations for all countries under a SHA-256 fingerprint. Exports come only from the locked pack and are hidden if anything changes afterwards.
- **Excel, Markdown and JSON exports.** The workbook has Summary, Variances, Explanations and Open items sheets.
- **Explanations backup and restore.** Restored notes are re-checked against the current figures; stale ones are skipped.
- **AI interviewer.** Before locking, it lists follow-up questions for each material line, grouped **by owner**, with a downloadable query list (Markdown or CSV) for the month-end chase. Fixed rules catch missing, unconfirmed and partial explanations, spread-variance shares and unknown timing; the AI flags vague wording such as "higher costs" and suggests a sharper question.
- **AI CFO commentary.** Drafted from the locked pack. The AI writes wording only for explained lines; unexplained lines, open items and actions come straight from the pack.
- **CFO-ready layout.** Headline tiles, colour-coded line cards (▲ unfavorable / ▼ favorable), review-status and timing chips, and an explained-vs-unexplained bar per line. Download it as HTML (prints neatly to PDF), Markdown or JSON. Every colour is paired with an arrow, icon or word, so it reads in black-and-white print too.
- **CFO deck (PowerPoint).** Built in parallel with the commentary from the same locked pack, in 16:9 with 10 slides: title, executive summary with KPI tiles, budget-to-actual **waterfall bridge**, largest movements, cost centre × account **heatmap**, what net figures hide (gross vs net), open items by owner, run-rate outlook by timing (one-off / recurring / timing difference) with the full-year view and reviewer actions, and an appendix. The largest-movements slide shows each owner's **reason** next to the figures. Code draws every chart, table and number; headlines are standard (engine-written) by default, or AI-written on request, and any AI headline that fails the checks is replaced by standard wording.
- **Tick-and-tie checker.** Plain code with no AI. Every figure in AI text must exist in the pack (lakh, crore and million are understood). It also catches favourable/unfavourable mix-ups, unconfirmed or conflicting notes presented as fact, partial explanations that hide the unexplained gap, internal codes, and **figures attached to the wrong line**: each amount must belong to the name written just before it, e.g. INR 340,000 is Distribution across all countries, not India Distribution. With cost centres, "India Distribution" means the account total, so commentary that calls one cost centre's line by that name is rejected. A failing draft gets one retry with the problems listed; if it fails again, download is blocked.

## AI modes (all free)

| Mode | Who it's for | Setup |
| --- | --- | --- |
| **Demo** (default) | Anyone, instantly | None. Click **Load demo explanations**. Replays answers recorded from a local model and labelled as recorded. |
| **Ollama** | Running it on your own computer | Install [Ollama](https://ollama.com), then `ollama pull qwen3:8b` (about 5 GB; needs a GPU with 8 GB or 16 GB RAM). Nothing leaves your machine. |
| **Groq / Gemini / OpenRouter** | Live AI without a GPU | A free-tier API key pasted into the sidebar. It is used for that session only and never saved. Free tiers have daily limits, and some providers may use requests to improve their models. Use synthetic data only. |

All providers share one connection (`llm.py`) because they accept the same OpenAI-style request format; only the address, key and model differ.

## How it works

```mermaid
flowchart LR
    A[CSV upload] --> B[finance.py<br/>validate · variance · materiality]
    B --> C[Streamlit review screen]
    C --> D[Reviewer explains<br/>material lines<br/>evidence.py]
    D --> E[Locked review pack<br/>pack.py · fingerprinted]
    D <-.-> I[AI interviewer<br/>follow-up questions]
    E --> X[Excel · Markdown · JSON]
    E --> F[AI commentary writer]
    F --> K{Tick-and-tie<br/>checker.py}
    K -- passes --> O[CFO commentary download]
    K -- fails --> F
    E --> G[CFO deck agent<br/>agents/deck.py]
    G --> K
```

| File | Role |
| --- | --- |
| `finance.py` | CSV validation, variance and materiality engine (Decimal only) |
| `evidence.py` | Reviewer notes, coverage arithmetic, evidence-aware commentary |
| `ingest.py` | CSV/Excel reading, column-role guessing, month and amount parsing, matching separate actuals/budget/forecast files |
| `data_panel.py` | Data source choice, column-mapping dropdowns, reconciliation of control totals |
| `pack.py` | Locked review pack, tamper check, Excel/Markdown/JSON exports, notes backup and restore |
| `llm.py` | One connection for every AI provider, plus demo mode |
| `agents/` | `interviewer.py` (follow-up questions) and `writer.py` (CFO commentary) |
| `checker.py` | Tick-and-tie of AI text against the pack (no AI) |
| `report_html.py` | Styled CFO commentary page (tiles, cards, coverage bars), HTML-escaped |
| `demo/` | Fictional demo explanations and recorded AI answers; rebuild with `scripts/record_demo.py` |
| `app.py`, `ai_panel.py` | Streamlit interface |
| `notes_panel.py` | Bulk explanation entry: editable table and Excel template round-trip |
| `fmt.py` | Number style (international or Indian lakh/crore) for screen, commentary and deck |
| `charts.py` | On-screen waterfall (Altair) |
| `tests/` | 106 tests: engine, data loading and column mapping, cost centres and roll-ups, evidence, review pack, agents, checker, provider connection and UI. No AI or network needed. |

## Run it locally

Requires Python 3.12 or 3.13. On Windows, open Command Prompt in the project folder:

```bat
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run app.py
```

The app opens at http://localhost:8501 with fictional demo data loaded. No API key is needed. For a guided tour with expected figures, see the [walkthrough](docs/WALKTHROUGH.md).

Run the tests:

```bat
.venv\Scripts\python -m unittest discover -s tests -v
```

## Input format

Choose a **Data source** in the sidebar:

| Data source | Upload | Typical origin |
| --- | --- | --- |
| **One combined file** | One CSV or Excel file with actual, budget and forecast columns | A prepared variance file |
| **Separate files** | **Actuals** (required), **Budget** (required), **Forecast** (optional), each CSV or Excel | Actuals from the ledger (GL export), budget from the planning tool, forecast from the latest reforecast |

**Column mapping.** After upload, every column gets a dropdown (Month, Country / entity, Cost centre, GL account, Currency, Amount, cost centre name, function, owner, Extra dimension, Ignore), pre-filled from the column name. "Posting Period", "GL Account", "CC" and "Amount (INR)" are recognised, for example. Fix any guess with the dropdown; unknown columns such as Description default to Ignore.

**Matching separate files:**
- The actuals file sets the month; a full-year budget or forecast file is filtered to that month.
- Rows are added up into lines, so transaction-level exports work. Credit notes can be negative or in brackets, but a line that nets negative stops the file.
- An actual with no budget line is **unbudgeted (zero budget)**, and a budget with no actuals is **zero actual**. Both are listed, never hidden.
- If one file lacks a level (e.g. budget by GL only), the files are matched without it, and the app says so.
- **Data reconciliation** shows each file's rows, lines, ignored months and total against the totals used in the analysis.
- Without a country or currency column, you type the entity name, and amounts are read as INR.

Samples for both routes are in the sidebar; the separate samples (GL-style actuals CSV, full-year budget **Excel**, forecast CSV) rebuild the combined demo exactly.

Standard columns (combined file or after mapping):

| Column | Required? | Notes |
| --- | --- | --- |
| `month`, `country`, `account`, `currency`, `actual`, `budget`, `forecast` | Yes | |
| `cost_center` | Optional | Also accepted as "Cost Centre" or `cc_code`. A cost centre belongs to one country. |
| `cc_name`, `function`, `owner` | Optional | Must be the same on every row of a cost centre. Can come from the master instead. |
| Any other column, e.g. `project`, `region` | Optional | Becomes an extra level under **Analyse by**. |

- `month` is `YYYY-MM`, one month per file
- `currency` is `INR`; all countries are already translated (no FX conversion)
- Amounts are numbers with at most 2 decimals; thousands separators (1,20,000 or 120,000) are accepted, currency symbols are not
- Every cell must be filled, and each month/country/cost centre/extra columns/account/currency combination must be unique

**Cost centre master** (optional): `cost_center` plus any of `country`, `cc_name`, `function`, `owner`. Every cost centre in the data must be listed. If the data and the master disagree, the file is stopped rather than guessed.

## Calculation rules

- Variance = actual − baseline. Percentage = variance ÷ baseline × 100, calculated before display rounding.
- Positive expense variance is **unfavorable**; negative is **favorable**.
- Zero baseline → percentage is N/A; only the amount threshold applies. Zero movement is never material.
- Defaults: material when |variance| ≥ INR 50,000 **and** |%| ≥ 5%. With cost centres, those apply to account totals, and each cost centre line uses INR 25,000 **and** 10%.
- Spread rule: if a country × account total is material but none of its lines is, every line in it with a movement must be explained.
- Gross movement = sum of the absolute line variances. Offsets are flagged when gross − |net| ≥ the account amount threshold.
- Explanations are given at the chosen level and roll up: an account's explained amount is the sum of the confirmed notes beneath it.

## Limitations

Expense accounts only (no revenue or credits), one month per file, INR only. The master catches cost centres with no data, but an account missing from a cost centre that has other rows can't be detected without an expected cost centre × account roster. Explanations saved at one level (e.g. cost centre) don't carry over if you switch to another level. Notes live in the browser session unless you download the explanations backup. The checker verifies figures, direction and review status, not meaning: an AI sentence can still paraphrase an explanation loosely, so commentary must be read before use. Demo mode only covers the sample data with the demo explanations. Reference labels are not fetched or verified, and "confirmed" is the reviewer's assertion. Full details are in [VERIFICATION.md](VERIFICATION.md).

## Roadmap

1. ✅ Engine, validation, reviewer explanations, tests
2. ✅ Locked review pack + Excel/Markdown/JSON export, explanations backup and restore
3. ✅ AI interviewer and CFO commentary writer with tick-and-tie checker; free local and free-tier AI
3b. ✅ Cost centres, owners, extra dimensions, roll-ups with gross vs net, spread variances, heatmap, owner query lists
3c. ✅ Separate actuals / budget / forecast uploads (CSV or Excel) with column-mapping dropdowns and reconciliation
4. ✅ CFO deck agent (python-pptx), running in parallel with the commentary agent on the same locked pack
5. 🔜 Hosted demo

Full plan, tools and backlog: [docs/ROADMAP.md](docs/ROADMAP.md).

## Data and transparency

All sample data is synthetic. No employer data or internal methodology is included. The project was built with AI assistance; the finance rules and their limitations are documented so they can be reviewed. No production use, time savings or business results are claimed.

## License

[MIT](LICENSE)
