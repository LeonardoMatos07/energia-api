"""CSV and Excel files for the analyses."""

import csv
import io
import zipfile

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet._write_only import WriteOnlyWorksheet

from app.exporting.data import Analysis, Cell, Table, tables

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
UTF8_BOM = "﻿"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ZIP_MEDIA_TYPE = "application/zip"
MAX_SHEET_TITLE = 31


def safe_cell(value: Cell) -> Cell:
    """A text cell that starts like a formula would run as one when the file is opened, and some
    of these names come from public registries. Numbers are left alone."""
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return "'" + value
    return value


def to_csv(table: Table) -> bytes:
    """UTF-8 with a byte-order mark, which is what makes Excel read the accents right."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(table.headers)
    for row in table.rows:
        writer.writerow(["" if cell is None else safe_cell(cell) for cell in row])
    return (UTF8_BOM + out.getvalue()).encode("utf-8")


def _fill_sheet(sheet: WriteOnlyWorksheet, table: Table) -> None:
    """Written row by row, so the sheet of every plant in every month does not sit in memory."""
    for index, header in enumerate(table.headers, start=1):
        widest = max(
            [len(header)] + [len(str(row[index - 1])) for row in table.rows[:500] if row[index - 1]]
        )
        sheet.column_dimensions[get_column_letter(index)].width = min(max(widest + 2, 10), 60)
    sheet.freeze_panes = "A2"
    head: list[WriteOnlyCell] = []
    for header in table.headers:
        cell = WriteOnlyCell(sheet, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0E1B2C")
        head.append(cell)
    sheet.append(head)
    for row in table.rows:
        sheet.append([safe_cell(cell) for cell in row])


def to_xlsx(data: Analysis) -> bytes:
    """Every table of the analysis in one workbook, one sheet each."""
    workbook = Workbook(write_only=True)
    for table in tables(data).values():
        _fill_sheet(workbook.create_sheet(table.title[:MAX_SHEET_TITLE]), table)
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


def to_csv_zip(data: Analysis) -> bytes:
    """A CSV holds one table, so the complete set travels as one zip with a CSV per table."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for number, table in enumerate(tables(data).values(), start=1):
            archive.writestr(f"{number:02d}-{table.key}.csv", to_csv(table))
    return out.getvalue()
