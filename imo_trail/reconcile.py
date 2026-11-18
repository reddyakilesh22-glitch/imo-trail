"""Cross-source reconciliation: does the IMO's Excel register agree with Jira?

Workstreams update Jira every day; the IMO register is updated by hand before
SteerCo. They drift, and SteerCo sees the register. These checks find the drift
before a stakeholder does, and say which side is probably wrong.
"""
from __future__ import annotations

import pandas as pd

from . import config
from .quality import Finding

CHECK_TEXT = {
    "key_not_in_jira": ("ERROR", "Register row points to a Jira key that doesn't exist - typo?"),
    "missing_from_register": ("ERROR", "Critical/High issue in Jira has no register row - add it before SteerCo"),
    "status_register_closed_jira_open": ("ERROR", "Register says Closed but Jira is still open - closed too early?"),
    "status_jira_closed_register_open": ("WARN", "Closed in Jira but register not updated"),
    "severity_mismatch": ("ERROR", "Severity differs between register and Jira - agree a rating"),
    "target_date_mismatch": ("WARN", "Target date differs - register likely missed a Jira re-baseline"),
    "workstream_mismatch": ("WARN", "Workstream differs between register and Jira"),
}


def reconcile(register: pd.DataFrame, jira: pd.DataFrame) -> tuple[list[Finding], pd.DataFrame]:
    """Return findings plus a row-level breaks table for the report."""
    j = jira.drop_duplicates("key").set_index("key")
    rows: list[dict] = []

    def add(check, reg_id, key, reg_val, jira_val):
        rows.append({"check": check, "register_id": reg_id, "jira_key": key, "register_value": reg_val,
                     "jira_value": jira_val, "suggested_action": CHECK_TEXT[check][1]})

    for _, r in register.iterrows():
        key = r["jira_key"]
        if key is None:
            continue  # reported by quality checks
        if key not in j.index:
            add("key_not_in_jira", r["register_id"], key, key, None)
            continue
        ji = j.loc[key]
        if not r["is_open"] and r["status"] == "Closed" and ji["is_open"]:
            add("status_register_closed_jira_open", r["register_id"], key, r["status"], ji["status"])
        elif r["is_open"] and not ji["is_open"]:
            add("status_jira_closed_register_open", r["register_id"], key, r["status"], ji["status"])
        if r["severity"] != ji["severity"]:
            add("severity_mismatch", r["register_id"], key, r["severity"], ji["severity"])
        if r["is_open"] and ji["is_open"] and pd.notna(r["target"]) and pd.notna(ji["target"]) \
                and r["target"].normalize() != ji["target"].normalize():
            add("target_date_mismatch", r["register_id"], key, r["target"].date().isoformat(), ji["target"].date().isoformat())
        if r["workstream"] and r["workstream"] != ji["workstream"]:
            add("workstream_mismatch", r["register_id"], key, r["workstream"], ji["workstream"])

    in_register = set(register["jira_key"].dropna())
    missing = jira[jira["is_open"] & jira["severity"].isin(config.HIGH_SEVERITIES) & ~jira["key"].isin(in_register)]
    for _, ji in missing.iterrows():
        add("missing_from_register", ji["register_id"], ji["key"], None, f"{ji['severity']} / {ji['status']}")

    table = pd.DataFrame(rows, columns=["check", "register_id", "jira_key", "register_value", "jira_value", "suggested_action"])
    findings = []
    for check, grp in table.groupby("check", sort=False):
        sev, text = CHECK_TEXT[check]
        findings.append(Finding(f"recon_{check}", sev, "register+jira", len(grp), text,
                                [f"{a or '-'}/{b}" for a, b in zip(grp["register_id"], grp["jira_key"])][:10]))
    return findings, table
