"""Data-quality checks. Each check returns a Finding with severity, count and sample keys.

Severity policy (also in docs/RUNBOOK.md):
    BLOCKER - report cannot be trusted at all; pipeline stops (raised as SchemaError upstream)
    ERROR   - a number in the report may be wrong; report is published with a REVIEW banner
              (or fails with --strict)
    WARN    - fixed automatically (e.g. exact duplicate rows removed); fix is logged
    INFO    - cosmetic, auto-normalized
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from . import config

SEVERITY_ORDER = {"BLOCKER": 0, "ERROR": 1, "WARN": 2, "INFO": 3}


@dataclass
class Finding:
    check: str
    severity: str
    source: str
    count: int
    detail: str
    sample_keys: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _f(check, severity, source, mask_or_keys, detail, keys_col=None, df=None) -> Finding | None:
    if isinstance(mask_or_keys, pd.Series):
        keys = df.loc[mask_or_keys, keys_col].astype(str).tolist()
    else:
        keys = list(mask_or_keys)
    if not keys:
        return None
    return Finding(check, severity, source, len(keys), detail, keys[:10])


def dedupe_register(df: pd.DataFrame) -> tuple[pd.DataFrame, list[Finding]]:
    """Remove exact duplicate rows (every business column equal) - the classic re-paste error."""
    business_cols = [c for c in df.columns if c != "source_row"]
    dup = df.duplicated(subset=business_cols, keep="first")
    findings = []
    if dup.any():
        rows = df.loc[dup, "source_row"].tolist()
        n_open = int(df.loc[dup, "is_open"].sum())
        findings.append(Finding(
            "register_exact_duplicate_rows", "WARN", "register", int(dup.sum()),
            f"Exact duplicate rows removed (Excel rows {rows}); open-issue count would have been overstated by {n_open}",
            df.loc[dup, "register_id"].tolist()[:10]))
    return df.loc[~dup].reset_index(drop=True), findings


def check_register(df: pd.DataFrame, asof: pd.Timestamp) -> list[Finding]:
    out = [
        _f("register_duplicate_id_conflict", "ERROR", "register", df["register_id"].duplicated(keep=False),
           "Same Register ID on rows with different values - which row is right?", "register_id", df),
        _f("register_unknown_status", "ERROR", "register", ~df["status"].isin(config.REGISTER_STATUSES),
           f"Status not in {config.REGISTER_STATUSES}", "register_id", df),
        _f("register_unknown_severity", "ERROR", "register", ~df["severity"].isin(config.SEVERITIES),
           f"Severity not in {config.SEVERITIES}", "register_id", df),
        _f("register_missing_jira_key", "ERROR", "register", df["jira_key"].isna(),
           "No Jira key - issue can't be traced to remediation work", "register_id", df),
        _f("register_open_without_target", "ERROR", "register", df["is_open"] & df["target"].isna(),
           "Open issue has no (parseable) target date", "register_id", df),
        _f("register_closed_without_date", "WARN", "register", (df["status"] == "Closed") & df["closed"].isna(),
           "Status Closed but Date Closed is blank", "register_id", df),
        _f("register_closed_before_raised", "ERROR", "register", df["closed"].notna() & df["raised"].notna() & (df["closed"] < df["raised"]),
           "Date Closed earlier than Date Raised", "register_id", df),
        _f("register_future_dates", "ERROR", "register", (df["raised"] > asof) | (df["closed"] > asof),
           "Raised/closed date after the report as-of date", "register_id", df),
    ]
    return [f for f in out if f]


def check_jira(df: pd.DataFrame, asof: pd.Timestamp) -> list[Finding]:
    out = [
        _f("jira_duplicate_keys", "ERROR", "jira", df["key"].duplicated(keep=False),
           "Issue key appears more than once in the export", "key", df),
        _f("jira_done_without_resolution", "WARN", "jira", ~df["is_open"] & df["resolved"].isna(),
           "Closed but no resolution date - excluded from time-to-close metrics (check workflow post-function)", "key", df),
        _f("jira_high_sev_without_target", "ERROR", "jira",
           df["is_open"] & df["severity"].isin(config.HIGH_SEVERITIES) & df["target"].isna(),
           "Open Critical/High issue with no due date (target remediation date)", "key", df),
        _f("jira_missing_severity", "ERROR", "jira", ~df["severity"].isin(config.SEVERITIES),
           "Severity field empty or not a known value", "key", df),
        _f("jira_unassigned_open", "INFO", "jira", df["is_open"] & df["owner"].isna(),
           "Open issue with no owner (assignee)", "key", df),
    ]
    return [f for f in out if f]


def extract_notes_to_findings(notes: list[dict]) -> list[Finding]:
    sev = {"target_date_text_coerced": "WARN", "status_normalized": "INFO", "severity_normalized": "INFO"}
    return [Finding(f"{n['source']}_{n['kind']}", sev.get(n["kind"], "INFO"), n["source"], n["count"], n["detail"], n["keys"])
            for n in notes]


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (SEVERITY_ORDER[f.severity], -f.count))
