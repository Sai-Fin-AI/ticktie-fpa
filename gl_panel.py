"""GL mapping screens: the mapping workbook (the master, with any new GL codes added) and the GL code tables."""
import io

import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from finance import GL, PL_LINES, dim_label, gl_detail, gl_lines
from style import colour_table, money_columns

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
NEW = 'New: fill in the account'


def mapping_workbook(gl_map, present, new=()):
    """The GL mapping as an Excel file that uploads straight back.

    New codes come first with the account left blank, then the mapping in use. Status says Mapped, New, or
    No rows this month. GL codes are text cells, so leading zeros survive; account and P&L line have dropdowns.
    """
    book = Workbook()
    sheet = book.active
    sheet.title = 'GL mapping'
    sheet.append((GL, 'gl_name', 'account', 'pl_line', 'status'))
    present = set(present)
    for code in new:
        sheet.append((code, '', '', '', NEW))
    for code, entry in sorted(gl_map.items()):
        sheet.append((code, entry['gl_name'], entry['account'], entry.get('pl_line', ''),
                      'Mapped' if code in present else 'No rows this month'))
    header, fill = PatternFill('solid', fgColor='13294B'), PatternFill('solid', fgColor='FEF0C7')
    for cell in sheet[1]:
        cell.font, cell.fill = Font(bold=True, color='FFFFFF'), header
    for row in sheet.iter_rows(min_row=2):
        row[0].number_format = '@'
        if row[4].value == NEW:
            for cell in row:
                cell.fill = fill
    for column, width in zip('ABCDE', (14, 34, 26, 30, 24)):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = 'A2'
    last = max(sheet.max_row, 2) + 200  # room for codes added by hand
    lists = book.create_sheet('Lists')
    accounts = sorted({e['account'] for e in gl_map.values()})
    for i, account in enumerate(accounts, 1):
        lists.cell(i, 1, account)
    lists.sheet_state = 'hidden'
    if accounts:  # a pick list of known accounts; typing a new account is still allowed
        pick = DataValidation(type='list', formula1=f'=Lists!$A$1:$A${len(accounts)}', showErrorMessage=False)
        sheet.add_data_validation(pick)
        pick.add(f'C2:C{last}')
    lines = DataValidation(type='list', formula1='"' + ','.join(PL_LINES) + '"', allow_blank=True)
    sheet.add_data_validation(lines)
    lines.add(f'D2:D{last}')
    notes = book.create_sheet('How to use')
    for text in ('One row per GL code. gl_code and account are required; gl_name and pl_line are optional.',
                 'account: the reporting account the GL code rolls up to. Several GL codes can share one account.',
                 'pl_line: where the account sits on the P&L (' + ', '.join(PL_LINES) + '). Blank: read from the '
                 'account name, otherwise Operating expenses. All GL codes of an account sit on one line.',
                 'Yellow rows are GL codes in the data that the mapping does not know yet: fill in their account.',
                 'The status column is for information and is ignored on upload. Save and upload this file in the sidebar.'):
        notes.append((text,))
    notes.column_dimensions['A'].width = 120
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def _table(items, grain, baseline):
    frame = pd.DataFrame([{'Country': g['country'], **{dim_label(d): g[d] for d in grain}, 'Account': g['account'],
                           'GL code': g[GL], 'GL name': g['gl_name'], 'Actual INR': float(g['actual']),
                           f'{baseline.title()} INR': float(g[baseline]), 'Variance INR': float(g['variance']),
                           'Variance %': f"{g['pct']:.1f}%" if g['pct'] is not None else 'N/A', 'Outcome': g['direction']}
                          for g in items])
    st.dataframe(colour_table(frame) if len(frame) else frame, width='stretch', hide_index=True,
                 column_config=money_columns(frame))


def gl_codes_view(dataset, grain, baseline, in_view):
    """Variance detail by GL code: every GL code behind the lines, largest variance first."""
    _table([g for g in gl_lines(dataset, grain, baseline) if in_view(g)], grain, baseline)
    st.caption('Each GL code at the chosen level, largest variance first. A credit GL code (for example a recharge '
               'or recovery) can be negative.')


def gl_breakdown(dataset, line, baseline):
    """The GL codes inside the line being explained, so the owner sees which postings drive it."""
    parts = gl_detail(dataset, line, baseline)
    with st.expander(f'GL codes in this line ({len(parts)})', expanded=True):
        _table(parts, (), baseline)
