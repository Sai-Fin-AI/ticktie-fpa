# Starter verification — 24 September 2026

The repository's six starter files were read through the authenticated GitHub connection. This verification uses that uploaded code, with the small interface fixes and tests in the accompanying change.

Environment: isolated Linux virtual environment, Python 3.12.14, Streamlit 1.64.0. No AI model or API credential was used.

## Result

`python -m unittest discover -s tests -v` — **12 tests passed**.

Separately, the Streamlit server was started on loopback and its `/_stcore/health` endpoint returned `ok`. The server was stopped after the check. This did not deploy a public application.

## What was checked

| Area | Evidence |
|---|---|
| Budget totals | Actual INR 12,380,000; budget INR 12,000,000; variance INR 380,000 unfavorable; nine material movements |
| Forecast totals | Forecast INR 12,190,000; variance INR 190,000 unfavorable |
| Country filter | India: actual INR 4,315,000; budget INR 4,000,000; five displayed rows belonging to India |
| Zero budget | India Consulting percentage is N/A and the INR 75,000 movement is material |
| Materiality | Both thresholds, exact boundaries, no-change rows and an increased threshold |
| Upload replacement | Changing one uploaded actual by INR 100,000 changes the total by INR 100,000 |
| Input identity | Uploaded data is labeled as uploaded; clearing the upload restores the demo and its label |
| Invalid uploads | Missing amounts, duplicates, unsupported currency and invalid encoding show errors and no analysis metrics |
| Numeric validation | Negative values, NaN, Infinity and excessive decimal precision are rejected |
| Sessions | An upload in one simulated AppTest session does not alter another session's demo |
| Commentary | The existing rule-based text does not assert a cause from amounts alone |

The UI checks execute the real `app.py` with Streamlit AppTest. They are automated widget/output checks, not screenshots or full browser automation. Dataframe assertions and finance-engine tests are complementary checks of the calculation results.

## Improvements included

- Correct the banner that previously claimed demo data was loaded after an upload.
- Replace a deprecated dataframe width setting with its supported equivalent.
- Pin the direct Streamlit dependency to the version used for verification.
- Add seven application regression tests and correct the repository name in the README.

## Remaining work

- User review of the interface and financial usefulness.
- Real-browser download checks and hosted multi-user testing.
- Windows installation testing, if the user later chooses to run locally.
- Full dependency locking and GitHub Actions setup.
- An expected-account roster if completeness beyond blank-cell checks is required.
- Evidence-linked AI commentary, limits for public operation and deployment.

No production reliability, realized savings or public availability is claimed.
