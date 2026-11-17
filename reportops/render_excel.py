"""Excel output: the workbook stakeholders actually open. Formatted, frozen headers,
filters, conditional fills - no manual touch-up needed before it goes out."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(bold=True, color="FFFFFF")
FILLS = {
    "worse": PatternFill("solid", fgColor="F8D7DA"), "better": PatternFill("solid", fgColor="D4EDDA"),
    "ERROR": PatternFill("solid", fgColor="F8D7DA"), "BLOCKER": PatternFill("solid", fgColor="F1AEB5"),
    "WARN": PatternFill("solid", fgColor="FFF3CD"), "INFO": PatternFill("solid", fgColor="E2E3E5"),
    "REVIEW REQUIRED": PatternFill("solid", fgColor="FFF3CD"), "PUBLISHED": PatternFill("solid", fgColor="D4EDDA"),
}


def _table(ws, df: pd.DataFrame, start_row: int = 1, fill_col: str | None = None) -> int:
    for j, col in enumerate(df.columns, 1):
        c = ws.cell(row=start_row, column=j, value=str(col))
        c.fill, c.font = HEADER_FILL, HEADER_FONT
    for i, row in enumerate(df.itertuples(index=False), start_row + 1):
        for j, v in enumerate(row, 1):
            if isinstance(v, pd.Timestamp):
                v = v.to_pydatetime().date()
            elif isinstance(v, float) and pd.isna(v):
                v = None
            elif isinstance(v, list):
                v = ", ".join(map(str, v))
            c = ws.cell(row=i, column=j, value=v)
            if hasattr(v, "year"):
                c.number_format = "yyyy-mm-dd"
        if fill_col is not None:
            key = getattr(row, fill_col, None)
            if key in FILLS:
                for j in range(1, len(df.columns) + 1):
                    ws.cell(row=i, column=j).fill = FILLS[key]
    for j, col in enumerate(df.columns, 1):
        width = max([len(str(col))] + [len(str(v)) for v in df.iloc[:, j - 1].head(200)]) + 2
        ws.column_dimensions[get_column_letter(j)].width = min(width, 70)
    if len(df):
        ws.auto_filter.ref = f"A{start_row}:{get_column_letter(len(df.columns))}{start_row + len(df)}"
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    return start_row + len(df) + 2


def write_excel(r, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = f"IMO Issue Report - as of {r.asof}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Status: {r.status}"
    ws["A2"].fill = FILLS.get(r.status, FILLS["INFO"])
    ws["A3"] = f"Compared with: {r.prior_asof or 'no prior snapshot'} | Summary source: {r.summary_source}"
    row = 5
    for b in r.summary:
        ws.cell(row=row, column=1, value=f"- {b}").alignment = Alignment(wrap_text=False)
        row += 1
    kpi = pd.DataFrame([{"KPI": k["label"], "Current": k["current_fmt"], "Prior": k["prior_fmt"],
                         "Change": k["delta_fmt"], "trend": k["trend"]} for k in r.kpis])
    _table(ws, kpi, row + 1, fill_col="trend")
    ws.freeze_panes = None
    ws.column_dimensions["A"].width = 48

    _table(wb.create_sheet("By Workstream"), r.tables["workstreams"])
    aging = r.tables["aging"].reset_index().rename(columns={"workstream": "Workstream"})
    aging.columns = [str(c) for c in aging.columns]
    _table(wb.create_sheet("Aging"), aging)
    _table(wb.create_sheet("Overdue"), r.tables["overdue"])
    if len(r.slips):
        _table(wb.create_sheet("Target Date Slips"), r.slips)

    if r.explanations:
        ex_rows = []
        for e in r.explanations:
            if e.delta == 0 and not e.drivers:
                continue
            for d in e.drivers or [{"reason": "-", "count": 0, "impact": 0, "sample": ""}]:
                ex_rows.append({"KPI": e.label, "Prior": e.prior, "Current": e.current, "Change": e.delta,
                                "Driver": d["reason"], "Records": d["count"], "Impact": d["impact"], "Examples": d["sample"]})
        _table(wb.create_sheet("WoW Explainer"), pd.DataFrame(ex_rows))

    _table(wb.create_sheet("Reconciliation"), r.recon_table)
    fq = pd.DataFrame([f.to_dict() for f in r.findings]) if r.findings else pd.DataFrame(columns=["check"])
    _table(wb.create_sheet("Data Quality"), fq, fill_col="severity")
    wb.save(path)
    return path
