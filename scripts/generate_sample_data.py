"""Generate synthetic, deterministic sample data for two weekly IMO report runs.

Scenario: an Issue Management Office (IMO) for a fund transfer-agency (TA)
modernization program. Workstreams log and remediate issues in Jira; the IMO keeps
an Excel issue register for the steering committee. Every week someone builds the
SteerCo issue report from both.

Writes, for each as-of date:
    data/<asof>/jira_issues.json      - shaped like a Jira Cloud REST search response
    data/<asof>/issue_register.xlsx   - hand-maintained Excel register (title rows, SLA sheet)

All names, IDs and descriptions are fictional. Each issue has a lifecycle (raised,
remediation start, validation, close, target-date re-baselines), and each export
is derived from that lifecycle as of the export date, so the two weeks are
consistent - like real exports.

The CURRENT export also contains planted, realistic defects (duplicate rows from a
re-paste, dates typed as text, status casing, register/Jira drift, a Jira key typo,
High issues never added to the register, ...) so the pipeline has something to catch.

Usage:  python scripts/generate_sample_data.py
"""
from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

ROOT = Path(__file__).resolve().parent.parent
PRIOR = date(2026, 11, 6)
CURRENT = date(2026, 11, 13)
START = date(2026, 5, 4)

rng = random.Random(7)

WORKSTREAMS = {
    "Transaction Sourcing & Validation": [
        "Fund/SERV trade rejects not routed to exception queue",
        "As-of trade correction requires manual repricing",
        "Duplicate order check misses omnibus sub-accounts",
        "Cutoff-time validation fails for late-day intermediary trades",
        "Intermediary trade file arrives without CUSIP on some records",
        "Exchange (switch) orders split incorrectly across funds",
    ],
    "Firm Administration": [
        "Dealer agreement flag missing on converted firms",
        "Broker-dealer branch hierarchy not migrated",
        "Rep code mapping mismatch after conversion",
        "Networking level not set on new intermediary accounts",
        "Firm onboarding checklist not enforced in new platform",
    ],
    "External Client Experience": [
        "Intermediary portal shows stale transaction status",
        "Trade confirmation wording differs from legacy",
        "Advisor account search slower than SLA",
        "Accessibility defects on firm self-service pages",
        "Statement PDF missing fund footnotes",
    ],
    "Distributions": [
        "Dividend reinvestment share rounding differs from legacy",
        "Capital gains distribution file late to intermediaries",
        "Record-date eligibility logic gap for pending transfers",
        "12b-1 trail calculation variance vs legacy",
        "Distribution rate file not validated before release",
    ],
    "Hosting & Data Center Exit": [
        "Nightly TA batch still depends on legacy data center job",
        "Failover test not completed for validation service",
        "Mainframe extract decommission blocked by downstream consumer",
        "Cloud network latency on trade validation calls",
        "Legacy file transfer server still in use by two firms",
    ],
}
WS_WEIGHTS = [0.30, 0.18, 0.17, 0.20, 0.15]
SEV = {"Critical": 0.07, "High": 0.25, "Medium": 0.45, "Low": 0.23}
SLA = {"Critical": 15, "High": 30, "Medium": 60, "Low": 90}
PRIORITY_FOR = {"Critical": "Highest", "High": "High", "Medium": "Medium", "Low": "Low"}
TYPES = {"Issue": 0.55, "Risk": 0.18, "Defect": 0.22, "Action Item": 0.05}
OWNERS = ["Dana Kim", "Marcus Hill", "Priya Nair", "Tom Alvarez", "Grace Liu", "Owen Park", "Nia Brooks"]
EXECS = ["VP Transaction Processing", "VP Intermediary Services", "VP Distributions", "Head of TA Technology"]
COMMENTS = ["Fix in test; validation planned next sprint", "Dependency on vendor patch",
            "Workaround in place - manual check by ops", "Root cause confirmed; design change approved",
            "Awaiting business sign-off on approach", ""]


def wchoice(d):
    return rng.choices(list(d), weights=list(d.values()))[0]


@dataclass
class Issue:
    n: int
    key: str
    itype: str
    workstream: str
    summary: str
    severity: str
    owner: str | None
    raised: datetime
    remediation_start: datetime
    validation_start: datetime | None
    closed: datetime | None
    risk_accepted: bool
    base_target: date
    slips: list = field(default_factory=list)      # [(effective_date, new_target)]
    regulatory: bool = False
    register_id: str | None = None
    exec_owner: str = ""
    comment: str = ""

    def status_on(self, asof: date) -> tuple[str, str]:
        end = datetime.combine(asof, datetime.max.time())
        if self.closed and self.closed <= end:
            return ("Risk Accepted" if self.risk_accepted else "Closed"), "done"
        if self.validation_start and self.validation_start <= end:
            return "Pending Validation", "indeterminate"
        if self.remediation_start <= end:
            return "In Remediation", "indeterminate"
        return "Open", "new"

    def target_on(self, asof: date) -> date:
        t = self.base_target
        for eff, new in self.slips:
            if eff <= asof:
                t = new
        return t


