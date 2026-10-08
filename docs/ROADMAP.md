# Roadmap: FP&A Variance Assistant → CFO Review Pack

Living plan for turning the v0.2 prototype into a portfolio project: variances → reviewer-supplied reasons → AI-compiled commentary → CFO-ready slides.

Last updated: 8 October 2026.

## 1. Idea evaluation

**Verdict: strong portfolio idea, provided the controls stay the headline.** Thousands of "AI writes your variance commentary" demos exist. What most of them lack is what finance leaders worry about: invented causes, numbers that drift between the model and the ledger, and no review trail. This project already has that discipline (Decimal math, hard validation, "the input does not establish the cause", confirmed vs unconfirmed notes). Keep it as the differentiator.

| Strengths | Risks | Mitigation |
| --- | --- | --- |
| Real monthly pain point every FP&A hiring manager recognises | Scope creep: two agents, slides, hosting | Ship in phases; each phase is demo-able on its own |
| Human-in-the-loop by design: the tool asks, the reviewer explains | AI restates numbers wrongly in text or slides | Numbers come only from the engine; a validator rejects any AI output whose figures don't match the pack |
| Deterministic engine already tested (21 tests) | Hosted demo costs API money if recruiters click it | Demo mode with recorded AI responses; live AI only with the visitor's own key or a hard daily cap |
| Shows finance judgment *and* engineering *and* AI governance | Data privacy | Synthetic data only; never upload employer data |
| Slides are what CFOs actually consume, so it ends at a real deliverable | Looks "AI-generated" to reviewers | Write the design decisions yourself (section 6); be able to explain every rule |

**On the "parallel agent":** the deck depends on confirmed reasons, so the overall flow is sequential. The parallel part is real but narrower: once the reviewer locks the pack, the **commentary agent** and the **deck agent** both read the same locked data and can run at the same time. Neither depends on the other's output. That is a clean, defensible design to explain in an interview.

## 2. Target architecture

```
CSV upload ──► finance.py (validate, variance, materiality)      deterministic
                    │
                    ▼
          Reviewer explains material lines (evidence.py)        human
          AI asks follow-ups when a reason is vague or            AI-assisted
          doesn't cover the variance
                    │
                    ▼
          LOCKED REVIEW PACK (pack.json): figures + notes        single source of truth
                    │
          ┌─────────┴──────────┐   run in parallel
          ▼                    ▼
   Commentary agent        Deck agent
   structured JSON →       python-pptx: charts & tables built
   Word/Markdown/Excel     from pack, AI writes headlines only
          │                    │
          └──────► Number validator ◄──┘   every figure must match the pack
                    │
                    ▼
          Reviewer approves → download commentary + CFO deck
```

Key rule: **the AI never does arithmetic.** It receives figures and returns wording that references them; code checks the figures back against the pack.

## 3. Project map (step by step)

Status: ✅ done · 🔜 next · ⬜ planned

### Phase 0: Foundation (done in v0.2)
- ✅ Validated CSV loader, variance engine, configurable materiality
- ✅ Streamlit UI with country filter, budget/forecast switch
- ✅ Reviewer notes: reason, reference, amount explained, status, timing, action
- ✅ Rule-based commentary download; 21 automated tests
- ✅ Tests pass on Windows, Python 3.13 (8 Oct 2026)

### Phase 1: Publish-ready repo (≈1 day)
- ✅ Add `LICENSE` (MIT), GitHub Actions CI running the test suite on every push (Ubuntu + Windows, Python 3.12/3.13)
- ✅ Tighten README: pitch, badges, Mermaid architecture diagram; walkthroughs moved to `docs/WALKTHROUGH.md`
- ✅ Lock dependencies (`requirements.txt` with exact versions)
- 🔜 Add a screenshot (`docs/screenshot.png`) to the README
- 🔜 Repo description, topics (`fpa`, `variance-analysis`, `streamlit`, `finance-automation`, `llm`), pin to GitHub profile

