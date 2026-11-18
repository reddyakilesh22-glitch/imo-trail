"""Orchestration: extract -> validate -> reconcile -> metrics -> explain -> summarize -> render.

Each run leaves an audit trail (run manifest with input file hashes, row counts,
check results and timings) and a snapshot that the next run compares against.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from . import config, detective, metrics, narrative, quality, reconcile, sources

log = logging.getLogger("imo_trail")


@dataclass
class RunResult:
    asof: date
    prior_asof: date | None
    status: str                       # PUBLISHED | REVIEW REQUIRED | FAILED
    kpis: list[dict]
    findings: list[quality.Finding]
    recon_table: pd.DataFrame
    explanations: list
    slips: pd.DataFrame
    summary: list[str]
    summary_source: str
    tables: dict[str, pd.DataFrame]
    outputs: dict[str, str] = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              cwd=config.ROOT, timeout=5).stdout.strip() or None
    except Exception:
        return None


# ------------------------------------------------------------------ snapshots
DATE_COLS = {"jira": ["created", "resolved", "target"], "register": ["raised", "target", "closed"]}


def save_snapshot(asof: date, frames: dict[str, pd.DataFrame], values: dict, snap_dir: Path) -> Path:
    d = snap_dir / asof.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        df.to_csv(d / f"{name}.csv", index=False)
    (d / "metrics.json").write_text(json.dumps(values, indent=1, default=float))
    return d


def load_snapshot(asof: date, snap_dir: Path) -> tuple[dict[str, pd.DataFrame], dict] | None:
    d = snap_dir / asof.isoformat()
    if not (d / "metrics.json").exists():
        return None
    frames = {}
    for name, cols in DATE_COLS.items():
        df = pd.read_csv(d / f"{name}.csv", parse_dates=cols)
        df["is_open"] = df["is_open"].astype(bool)
        for col in df.select_dtypes(include="object").columns:   # CSV NaN -> None, same as a fresh load
            df[col] = df[col].where(df[col].notna(), None)
        frames[name] = df
    return frames, json.loads((d / "metrics.json").read_text())


def latest_snapshot_before(asof: date, snap_dir: Path) -> date | None:
    if not snap_dir.exists():
        return None
    dates = sorted(date.fromisoformat(p.name) for p in snap_dir.iterdir()
                   if p.is_dir() and (p / "metrics.json").exists() and p.name < asof.isoformat())
    return dates[-1] if dates else None


# ------------------------------------------------------------------ run
def build_kpis(cur: dict, prior: dict | None) -> list[dict]:
    out = []
    ids = [m.id for m in metrics.METRICS] + list(metrics.EXTRA_LABELS)
    for mid in ids:
        label, fmt, good = metrics.label_of(mid)
        c = cur.get(mid)
        p = prior.get(mid) if prior else None
        delta = None if p is None or pd.isna(p) or pd.isna(c) else c - p
        if delta is None or delta == 0 or good == "none":
            trend = "flat" if delta == 0 else "n/a" if delta is None else "neutral"
        else:
            trend = "better" if (delta > 0) == (good == "up") else "worse"
        out.append({"id": mid, "label": label, "fmt": fmt, "current": c, "prior": p, "delta": delta, "trend": trend,
                    "current_fmt": metrics.fmt_value(c, fmt),
                    "prior_fmt": "n/a" if p is None else metrics.fmt_value(p, fmt),
                    "delta_fmt": "n/a" if delta is None else (("+" if delta > 0 else "") + metrics.fmt_value(delta, fmt))})
    return out


def run(asof: date, data_dir: Path | None = None, out_dir: Path | None = None, snap_dir: Path | None = None,
        prior_asof: date | None = None, use_ai: bool = False, strict: bool = False) -> RunResult:
    t0 = time.perf_counter()
    timings: dict[str, float] = {}
    data_dir = Path(data_dir or config.DATA_DIR / asof.isoformat())
    out_dir = Path(out_dir or config.OUTPUT_DIR / asof.isoformat())
    snap_dir = Path(snap_dir or config.SNAPSHOT_DIR)
    ts = pd.Timestamp(asof)

    def step(name):
        timings[name] = round(time.perf_counter() - t0 - sum(timings.values()), 3)

    # 1. Extract
    xlog = sources.ExtractLog()
    jira_path, reg_path = data_dir / "jira_issues.json", data_dir / "issue_register.xlsx"
    jira = sources.load_jira(jira_path, xlog)
    register = sources.load_register(reg_path, xlog)
    sla = sources.load_sla(reg_path)
    if sla != config.REMEDIATION_SLA_DAYS:
        log.warning("SLA sheet differs from config: %s", sla)
    rows_in = {"jira": len(jira), "register": len(register)}
    step("extract")

    # 2. Validate (auto-fix what is safe to fix, flag the rest)
    register, dedupe_findings = quality.dedupe_register(register)
    findings = (dedupe_findings + quality.extract_notes_to_findings(xlog.notes)
                + quality.check_register(register, ts) + quality.check_jira(jira, ts))
    step("validate")

    # 3. Reconcile register vs Jira
    recon_findings, recon_table = reconcile.reconcile(register, jira)
    findings = quality.sort_findings(findings + recon_findings)
    step("reconcile")

    # 4. Metrics + week-over-week explanation
    frames = {"jira": jira, "register": register}
    values = metrics.compute_all(frames, ts)
    prior_asof = prior_asof or latest_snapshot_before(asof, snap_dir)
    snap = load_snapshot(prior_asof, snap_dir) if prior_asof else None
    prior_frames, prior_values = snap if snap else (None, None)
    kpis = build_kpis(values, prior_values)
    explanations = detective.explain_all(prior_frames, pd.Timestamp(prior_asof), frames, ts) if prior_frames else []
    slips = detective.target_date_slips(prior_frames["jira"], jira) if prior_frames else pd.DataFrame()
    tables = {"workstreams": metrics.workstream_table(jira, ts), "aging": metrics.aging_table(jira, ts),
              "overdue": metrics.overdue_list(jira, ts)}
    step("metrics")

    # 5. Summary (AI optional, guarded; template is the fallback)
    fdicts = [f.to_dict() for f in findings]
    recon_counts = recon_table["check"].value_counts().to_dict()
    summary = narrative.template_summary(kpis, fdicts, recon_counts, slips, explanations)
    summary_source = "template"
    if use_ai:
        facts = narrative.build_facts(kpis, fdicts, recon_counts, len(slips), asof.isoformat())
        bullets, status = narrative.ai_summary(facts)
        if bullets:
            summary, summary_source = bullets, f"AI draft ({narrative.MODEL}), numbers verified against facts"
        else:
            summary_source = f"template (AI skipped: {status})"
            log.warning("AI summary not used: %s", status)
    step("summarize")

    # 6. Status gate
    has_error = any(f.severity in ("ERROR", "BLOCKER") for f in findings)
    status = "REVIEW REQUIRED" if has_error else "PUBLISHED"
    if strict and has_error:
        status = "FAILED"

    result = RunResult(asof, prior_asof, status, kpis, findings, recon_table, explanations, slips,
                       summary, summary_source, tables)

    # 7. Render + snapshot + manifest
    from .render_excel import write_excel
    from .render_html import write_html
    out_dir.mkdir(parents=True, exist_ok=True)
    if status != "FAILED":
        result.outputs["excel"] = str(write_excel(result, out_dir / f"IMO_Issue_Report_{asof}.xlsx"))
        result.outputs["html"] = str(write_html(result, out_dir / f"IMO_Issue_Report_{asof}.html"))
        save_snapshot(asof, frames, values, snap_dir)
    step("render")

    result.manifest = {
        "as_of": asof.isoformat(), "prior_as_of": prior_asof.isoformat() if prior_asof else None,
        "status": status, "git_commit": _git_commit(),
        "inputs": {"jira": {"path": str(jira_path), "sha256": _sha256(jira_path), "rows": rows_in["jira"]},
                   "register": {"path": str(reg_path), "sha256": _sha256(reg_path), "rows": rows_in["register"],
                                "rows_after_dedupe": len(register)}},
        "findings": {sev: sum(1 for f in findings if f.severity == sev) for sev in quality.SEVERITY_ORDER},
        "metrics": values, "summary_source": summary_source, "outputs": result.outputs,
        "timings_s": timings, "total_s": round(time.perf_counter() - t0, 3),
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(result.manifest, indent=1, default=float))
    return result