def at_time(d: date) -> datetime:
    return datetime.combine(d, datetime.min.time()) + timedelta(hours=rng.randint(8, 17), minutes=rng.randint(0, 59))


def make_issues(n: int = 260) -> list[Issue]:
    issues = []
    span = (CURRENT - START).days
    for i in range(n):
        ws = rng.choices(list(WORKSTREAMS), weights=WS_WEIGHTS)[0]
        sev = wchoice(SEV)
        raised = at_time(START + timedelta(days=rng.randint(0, span)))
        remediation = raised + timedelta(days=rng.randint(0, 7))
        sla = SLA[sev]
        duration = rng.randint(int(sla * 0.3), int(sla * 1.6)) if rng.random() < (0.55 if sev == "Critical" else 0.85) else rng.randint(sla * 2, sla * 5)
        closed = remediation + timedelta(days=duration)
        validation = closed - timedelta(days=rng.randint(3, 14))
        target = (raised + timedelta(days=sla + rng.randint(-3, 5))).date()
        iss = Issue(
            n=i, key=f"TAMI-{1001 + i}", itype=wchoice(TYPES), workstream=ws,
            summary=rng.choice(WORKSTREAMS[ws]), severity=sev,
            owner=rng.choice(OWNERS) if rng.random() > 0.04 else None,
            raised=raised, remediation_start=remediation, validation_start=max(validation, remediation),
            closed=closed, risk_accepted=rng.random() < 0.05, base_target=target,
            regulatory=rng.random() < 0.22, exec_owner=rng.choice(EXECS), comment=rng.choice(COMMENTS),
        )
        # Target re-baselines: when an issue runs late, the owner pushes the date
        if closed.date() > target and rng.random() < 0.6:
            eff = target - timedelta(days=rng.randint(0, 5))
            iss.slips.append((eff, target + timedelta(days=rng.choice([7, 14, 21, 30]))))
            if rng.random() < 0.35:
                eff2 = iss.slips[-1][1] - timedelta(days=rng.randint(0, 4))
                iss.slips.append((eff2, iss.slips[-1][1] + timedelta(days=rng.choice([14, 30]))))
        issues.append(iss)
    # Force a handful of re-baselines to land in the reporting week (realistic: SteerCo prep week)
    week = [x for x in issues if x.status_on(PRIOR)[1] != "done" and x.status_on(CURRENT)[1] != "done"]
    for x in rng.sample(week, 7):
        x.slips.append((PRIOR + timedelta(days=rng.randint(1, 6)), x.target_on(PRIOR) + timedelta(days=rng.choice([14, 21, 30, 45]))))
    # Register: every Critical/High issue, regulatory Mediums, and ~25% of the rest
    seq = 1
    for x in sorted(issues, key=lambda x: x.raised):
        if x.severity in ("Critical", "High") or (x.severity == "Medium" and x.regulatory) or rng.random() < 0.25:
            x.register_id = f"IMO-{seq:04d}"
            seq += 1
    return issues


def jira_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000-0500")


def issue_json(x: Issue, asof: date) -> dict:
    status, cat = x.status_on(asof)
    end = datetime.combine(asof, datetime.max.time())
    labels = (["regulatory"] if x.regulatory else []) + (["steerco"] if x.severity == "Critical" else [])
    return {"key": x.key, "fields": {
        "summary": x.summary,
        "issuetype": {"name": x.itype},
        "status": {"name": status, "statusCategory": {"key": cat}},
        "priority": {"name": PRIORITY_FOR[x.severity]},
        "assignee": {"displayName": x.owner} if x.owner else None,
        "components": [{"name": x.workstream}],
        "labels": labels,
        "created": jira_ts(x.raised),
        "resolutiondate": jira_ts(x.closed) if x.closed and x.closed <= end else None,
        "duedate": x.target_on(asof).isoformat(),
        "customfield_10050": {"value": x.severity},
        "customfield_10051": x.register_id,
    }}


def write_jira(issues: list[Issue], asof: date, out: Path, defects: bool) -> dict:
    end = datetime.combine(asof, datetime.max.time())
    visible = [x for x in issues if x.raised <= end]
    payload = [issue_json(x, asof) for x in visible]
    planted = {}
    if defects:
        closed = [p for p in payload if p["fields"]["status"]["name"] == "Closed"]
        closed[4]["fields"]["resolutiondate"] = None
        planted["closed_no_resolution"] = closed[4]["key"]
        crit = [p for p in payload if p["fields"]["customfield_10050"]["value"] in ("Critical", "High") and p["fields"]["status"]["statusCategory"]["key"] != "done"]
        crit[0]["fields"]["duedate"] = None
        planted["high_sev_no_target"] = crit[0]["key"]
    body = {"startAt": 0, "maxResults": len(payload), "total": len(payload), "issues": payload}
    (out / "jira_issues.json").write_text(json.dumps(body, indent=1))
    return planted


