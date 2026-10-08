# Walkthrough

Hands-on checks for the demo data. Start the app first (see the [README](../README.md#run-it-locally)).

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

## Business explanations: first review

Choose **forecast**, then **India**. Under **Business explanations**, select **India | Distribution**.
Enter this fictional case:

| Field | Entry |
| --- | --- |
| Business explanation | Additional freight arose from an urgent shipment in August. |
| Supporting reference | Fictional freight note, August 2026 |
| Amount explained (INR) | 100000 |
| Review status | Confirmed by reviewer |
| Timing | One-off |
| Proposed next action (optional) | Review emergency shipment approvals next month. |

Click **Save explanation**. The draft retains the verified INR 100,000 / 9.1% unfavorable forecast variance, labels the explanation and reference as supplied by the reviewer, and shows INR 0 unexplained. Download commentary to retain the output.

Try these additional cases:

- No note: the draft requests investigation and makes no causal claim.
- Change the amount explained to 60000 and save: INR 40,000 remains unexplained.
- Describe two conflicting explanations in the same note and select **Conflicting explanations**: the full INR 100,000 remains unresolved.
- Choose **Unconfirmed**: the explanation is visible but does not reduce the unresolved amount.
- Switch to budget: the forecast explanation is not reused. Return to forecast to see the saved note.
- Use **Remove saved explanation** to remove the selected note.

## How notes behave

Each note has an ID and belongs to a specific month, country, account, currency, actual amount, comparison amount and comparison basis. One consolidated note is supported per record and basis; saving replaces that note. This is not an audit history. Reference labels are not fetched or independently verified. Confirmation is the reviewer's assertion, not automated verification. Amount coverage is arithmetic only: the app cannot detect contradictions within free text.

Notes remain in the current browser session, survive country and threshold changes, and clear when the input source, filename or contents change. They are not written to disk or GitHub. Download the commentary before refreshing, disconnecting or closing the app. The export includes saved notes for the currently selected material movements, not hidden or nonmaterial rows. There is no note import yet.

Amounts explained must be positive or zero and no greater than the absolute variance. Offsetting driver bridges are not supported. Favorable expense movements are not automatically treated as lasting savings. No AI service is connected.
