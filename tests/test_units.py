from datetime import date

import pandas as pd
import pytest
from openpyxl import Workbook

from imo_trail import detective, narrative, quality, reconcile, sources
from imo_trail.metrics import METRICS_BY_ID

ASOF = pd.Timestamp("2026-11-13")


# ------------------------------------------------------------------ helpers
def jira_frame(rows):
    base = {"summary": "x", "issue_type": "Issue", "priority": "High", "owner": "A", "workstream": "Distributions",
            "labels": "", "register_id": None, "resolved": pd.NaT, "status": "In Remediation",
            "status_category": "indeterminate", "severity": "High", "created": pd.Timestamp("2026-10-01"),
            "target": pd.Timestamp("2026-12-01")}
    df = pd.DataFrame([{**base, **r} for r in rows])
    for c in ("created", "resolved", "target"):
        df[c] = pd.to_datetime(df[c])
    df["is_open"] = df["status_category"] != "done"
    return df


def write_register(path, rows, header=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Issue Register"
    ws.append(["Title row"])
    ws.append([])
    header = header or ["Register ID", "Jira Key", "Workstream", "Severity", "Status", "Escalation Level",
                        "Regulatory / Client Impact", "Date Raised", "Target Date", "Date Closed"]
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


# ------------------------------------------------------------------ extract
def test_register_header_detection_and_coercion(tmp_path):
    p = write_register(tmp_path / "r.xlsx", [
        ["IMO-1", "TAMI-1", "Distributions", "high", "in remediation ", "Program", "N", date(2026, 10, 1), "12/15/2026", None],
        ["IMO-2", "TAMI-2", "Distributions", "Low", "Closed", "Workstream", "N", date(2026, 9, 1), date(2026, 10, 1), date(2026, 10, 2)],
    ])
    log = sources.ExtractLog()
    df = sources.load_register(p, log)
    assert list(df["status"]) == ["In Remediation", "Closed"]
    assert df.loc[0, "severity"] == "High"
    assert df.loc[0, "target"] == pd.Timestamp("2026-12-15")
    assert df.loc[0, "source_row"] == 4          # traceable back to the Excel row
    kinds = {n["kind"] for n in log.notes}
    assert {"status_normalized", "severity_normalized", "target_date_text_coerced"} <= kinds


def test_renamed_header_is_a_blocker(tmp_path):
    p = write_register(tmp_path / "r.xlsx", [["IMO-1", "TAMI-1", "x", "High", "Open", "", "", None, None, None]],
                       header=["Register ID", "Jira Key", "Workstream", "Sev", "Status", "Escalation Level",
                               "Regulatory / Client Impact", "Date Raised", "Target Date", "Date Closed"])
    with pytest.raises(sources.SchemaError, match="Severity"):
        sources.load_register(p, sources.ExtractLog())


# ------------------------------------------------------------------ quality
def test_exact_duplicates_removed_but_conflicts_flagged():
    df = pd.DataFrame({"register_id": ["IMO-1", "IMO-1", "IMO-2", "IMO-2"], "status": ["Open", "Open", "Open", "Closed"],
                       "is_open": [True, True, True, False], "source_row": [5, 6, 7, 8]})
    out, findings = quality.dedupe_register(df)
    assert len(out) == 3 and findings[0].count == 1
    assert "Excel rows [6]" in findings[0].detail


def test_high_severity_without_target_is_error():
    j = jira_frame([{"key": "TAMI-1", "target": None}, {"key": "TAMI-2", "severity": "Low", "target": None}])
    f = {x.check: x for x in quality.check_jira(j, ASOF)}
    assert f["jira_high_sev_without_target"].sample_keys == ["TAMI-1"]


# ------------------------------------------------------------------ reconcile
def test_reconcile_finds_each_break_type():
    j = jira_frame([
        {"key": "TAMI-1"},                                                     # register says Closed
        {"key": "TAMI-2", "status": "Closed", "status_category": "done"},      # register says open
        {"key": "TAMI-3", "severity": "Medium"},                               # severity mismatch
        {"key": "TAMI-4", "severity": "Critical"},                             # missing from register
    ])
    reg = pd.DataFrame([
        {"register_id": "IMO-1", "jira_key": "TAMI-1", "status": "Closed", "is_open": False, "severity": "High", "target": pd.Timestamp("2026-12-01"), "workstream": "Distributions"},
        {"register_id": "IMO-2", "jira_key": "TAMI-2", "status": "Open", "is_open": True, "severity": "High", "target": pd.Timestamp("2026-12-01"), "workstream": "Distributions"},
        {"register_id": "IMO-3", "jira_key": "TAMI-3", "status": "Open", "is_open": True, "severity": "High", "target": pd.Timestamp("2026-12-08"), "workstream": "Distributions"},
        {"register_id": "IMO-9", "jira_key": "TAMI-999", "status": "Open", "is_open": True, "severity": "High", "target": pd.Timestamp("2026-12-01"), "workstream": "Distributions"},
    ])
    _, table = reconcile.reconcile(reg, j)
    got = set(zip(table["check"], table["jira_key"]))
    assert got == {("status_register_closed_jira_open", "TAMI-1"), ("status_jira_closed_register_open", "TAMI-2"),
                   ("severity_mismatch", "TAMI-3"), ("target_date_mismatch", "TAMI-3"),
                   ("key_not_in_jira", "TAMI-999"), ("missing_from_register", "TAMI-4")}


# ------------------------------------------------------------------ detective
def test_explainer_separates_time_from_data_changes():
    prior_asof = ASOF - pd.Timedelta(days=7)
    prior = jira_frame([
        {"key": "A", "target": "2026-11-10"},   # not overdue on 11-06; overdue by 11-13 purely from time
        {"key": "B", "target": "2026-11-01"},   # overdue, then target pushed -> leaves via data change
        {"key": "C", "target": "2026-11-01"},   # overdue, then closed
    ])
    cur = jira_frame([
        {"key": "A", "target": "2026-11-10"},
        {"key": "B", "target": "2026-12-15"},
        {"key": "C", "target": "2026-11-01", "status": "Closed", "status_category": "done"},
        {"key": "D", "target": "2026-11-11", "created": "2026-11-12"},   # new and already overdue
    ])
    e = detective.explain_metric(METRICS_BY_ID["overdue"], prior, prior_asof, cur, ASOF)
    reasons = dict(zip(e.records["key"], e.records["reason"]))
    assert reasons == {"A": "time passed (crossed date threshold)", "B": "target date moved later",
                       "C": "status In Remediation -> Closed", "D": "newly raised"}
    assert sum(d["impact"] for d in e.drivers) == e.delta   # drivers add up to the change


def test_target_date_slips():
    prior = jira_frame([{"key": "A", "target": "2026-11-20"}, {"key": "B", "target": "2026-11-20"}])
    cur = jira_frame([{"key": "A", "target": "2026-12-04"}, {"key": "B", "target": "2026-11-20"}])
    s = detective.target_date_slips(prior, cur)
    assert list(s["key"]) == ["A"] and s.loc[0, "days_slipped"] == 14


# ------------------------------------------------------------------ AI guardrail
def test_guardrail_rejects_numbers_not_in_facts():
    facts = {"as_of": "2026-11-13", "kpis": [{"metric": "Open issues", "current": "94", "prior": "87", "change": "+7"}]}
    assert narrative.ungrounded_numbers("94 open issues, up 7 from 87.", facts) == []
    assert narrative.ungrounded_numbers("94 open issues, an 8% increase.", facts) == ["8%"]
    assert narrative.ungrounded_numbers("Closed 1,250 issues", facts) == ["1,250"]
