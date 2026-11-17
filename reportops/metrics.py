"""KPI definitions. Each metric is defined ONCE, as data, so that:

* the report, the snapshot and the variance explainer all use the same definition
* count metrics are *record-attributable* - the explainer can say exactly which
  issues moved a number week over week
* a definition change is a reviewable one-line diff (KPI governance)

Jira is the system of record for issue status and dates; the Excel register adds
the SteerCo view (escalation level, regulatory flag, exec owner).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from . import config

Mask = Callable[[pd.DataFrame, pd.Timestamp], pd.Series]


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    source: str              # "jira" | "register"
    key: str                 # record key column
    mask: Mask               # which records count toward the metric
    fmt: str = "int"
    good: str = "down"       # direction that is good: "up" | "down" | "none"
    definition: str = ""

    def compute(self, df: pd.DataFrame, asof: pd.Timestamp) -> float:
        return float(self.mask(df, asof).sum())


def _window7(col):
    """The 7 calendar days ending on (and including) the as-of date."""
    def mask(df, asof):
        end = asof + pd.Timedelta(days=1)
        return df[col].notna() & (df[col] >= end - pd.Timedelta(days=7)) & (df[col] < end)
    return mask


def age_days(df: pd.DataFrame, asof: pd.Timestamp) -> pd.Series:
    return (asof + pd.Timedelta(days=1) - df["created"]).dt.days


def overdue(df: pd.DataFrame, asof: pd.Timestamp) -> pd.Series:
    return df["is_open"] & df["target"].notna() & (df["target"] < asof)


def past_sla(df: pd.DataFrame, asof: pd.Timestamp, sla: dict[str, int] | None = None) -> pd.Series:
    """Open longer than the severity's remediation SLA, regardless of (re-baselined) target date."""
    limit = df["severity"].map(sla or config.REMEDIATION_SLA_DAYS)
    return df["is_open"] & (age_days(df, asof) > limit)


_open = lambda df, asof: df["is_open"]
_high = lambda df, asof: df["is_open"] & df["severity"].isin(config.HIGH_SEVERITIES)

METRICS: list[Metric] = [
    Metric("open_issues", "Open issues", "jira", "key", _open, definition="Jira statusCategory != done"),
    Metric("open_critical_high", "Open Critical/High", "jira", "key", _high,
           definition="Open issues with severity Critical or High"),
    Metric("overdue", "Overdue (past target date)", "jira", "key", overdue,
           definition="Open issues whose Jira due date is before the as-of date"),
    Metric("overdue_critical_high", "Overdue Critical/High", "jira", "key",
           lambda df, asof: overdue(df, asof) & df["severity"].isin(config.HIGH_SEVERITIES),
           definition="Overdue issues with severity Critical or High"),
    Metric("past_sla", "Open past remediation SLA", "jira", "key", past_sla,
           definition=f"Open issues older than the severity SLA {config.REMEDIATION_SLA_DAYS} days (ignores re-baselines)"),
    Metric("aged_90", "Open > 90 days", "jira", "key", lambda df, asof: df["is_open"] & (age_days(df, asof) > 90),
           definition="Open issues raised more than 90 days before as-of"),
    Metric("raised_7d", "Raised this week", "jira", "key", _window7("created"), good="none",
           definition="Created in the 7 days ending as-of"),
    Metric("closed_7d", "Closed this week", "jira", "key", _window7("resolved"), good="up",
           definition="Resolution date in the 7 days ending as-of"),
    Metric("steerco_open", "Open SteerCo escalations", "register", "register_id",
           lambda df, asof: df["is_open"] & (df["escalation"] == "SteerCo"),
           definition="Register: open and Escalation Level = SteerCo"),
    Metric("regulatory_open", "Open regulatory/client-impact", "register", "register_id",
           lambda df, asof: df["is_open"] & (df["regulatory"] == "Y"),
           definition="Register: open and Regulatory / Client Impact = Y"),
]
METRICS_BY_ID = {m.id: m for m in METRICS}

EXTRA_LABELS = {
    "median_days_to_close_30d": ("Median days to close (closed last 30d)", "days", "down"),
    "net_burn_7d": ("Net burn this week (closed - raised)", "int", "up"),
}


def non_additive(frames: dict[str, pd.DataFrame], asof: pd.Timestamp) -> dict[str, float]:
    """Reported, but not record-attributable."""
    j = frames["jira"]
    closed30 = j[j["resolved"].notna() & (j["resolved"] > asof - pd.Timedelta(days=30))]
    ttc = (closed30["resolved"] - closed30["created"]).dt.total_seconds() / 86400
    raised = METRICS_BY_ID["raised_7d"].compute(j, asof)
    closed = METRICS_BY_ID["closed_7d"].compute(j, asof)
    return {
        "median_days_to_close_30d": round(float(ttc.median()), 1) if len(ttc) else float("nan"),
        "net_burn_7d": closed - raised,
    }


def compute_all(frames: dict[str, pd.DataFrame], asof: pd.Timestamp) -> dict[str, float]:
    values = {m.id: m.compute(frames[m.source], asof) for m in METRICS}
    values.update(non_additive(frames, asof))
    return values


def label_of(metric_id: str) -> tuple[str, str, str]:
    if metric_id in METRICS_BY_ID:
        m = METRICS_BY_ID[metric_id]
        return m.label, m.fmt, m.good
    return EXTRA_LABELS[metric_id]


def fmt_value(v: float, fmt: str) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    if fmt == "days":
        return f"{v:,.1f}"
    return f"{v:,.0f}"


# ---------------------------------------------------------------- breakdown tables
def aging_table(j: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """Open issues by workstream x aging bucket - the classic IMO heat map."""
    o = j[j["is_open"]].copy()
    ages = age_days(o, asof)
    o["bucket"] = pd.cut(ages, bins=[b[0] - 1 for b in config.AGING_BUCKETS] + [10_000],
                         labels=[b[2] for b in config.AGING_BUCKETS])
    t = pd.crosstab(o["workstream"], o["bucket"]).reindex(index=config.WORKSTREAMS, fill_value=0)
    t = t.reindex(columns=[b[2] for b in config.AGING_BUCKETS], fill_value=0)
    t["Total"] = t.sum(axis=1)
    t.loc["Total"] = t.sum()
    return t


def workstream_table(j: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for ws in config.WORKSTREAMS:
        d = j[j["workstream"] == ws]
        rows.append({
            "Workstream": ws,
            "Open": int(d["is_open"].sum()),
            "Critical/High": int(_high(d, asof).sum()),
            "Overdue": int(overdue(d, asof).sum()),
            "Past SLA": int(past_sla(d, asof).sum()),
            "Raised 7d": int(_window7("created")(d, asof).sum()),
            "Closed 7d": int(_window7("resolved")(d, asof).sum()),
        })
    return pd.DataFrame(rows)


def overdue_list(j: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    o = j[overdue(j, asof)].copy()
    o["days_overdue"] = (asof - o["target"]).dt.days
    o["severity_rank"] = o["severity"].map({s: i for i, s in enumerate(config.SEVERITIES)})
    cols = ["key", "register_id", "severity", "workstream", "summary", "owner", "status", "target", "days_overdue"]
    return o.sort_values(["severity_rank", "days_overdue"], ascending=[True, False])[cols]
