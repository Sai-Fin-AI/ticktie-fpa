# FP&A Variance Assistant

[![tests](https://github.com/casaikiran93-maker/fpa-variance-assistant/actions/workflows/tests.yml/badge.svg)](https://github.com/casaikiran93-maker/fpa-variance-assistant/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12 | 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)

**Month-end variance analysis that never invents a cause.** Upload actuals, budget and forecast. The tool validates the data, calculates variances in exact decimal arithmetic and flags material movements. It then asks the reviewer to explain each one. Figures come only from the engine; explanations come only from people and are labelled as such.

Version 0.4: AI interviewer and AI CFO commentary, with every AI figure tick-and-tied to a locked review pack. Runs free, on a local model or free-tier services. A CFO slide-deck generator is next (see the [roadmap](docs/ROADMAP.md)).

<!-- Add a screenshot: save it as docs/screenshot.png and uncomment the line below. -->
<!-- ![App screenshot](docs/screenshot.png) -->

## Why it exists

During monthly reporting, FP&A teams reconcile actuals against budget or the latest forecast, pick out material movements and chase explanations. All of it is done under deadline pressure. The risk is commentary that sounds confident but restates numbers wrongly or guesses at causes. This project makes each step explicit, repeatable and reviewable.

## What it does

- **Validates before it calculates.** Missing values, duplicate keys, wrong currency, negative or malformed amounts and mixed months stop the analysis. Nothing is assumed.
- **Variance vs budget or forecast**, ranked by size, filterable by country.
- **Configurable materiality.** The amount **and** percentage thresholds must both be met; zero baselines use the amount only.
- **Reviewer explanations** per material line: reason, reference, amount explained, review status and timing (one-off, recurring or timing difference).
- **Explained vs unexplained.** Only *confirmed* notes reduce the unexplained balance. Unconfirmed and conflicting notes stay open.
- **Downloadable management commentary** that keeps verified figures separate from reviewer-supplied claims.
- **Locked review pack.** One click freezes figures and explanations for all countries under a SHA-256 fingerprint. Exports come only from the locked pack and are hidden if anything changes afterwards.
- **Excel, Markdown and JSON exports.** The workbook has Summary, Variances, Explanations and Open items sheets.
- **Explanations backup and restore.** Restored notes are re-checked against the current figures; stale ones are skipped.
- **AI interviewer.** Before locking, it lists follow-up questions for each material line. Fixed rules catch missing, unconfirmed and partial explanations and unknown timing; the AI flags vague wording such as "higher costs" and suggests a sharper question.
- **AI CFO commentary.** Drafted from the locked pack. The AI writes wording only for explained lines; unexplained lines, open items and actions come straight from the pack.
- **Tick-and-tie checker.** Plain code with no AI. Every figure in AI text must exist in the pack (lakh, crore and million are understood). It also catches favourable/unfavourable mix-ups, unconfirmed or conflicting notes presented as fact, partial explanations that hide the unexplained gap, and internal codes. A failing draft gets one retry with the problems listed; if it fails again, download is blocked.

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
    E -. planned .-> G[CFO deck agent]
```

| File | Role |
| --- | --- |
| `finance.py` | CSV validation, variance and materiality engine (Decimal only) |
| `evidence.py` | Reviewer notes, coverage arithmetic, evidence-aware commentary |
| `pack.py` | Locked review pack, tamper check, Excel/Markdown/JSON exports, notes backup and restore |
| `llm.py` | One connection for every AI provider, plus demo mode |
| `agents/` | `interviewer.py` (follow-up questions) and `writer.py` (CFO commentary) |
| `checker.py` | Tick-and-tie of AI text against the pack (no AI) |
| `demo/` | Fictional demo explanations and recorded AI answers; rebuild with `scripts/record_demo.py` |
| `app.py`, `ai_panel.py` | Streamlit interface |
| `tests/` | 40 tests: engine, evidence, review pack, agents, checker, provider connection and UI. No AI or network needed. |

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

Download **sample input** from the app sidebar and edit it in Excel (save as CSV UTF-8). Columns, in this order:

`month,country,account,currency,actual,budget,forecast`

- `month` is `YYYY-MM`, one month per file
- `currency` is `INR`; all countries are already translated (no FX conversion)
- Amounts are plain nonnegative numbers with at most 2 decimals: no currency symbols or thousands separators
- Each month/country/account/currency combination must be unique

## Calculation rules

- Variance = actual − baseline. Percentage = variance ÷ baseline × 100, calculated before display rounding.
- Positive expense variance is **unfavorable**; negative is **favorable**.
- Zero baseline → percentage is N/A; only the amount threshold applies. Zero movement is never material.
- Defaults: material when |variance| ≥ INR 50,000 **and** |%| ≥ 5%.
- Net variance can hide offsetting movements, so the total and the line detail are both shown.

## Limitations

Expense accounts only (no revenue or credits), one month per file, INR only. Accounts missing entirely from the upload can't be detected without an expected-account roster. Notes live in the browser session unless you download the explanations backup. The checker verifies figures, direction and review status, not meaning: an AI sentence can still paraphrase an explanation loosely, so commentary must be read before use. Demo mode only covers the sample data with the demo explanations. Reference labels are not fetched or verified, and "confirmed" is the reviewer's assertion. Full details are in [VERIFICATION.md](VERIFICATION.md).

## Roadmap

1. ✅ Engine, validation, reviewer explanations, tests
2. ✅ Locked review pack + Excel/Markdown/JSON export, explanations backup and restore
3. ✅ AI interviewer and CFO commentary writer with tick-and-tie checker; free local and free-tier AI
4. 🔜 CFO deck agent (python-pptx), running in parallel with the commentary agent on the same locked pack
5. Hosted demo

Full plan, tools and backlog: [docs/ROADMAP.md](docs/ROADMAP.md).

## Data and transparency

All sample data is synthetic. No employer data or internal methodology is included. The project was built with AI assistance; the finance rules and their limitations are documented so they can be reviewed. No production use, time savings or business results are claimed.

## License

[MIT](LICENSE)
