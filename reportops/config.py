"""Central configuration: canonical values, SLA targets, field mappings.

Everything a maintainer is likely to tweak lives here, so a business-rule change
(e.g. a new remediation SLA) is a one-line, reviewable diff.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SNAPSHOT_DIR = ROOT / "snapshots"
OUTPUT_DIR = ROOT / "output"

WORKSTREAMS = [
    "Transaction Sourcing & Validation",
    "Firm Administration",
    "External Client Experience",
    "Distributions",
    "Hosting & Data Center Exit",
]
SEVERITIES = ["Critical", "High", "Medium", "Low"]
HIGH_SEVERITIES = ["Critical", "High"]

# Days from date raised to target remediation, by severity (IMO policy)
REMEDIATION_SLA_DAYS = {"Critical": 15, "High": 30, "Medium": 60, "Low": 90}
AGING_BUCKETS = [(0, 30, "0-30"), (31, 60, "31-60"), (61, 90, "61-90"), (91, 10_000, "90+")]

# --- Jira (system of record for day-to-day remediation) ---------------------
JIRA_PROJECT = "TAMI"
SEVERITY_FIELD = "customfield_10050"     # single-select {"value": "High"}
REGISTER_ID_FIELD = "customfield_10051"  # text, e.g. "IMO-0042"
JIRA_FIELDS = ["summary", "issuetype", "status", "priority", "assignee", "components", "labels",
               "created", "resolutiondate", "duedate", SEVERITY_FIELD, REGISTER_ID_FIELD]
JIRA_OPEN_STATUSES = ["Open", "In Remediation", "Pending Validation"]
JIRA_CLOSED_STATUSES = ["Closed", "Risk Accepted"]

# --- Excel issue register (IMO's SteerCo view) ------------------------------
REGISTER_SHEET = "Issue Register"
REGISTER_HEADER_MARKER = "Register ID"
REGISTER_COLUMNS = {
    "Register ID": "register_id",
    "Jira Key": "jira_key",
    "Issue Title": "title",
    "Workstream": "workstream",
    "Severity": "severity",
    "Exec Owner": "exec_owner",
    "Status": "status",
    "Regulatory / Client Impact": "regulatory",
    "Escalation Level": "escalation",
    "Date Raised": "raised",
    "Target Date": "target",
    "Date Closed": "closed",
    "Commentary": "commentary",
}
REGISTER_REQUIRED = ["register_id", "jira_key", "severity", "status", "target"]
REGISTER_STATUSES = ["Open", "In Remediation", "Pending Validation", "Closed", "Risk Accepted"]
REGISTER_OPEN_STATUSES = ["Open", "In Remediation", "Pending Validation"]
ESCALATION_LEVELS = ["Workstream", "Program", "SteerCo"]
