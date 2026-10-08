"""User-supplied explanations; no inferred causes or AI calls."""
import hashlib
import json
from decimal import Decimal, InvalidOperation
from finance import commentary

STATUSES = ('Unconfirmed', 'Confirmed by reviewer', 'Conflicting explanations')
TIMINGS = ('Unknown', 'One-off', 'Recurring', 'Timing difference')


def note_key(row, baseline):
    return tuple(str(row[k]) for k in ('month', 'country', 'account', 'currency', 'actual', baseline)) + (baseline,)


def make_note(row, baseline, reason, reference, amount, status, timing, action=''):
    if baseline not in ('budget', 'forecast'):
        raise ValueError('Choose budget or forecast.')
    reason, reference, action = reason.strip(), reference.strip(), action.strip()
    if not reason or not reference:
        raise ValueError('Enter a business explanation and a supporting reference.')
    if len(reason) > 2000 or len(reference) > 300 or len(action) > 1000:
        raise ValueError('The explanation, reference or action is too long.')
    if status not in STATUSES or timing not in TIMINGS:
        raise ValueError('Select a valid review status and timing.')
    try:
        value = Decimal(str(amount))
        if not value.is_finite() or value < 0 or value != value.quantize(Decimal('.01')):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise ValueError('Amount explained must be a nonnegative number with at most 2 decimals.') from None
    if value > abs(row['variance']):
        raise ValueError('Amount explained cannot exceed the absolute variance. Offsetting drivers are not supported yet.')
    key = note_key(row, baseline)
    identifier = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:12]
    return dict(key=key, id='NOTE-' + identifier, reason=reason, reference=reference,
                amount=value, status=status, timing=timing, action=action)


def accepted_amount(note):
    """Only reviewer-confirmed notes count toward explained variance."""
    return note['amount'] if note and note['status'] == 'Confirmed by reviewer' else Decimal('0')


def evidence_commentary(row, baseline, note=None):
    text = commentary(row, baseline)
    caution = (' Favorable spend does not by itself establish lasting savings; review timing and activity.'
               if row['variance'] < 0 else '')
    if note is None or tuple(note['key']) != note_key(row, baseline):
        return text + caution
    # Keep every supplied claim distinct from the engine's verified figures.
    text = text.replace('The input does not establish the cause. Confirm the explanation with the account owner.', '').rstrip()
    text += (f"\n[{note['id']}] Reference supplied by reviewer: {note['reference']}. "
             f"Review status: {note['status']}.\nSupplied explanation: {note['reason']} "
             f"Reported amount explained: INR {note['amount']:,.2f}. "
             f"Timing reported by reviewer: {note['timing']}.")
    accepted = accepted_amount(note)
    if note['status'] != 'Confirmed by reviewer':
        text += ' This explanation is unresolved and is not counted toward explained variance.'
    text += f" Unexplained amount: INR {abs(row['variance']) - accepted:,.2f}."
    if note['action']:
        text += f"\nProposed action supplied by reviewer: {note['action']}"
    return text + caution