### Phase 2: Structured review pack (done 8 Oct 2026)
- ✅ `pack.py`: one record holding period, basis, thresholds, totals, every line, notes, explained/unexplained amounts and open items. It always covers all countries, whatever filter is on screen.
- ✅ "Lock review pack" in the UI: freezes the pack with a SHA-256 fingerprint. Any later change to figures, thresholds or notes is detected, and downloads stay hidden until you lock again.
- ✅ `load_pack` refuses a pack file whose contents were edited after locking (the guard the AI agents will rely on)
- ✅ Exports: Excel workbook (Summary, Variances, Explanations, Open items), Markdown commentary, pack JSON. Amounts are stored as exact decimal strings.
- ✅ Explanations backup/restore (JSON). On restore, each note is re-validated against the current data; notes for changed figures are skipped, not adapted.
- ✅ 7 new tests (28 total): reconciliation, open-item logic, fingerprint/tamper checks, Excel and Markdown contents, notes round-trip, lock/relock and restore in the app
- ⬜ Word export (deferred; Markdown converts cleanly if needed)

### Phase 3: Commentary agent (≈3–4 days)
**Decision (8 Oct 2026): free, local AI.** The agent runs on a local open-weight model through [Ollama](https://ollama.com), so there are no API costs and no data leaves the machine. Provider code sits behind a small interface (`llm.py`), so a hosted API could be added later without touching the agents. The hosted demo uses recorded responses from the local model, clearly labelled.
**Status (8 Oct 2026): built.** `llm.py` (Ollama, Groq, Gemini, OpenRouter and demo mode through one OpenAI-style connection), `agents/interviewer.py`, `agents/writer.py`, `checker.py`, demo recordings from `qwen3:8b` on an RTX 4060, 12 new tests (40 total). The items below are kept for the record; open items are marked ⬜.
- ✅ `llm.py`: provider interface with an Ollama backend (local HTTP API, JSON-schema structured output) and a demo backend (recorded responses)
- ✅ `agents/writer.py` requesting structured (JSON-schema) output
- ✅ **Follow-up questions:** for each material line, the agent flags vague reasons ("higher costs"), unexplained gaps and missing timing, then asks the reviewer a specific question. This is the "ask me for reasons" loop.
- ✅ **Compilation:** executive summary and per-line commentary from the AI; open items and actions assembled by code from the pack
- ✅ Guardrails: `checker.py` checks every figure, direction words, review-status wording, hidden unexplained gaps and internal codes; one retry with the problems listed, then download is blocked
- ✅ Demo mode: recorded responses for the sample data so the hosted app works without an API key
- ⬜ Daily usage cap when the app owner's free key is used on the hosted demo (Phase 5)
- ⬜ Critic agent: an AI second reviewer for loose paraphrasing that the rule-based checker can't judge
- ⬜ Eval set: 10–20 golden cases (vague reason, conflicting notes, zero budget, favorable variance) scored for hallucinated numbers/causes. Publish the scores in `docs/EVALUATION.md`.

### Phase 4: CFO deck agent (≈4–5 days)
- ⬜ `agents/deck.py` building a `.pptx` with python-pptx. Slides:
  1. Title & period
  2. Executive summary (3 bullets: net result, biggest drivers, what needs a decision)
  3. Budget → Actual bridge (waterfall chart)
  4. Top material variances (table, sorted, explained vs unexplained)
  5. Country view
  6. Open items & unexplained variance
  7. Actions, owners & outlook (one-off vs recurring)
  8. Appendix: full variance detail
- ⬜ Charts/tables generated by code from the pack; AI writes only slide titles ("so-what" headlines) and summary bullets
- ⬜ Run commentary + deck agents concurrently on the locked pack (`concurrent.futures` / `asyncio`)
- ⬜ Same number validator applied to slide text; reviewer previews and approves before download

### Phase 5: Host & showcase (≈1–2 days)
- ⬜ Deploy to Streamlit Community Cloud from the GitHub repo in demo mode (recorded local-model responses; Ollama can't run there)
- ⬜ 60–90 second demo video/GIF: upload → explain → follow-up question → download deck
- ⬜ Case-study write-up (`docs/CASE_STUDY.md`): problem, design choices, controls, eval results, what you'd do next
- ⬜ Resume bullets + LinkedIn post (claim only what is built and measured)

## 4. Tools

| Purpose | Tool | Status |
| --- | --- | --- |
| Language | Python 3.13 | ✅ installed |
| Version control | Git (via GitHub Desktop, or install Git for Windows to use `git` in a terminal) | ✅ GitHub Desktop; `git` not on PATH |
| Hosting code | GitHub account + repo `casaikiran93-maker/fpa-variance-assistant` | ✅ |
| UI | Streamlit 1.64 | ✅ |
| Editor / AI pair | VS Code, Claude Code | ✅ |
| Data handling | `decimal` (stdlib); pandas optional for Excel/charts | ✅ pandas present |
| Schemas | pydantic (or stdlib dataclasses) | ⬜ |
| AI model | Ollama (local, free) with an ~8B open-weight model on the RTX 4060 (8 GB VRAM); called over its local HTTP API with `requests` | ✅ Ollama 0.34 installed; model to pull |
| Excel export | openpyxl | ⬜ |
| Word export (optional) | python-docx | ⬜ |
| Slides | python-pptx | ⬜ |
| Charts | matplotlib (images) or native python-pptx charts | ⬜ |
| Testing | unittest (current) / pytest; Streamlit AppTest | ✅ |
| Lint/format | ruff | ⬜ |
| CI | GitHub Actions | ⬜ |
| Deployment | Streamlit Community Cloud (free) | ⬜ |
| Demo recording | ScreenToGif / OBS / Loom | ⬜ |
| Diagrams | Mermaid in README (renders natively on GitHub) | ⬜ |

No agent framework (LangChain, LangGraph, CrewAI) is needed. Two focused agents with plain SDK calls are easier to test and explain. Add a framework only if a real need appears.

## 5. Improvement backlog

Prioritised. Pick from the top once Phases 1–4 are done.

**High value for FP&A credibility**
1. **Driver taxonomy:** classify each note as volume, price/rate, timing, one-off, reclass, FX or unknown. Enables a "variance by driver" slide.
2. **Full-year outlook impact:** recurring variances roll forward into a FY forecast risk; timing differences net to zero.
3. **Multi-month / YTD** view and month-on-month trend.
4. **Expected-account roster** to catch accounts missing from the upload (completeness check).
5. **Revenue support** with sign logic flipped (revenue above plan = favorable).
6. **Offsetting drivers:** several notes per line that bridge the full variance.

**AI quality & governance**
7. Reason-quality score per note (specific? quantified? evidenced?) shown to the reviewer.
8. Audit log: who changed which note, when; AI prompt/response stored with the pack hash.
9. Published evaluation results and a "known failure modes" section.
10. Prompt caching and a token/cost meter per run.

**Usability**
11. Excel (`.xlsx`) upload, not just CSV.
12. Column-mapping screen for differently named columns.
13. PDF export of the deck.
14. Branding/theme options for the deck (company colours, logo placeholder).

**Engineering**
15. Locked dependencies, ruff, type hints, coverage badge.
16. Split `app.py` into pages: Upload → Review → Explain → Outputs.

## 6. Design decisions to own (interview prep)

Be ready to explain each of these in your own words:
- Why Decimal and not float for money.
- Why materiality needs *both* the amount and percentage thresholds, and why a zero baseline uses the amount only.
- Why the AI never calculates and how the validator enforces it.
- Why unconfirmed or conflicting notes don't reduce unexplained variance.
- Why favorable expense variance isn't automatically "savings".
- Why the two agents run in parallel on a locked pack instead of chaining.

## 7. Publishing checklist

- [ ] Confirm repo visibility is **Public** (GitHub → Settings → General → Danger Zone → Change visibility)
- [ ] No real or employer data anywhere in history; `.venv`, `.env`, secrets ignored
- [ ] LICENSE, CI badge, screenshot and live demo link in README
- [ ] Description, website link and topics set on the repo page
- [ ] Pinned on GitHub profile; profile README mentions it
- [ ] Live app reachable while logged out; demo mode works without an API key
- [ ] Resume line links to the repo and the live demo