def write_register(issues: list[Issue], asof: date, out: Path, defects: bool) -> dict:
    end = datetime.combine(asof, datetime.max.time())
    rows = []
    for x in issues:
        if not x.register_id or x.raised > end:
            continue
        status, cat = x.status_on(asof)
        rows.append({
            "Register ID": x.register_id, "Jira Key": x.key, "Issue Title": x.summary,
            "Workstream": x.workstream, "Severity": x.severity, "Exec Owner": x.exec_owner,
            "Status": status, "Regulatory / Client Impact": "Y" if x.regulatory else "N",
            "Escalation Level": {"Critical": "SteerCo", "High": "Program"}.get(x.severity, "Workstream"),
            "Date Raised": x.raised.date(), "Target Date": x.target_on(asof),
            "Date Closed": x.closed.date() if cat == "done" else None, "Commentary": x.comment,
        })
    planted = {}
    if defects:
        used: set[str] = set()

        def take(pool, n):
            """Pick n distinct rows not already used by another defect (defects must not overlap)."""
            picked = [r for r in pool if r["Register ID"] not in used][:n]
            used.update(r["Register ID"] for r in picked)
            return picked

        is_open = lambda r: r["Status"] in ("Open", "In Remediation", "Pending Validation")
        open_rows = [r for r in rows if is_open(r)]
        by_key = {x.key: x for x in issues}
        # 1) Two High issues raised recently, still open, never added to the register
        new_high = [r for r in open_rows if r["Severity"] == "High" and r["Date Raised"] > asof - timedelta(days=21)]
        drop = take(new_high, 2)
        rows = [r for r in rows if r not in drop]
        open_rows = [r for r in open_rows if r not in drop]
        planted["missing_from_register"] = [r["Jira Key"] for r in drop]
        # 2) Register target date stale after a Jira re-baseline this week
        slipped = [r for r in open_rows if any(PRIOR < eff <= asof for eff, _ in by_key[r["Jira Key"]].slips)]
        for r in take(slipped, 1):
            r["Target Date"] = by_key[r["Jira Key"]].target_on(PRIOR)
            planted["stale_target"] = r["Register ID"]
        # 3) Jira closed this week, register never updated
        recent = [r for r in rows if r["Status"] == "Closed" and r["Date Closed"] and r["Date Closed"] > asof - timedelta(days=7)]
        stale_open = take(recent, 2)
        for r in stale_open:
            r["Status"], r["Date Closed"] = "In Remediation", None
        planted["register_not_updated"] = [r["Register ID"] for r in stale_open]
        # 4) Register says Closed, Jira still open (closed too early in the register)
        early = take(open_rows, 2)
        for r in early:
            r["Status"], r["Date Closed"] = "Closed", asof - timedelta(days=2)
        planted["closed_too_early"] = [r["Register ID"] for r in early]
        # 5) Severity disagrees with Jira
        sev = take(open_rows, 2)
        for r in sev:
            r["Severity"] = "Medium" if r["Severity"] != "Medium" else "High"
        planted["severity_mismatch"] = [r["Register ID"] for r in sev]
        # 6) Jira key typo
        typo = take(open_rows, 1)[0]
        typo["Jira Key"] = "TAMI-9" + typo["Jira Key"][-3:]
        planted["key_typo"] = typo["Register ID"]
        # 7) Status entered with inconsistent casing / spaces
        casing = take(open_rows, 2)
        casing[0]["Status"], casing[1]["Status"] = casing[0]["Status"].lower() + " ", casing[1]["Status"].upper()
        planted["status_casing"] = [r["Register ID"] for r in casing]
        # 8) Target dates typed as text
        text = take(open_rows, 2)
        for r in text:
            r["Target Date"] = r["Target Date"].strftime("%m/%d/%Y")
        planted["target_as_text"] = [r["Register ID"] for r in text]
        # 9) Three rows pasted twice while re-sorting the sheet (exact copies)
        dups = take(open_rows, 3)
        rows.extend(dict(r) for r in dups)
        planted["duplicates"] = [r["Register ID"] for r in dups]

    wb = Workbook()
    ws = wb.active
    ws.title = "Issue Register"
    ws["A1"] = "TA Modernization Program - IMO Issue Register"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Snapshot {asof.isoformat()} - maintained by the IMO. Do not rename column headers."
    headers = list(rows[0].keys())
    ws.append([])
    ws.append(headers)
    for c in ws[4]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([r[h] for h in headers])
    for row in ws.iter_rows(min_row=5):
        for c in row:
            if isinstance(c.value, date):
                c.number_format = "yyyy-mm-dd"
    sla = wb.create_sheet("Severity SLA")
    sla.append(["Severity", "Days to Remediate"])
    for k, v in SLA.items():
        sla.append([k, v])
    wb.save(out / "issue_register.xlsx")
    return planted


def main() -> int:
    issues = make_issues()
    for asof, defects in [(PRIOR, False), (CURRENT, True)]:
        out = ROOT / "data" / asof.isoformat()
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*"):
            old.unlink()
        rng.seed(1000 + asof.toordinal())
        planted = write_jira(issues, asof, out, defects)
        planted.update(write_register(issues, asof, out, defects))
        print(f"wrote {out.relative_to(ROOT)}" + (f"  planted: {planted}" if planted else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
