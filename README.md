# FP&A Variance Analysis & Commentary Assistant

A learning portfolio project connecting finance judgment, controlled calculations and management reporting. Version 0.1 calculates expense variances and produces rule-based draft commentary. AI generation is planned, not implemented.

## Business problem

During monthly reporting, finance teams must reconcile actuals against budget or latest forecast, identify material movements and prepare explanations without inventing causes. This prototype makes those steps visible and repeatable.

## What works

- Fictional demo: three countries, five expense categories, August 2026.
- One-month CSV upload, with INR reporting amounts.
- Actual versus budget or forecast, country filter and configurable materiality.
- Validation that stops on missing values, duplicate keys and invalid amounts.
- Ranked variance detail and downloadable management commentary.
- Decimal arithmetic and automated checks of financial behavior.

This is a demonstration, not an employer system. No employer data or internal methodology is included. All countries' sample amounts are already expressed in INR; this is not an FX conversion model.

## Windows: first run

1. Download and extract the project ZIP. Open the folder containing `app.py`.
2. Install Python 3.13 from https://www.python.org/downloads/windows/ if needed. Select the installer for your computer. The installer may offer an option to add Python to PATH; enable it if shown.
3. Click the folder's address bar in File Explorer, type `cmd`, and press Enter. This opens Command Prompt in the right folder.
4. Run these commands one at a time:

```bat
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

If an existing Python 3.10-3.14 installation is being used, replace `py -3.13` in the first command with `python`. Internet access is needed to install dependencies. After installation the demo uses no AI account or API key. Leave Command Prompt open while using the app. If a browser tab does not open, visit http://localhost:8501. Stop the app with Ctrl+C.

Next time, open Command Prompt in the project folder and run only the last command.

Official setup reference: https://docs.streamlit.io/get-started/installation/command-line

## Your first finance check

With demo data, budget comparison, all countries and default thresholds:

| Measure | Expected |
| --- | ---: |
| Actual expense | INR 12,380,000 |
| Budget | INR 12,000,000 |
| Forecast | INR 12,190,000 |
| Actual minus budget | INR 380,000 unfavorable |
| Material account movements | 9 |

India Distribution should show actual INR 1,200,000 against budget INR 1,000,000: INR 200,000 / 20% unfavorable. India Consulting has a zero budget and actual INR 75,000: percentage N/A and material unbudgeted expense.

Try changing the comparison to forecast. The total variance becomes INR 190,000 unfavorable. Change the INR threshold and observe which rows require commentary.

## Input format

Use the app's **Download sample input** button to obtain a CSV that can be edited in Excel. Save edited files as CSV UTF-8. Keep columns in this order:

`month,country,account,currency,actual,budget,forecast`

Month is YYYY-MM; currency is INR. Amounts must be unformatted nonnegative numbers with at most two decimal places (no currency signs or grouping commas). All fields are required. Each month/country/account/currency combination must be unique. One file contains one month. For an entirely missing account to be detected, a future version needs an expected-account roster.

## Calculation rules

Variance = actual minus baseline. Percentage = variance / baseline x 100. Positive expense variance is unfavorable. When baseline is zero, percentage is N/A. Material movements meet BOTH absolute amount >= INR 50,000 AND absolute percentage >= 5% by default. A zero baseline uses only the amount threshold. Zero movement is never material.

Percentages are calculated before display rounding. All calculations use Decimal. The on-screen summary rounds amounts to whole INR; detail and downloaded commentary preserve finer amounts. The aggregate net variance can conceal offsetting account movements, so both are shown.

Commentary describes observed movement and explicitly requests investigation of the cause. No explanation is inferred from amounts alone. Revenue, negative amounts/credits, multiple periods and currency conversion are outside version 0.1.

## Checks

```bat
.venv\Scripts\python -m unittest discover -s tests -v
```

Checks cover sample control totals, zero baselines, both materiality thresholds, favorability, exact threshold boundaries, forecast comparisons and invalid input rejection.

## GitHub

Suggested repository name: `fpna-variance-assistant`.

Suggested description: Expense variance analysis with input validation, configurable materiality and evidence-aware management commentary using synthetic finance data.

Upload the contents of this folder into the repository root so the README is visible. Keep the `tests` folder structure. Do not upload `.venv`, personal source data or secrets. The supplied `.gitignore` excludes common local files when using Git; browser uploads still require choosing the correct files.

GitHub stores and displays the project source. Publishing a repository alone does not run the app. A hosted demonstration can be added after the local version is reviewed.

## Learning roadmap

1. Run the demo and validate the finance outcomes.
2. Refine the input format, finance rules and commentary with user feedback.
3. Add explicitly supplied driver evidence and human-reviewed AI commentary.
4. Document evaluation results and prepare a short demonstration for the portfolio.

## Build transparency

This starter was generated with AI assistance. Its finance rules and limitations are documented for review. No time savings, production deployments or business results are claimed. Portfolio ownership should be demonstrated by validating the logic, explaining design choices and making considered improvements.

## Verification status

The finance test suite has been run in the build environment. The Windows setup and Streamlit browser interface still need a first run on the target laptop. Dependencies have version bounds rather than a fully tested lockfile; a reproducible lockfile can be added after that run.
