"""Variance explainer ("why did this number change since last week?").

Because every KPI is defined as a record filter, the change in a KPI can be
explained exactly: compare which records were in the metric last run vs this run,
and say *why* each moved (new issue, status change, severity change, target date
moved, aged into the window...). The drivers are checked to add up to the delta.

Also: target-date slippage - the number one thing an Issue Management Office is
asked about, and invisible in a point-in-time report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .metrics import METRICS, Metric, fmt_value


@dataclass
class Explanation:
    metric_id: str
    label: str
    prior: float
    current: float
    fmt: str
    drivers: list[dict] = field(default_factory=list)   # grouped reasons, largest first
    records: pd.DataFrame | None = None                 # record-level detail

    @property
    def delta(self) -> float:
        return self.current - self.prior

    def headline(self) -> str:
        sign = "+" if self.delta >= 0 else "-"
        parts = [f"{d['reason']} ({'+' if d['impact'] >= 0 else ''}{d['impact']:.0f})" for d in self.drivers[:3]]
        return (f"{self.label}: {fmt_value(self.prior, self.fmt)} -> {fmt_value(self.current, self.fmt)} "
                f"({sign}{fmt_value(abs(self.delta), self.fmt)}). Drivers: " + ("; ".join(parts) if parts else "none"))


def _data_change(prev: pd.Series, cur: pd.Series) -> str:
    """Describe which business field changed between two versions of a record."""
    for col, word in (("status", "status"), ("severity", "severity")):
        if col in prev and prev[col] != cur[col]:
            return f"{word} {prev[col]} -> {cur[col]}"
    if "target" in prev:
        if pd.notna(prev["target"]) and pd.isna(cur["target"]):
            return "target date removed"
        if pd.notna(prev["target"]) and pd.notna(cur["target"]) and prev["target"] != cur["target"]:
            return "target date moved later" if cur["target"] > prev["target"] else "target date moved earlier"
    return "other field changed"


def explain_metric(m: Metric, prior_df: pd.DataFrame, prior_asof: pd.Timestamp,
                   cur_df: pd.DataFrame, cur_asof: pd.Timestamp) -> Explanation:
    p = prior_df.drop_duplicates(m.key, keep="first").set_index(m.key, drop=False)
    c = cur_df.drop_duplicates(m.key, keep="first").set_index(m.key, drop=False)
    p_in = set(p.index[m.mask(p, prior_asof).values])
    c_in = set(c.index[m.mask(c, cur_asof).values])
    # Counterfactual: last week's data evaluated at this week's date. If that alone
    # explains the move, it was the calendar (time passing), not a data change.
    p_at_cur = set(p.index[m.mask(p, cur_asof).values])

    rows = []
    for k in c_in - p_in:
        reason = ("newly raised" if k not in p.index
                  else "time passed (crossed date threshold)" if k in p_at_cur
                  else _data_change(p.loc[k], c.loc[k]))
        rows.append({"key": k, "direction": "entered", "impact": 1, "reason": reason})
    for k in p_in - c_in:
        reason = ("removed from source" if k not in c.index
                  else "time passed (left date window)" if k not in p_at_cur
                  else _data_change(p.loc[k], c.loc[k]))
        rows.append({"key": k, "direction": "exited", "impact": -1, "reason": reason})
    detail = pd.DataFrame(rows, columns=["key", "direction", "impact", "reason"])

    drivers = []
    if not detail.empty:
        g = detail.groupby("reason").agg(count=("key", "size"), impact=("impact", "sum"),
                                         sample=("key", lambda s: ", ".join(sorted(map(str, s))[:5])))
        g = g.reindex(g["impact"].abs().sort_values(ascending=False).index)
        drivers = [{"reason": r, **row} for r, row in g.to_dict("index").items()]

    prior_v, cur_v = m.compute(prior_df, prior_asof), m.compute(cur_df, cur_asof)
    # Self-check: drivers must add up to the change. A residual means duplicate keys
    # in a source - surface it rather than hide it.
    residual = (cur_v - prior_v) - (detail["impact"].sum() if not detail.empty else 0)
    if residual:
        drivers.append({"reason": "unattributed (duplicate keys in source)", "count": 0, "impact": residual, "sample": ""})
    return Explanation(m.id, m.label, prior_v, cur_v, m.fmt, drivers, detail)


def explain_all(prior: dict[str, pd.DataFrame], prior_asof: pd.Timestamp,
                current: dict[str, pd.DataFrame], cur_asof: pd.Timestamp) -> list[Explanation]:
    return [explain_metric(m, prior[m.source], prior_asof, current[m.source], cur_asof) for m in METRICS]


def target_date_slips(prior_jira: pd.DataFrame, cur_jira: pd.DataFrame) -> pd.DataFrame:
    """Open issues whose Jira due date moved later since the prior run."""
    p = prior_jira.drop_duplicates("key").set_index("key")
    c = cur_jira.drop_duplicates("key").set_index("key")
    both = c.index.intersection(p.index)
    p, c = p.loc[both], c.loc[both]
    moved = c["is_open"] & p["target"].notna() & c["target"].notna() & (c["target"] > p["target"])
    out = pd.DataFrame({
        "key": both[moved],
        "severity": c.loc[moved, "severity"].values,
        "workstream": c.loc[moved, "workstream"].values,
        "summary": c.loc[moved, "summary"].values,
        "owner": c.loc[moved, "owner"].values,
        "prior_target": p.loc[moved, "target"].dt.date.values,
        "new_target": c.loc[moved, "target"].dt.date.values,
        "days_slipped": (c.loc[moved, "target"] - p.loc[moved, "target"]).dt.days.values,
    })
    return out.sort_values("days_slipped", ascending=False).reset_index(drop=True)
