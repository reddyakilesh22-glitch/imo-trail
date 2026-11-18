"""Extract layer: read the Jira export / API and the Excel issue register into tidy DataFrames.

Extraction is deliberately *lossless*: values are normalized but never silently
dropped. Anything odd is recorded in ``ExtractLog`` notes so the quality layer can
report it.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import config


class SchemaError(Exception):
    """Raised when a source is missing columns the report cannot run without."""


@dataclass
class ExtractLog:
    notes: list[dict] = field(default_factory=list)

    def note(self, source: str, kind: str, count: int, keys: list[str], detail: str) -> None:
        if count:
            self.notes.append({"source": source, "kind": kind, "count": count,
                               "keys": keys[:10], "detail": detail})


# --------------------------------------------------------------------------- Jira
def fetch_jira_live(jql: str, out_path: Path) -> Path:
    """Pull issues from Jira Cloud and save them in the same shape as an export.

    Uses the paginated /rest/api/3/search/jql endpoint (nextPageToken pagination).
    Credentials come from the environment: JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN.
    """
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    token = base64.b64encode(f"{os.environ['JIRA_EMAIL']}:{os.environ['JIRA_API_TOKEN']}".encode()).decode()
    issues, next_token = [], None
    while True:
        params = {"jql": jql, "fields": ",".join(config.JIRA_FIELDS), "maxResults": 100}
        if next_token:
            params["nextPageToken"] = next_token
        req = urllib.request.Request(f"{base}/rest/api/3/search/jql?{urllib.parse.urlencode(params)}",
                                     headers={"Authorization": f"Basic {token}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            page = json.load(resp)
        issues.extend(page.get("issues", []))
        next_token = page.get("nextPageToken")
        if page.get("isLast", True) or not next_token:
            break
    out_path.write_text(json.dumps({"total": len(issues), "issues": issues}, indent=1))
    return out_path


def _name(obj, key="name"):
    return obj.get(key) if isinstance(obj, dict) else None


def load_jira(path: Path, log: ExtractLog) -> pd.DataFrame:
    raw = json.loads(Path(path).read_text())
    rows = []
    for issue in raw.get("issues", []):
        f = issue.get("fields", {})
        comps = [c["name"] for c in f.get("components") or []]
        rows.append({
            "key": issue["key"],
            "summary": f.get("summary") or "",
            "issue_type": _name(f.get("issuetype")),
            "status": _name(f.get("status")),
            "status_category": _name((f.get("status") or {}).get("statusCategory"), "key"),
            "priority": _name(f.get("priority")),
            "severity": _name(f.get(config.SEVERITY_FIELD), "value"),
            "owner": _name(f.get("assignee"), "displayName"),
            "workstream": comps[0] if comps else None,
            "labels": ",".join(f.get("labels") or []),
            "register_id": f.get(config.REGISTER_ID_FIELD),
            "created": f.get("created"),
            "resolved": f.get("resolutiondate"),
            "target": f.get("duedate"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        raise SchemaError(f"{path}: no issues found")
    for col in ("created", "resolved"):
        # Jira timestamps carry a UTC offset; report in US Eastern calendar time
        df[col] = pd.to_datetime(df[col], utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
    df["target"] = pd.to_datetime(df["target"], errors="coerce")
    df["is_open"] = df["status_category"] != "done"
    return df


# --------------------------------------------------------------------------- Excel
def _find_header_row(path: Path, sheet: str, marker: str) -> int:
    probe = pd.read_excel(path, sheet_name=sheet, header=None, nrows=25)
    for idx, row in probe.iterrows():
        if any(str(v).strip() == marker for v in row.values):
            return int(idx)
    raise SchemaError(f"{path}: could not find a header row containing '{marker}'")


def normalize_choice(value, choices: list[str]) -> str | None:
    """'in remediation ' -> 'In Remediation'. Unknown values are kept as-is so they get flagged."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = " ".join(str(value).split()).lower()
    for canonical in choices:
        if s == canonical.lower():
            return canonical
    return str(value)


def _blank_to_none(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return None
    return str(v).strip()


def load_register(path: Path, log: ExtractLog) -> pd.DataFrame:
    header = _find_header_row(path, config.REGISTER_SHEET, config.REGISTER_HEADER_MARKER)
    df = pd.read_excel(path, sheet_name=config.REGISTER_SHEET, header=header, dtype=object)
    df.columns = [str(c).strip() for c in df.columns]
    missing = [src for src, dst in config.REGISTER_COLUMNS.items()
               if dst in config.REGISTER_REQUIRED and src not in df.columns]
    if missing:
        raise SchemaError(f"{path}: missing required column(s) {missing}. Was a header renamed?")
    df = df.rename(columns=config.REGISTER_COLUMNS)
    df = df[[c for c in config.REGISTER_COLUMNS.values() if c in df.columns]].dropna(how="all")
    df["source_row"] = df.index + header + 2  # 1-based Excel row number, for tracing back

    for col in ("register_id", "jira_key", "title", "workstream", "exec_owner", "commentary", "regulatory", "escalation"):
        if col in df:
            df[col] = df[col].map(_blank_to_none)

    for col, choices, kind in (("status", config.REGISTER_STATUSES, "status_normalized"),
                               ("severity", config.SEVERITIES, "severity_normalized")):
        raw = df[col].copy()
        df[col] = df[col].map(lambda v: normalize_choice(v, choices))
        changed = raw.astype(str) != df[col].astype(str)
        log.note("register", kind, int(changed.sum()), df.loc[changed, "register_id"].tolist(),
                 f"{col.title()} had stray spaces/casing; normalized to canonical value")

    for col in ("raised", "target", "closed"):
        was_text = df[col].map(lambda v: isinstance(v, str) and v.strip() != "")
        df[col] = pd.to_datetime(df[col], errors="coerce", format="mixed")
        if col == "target":
            log.note("register", "target_date_text_coerced", int(was_text.sum()),
                     df.loc[was_text, "register_id"].tolist(), "Target Date typed as text in Excel; parsed to a date")
    df["is_open"] = df["status"].isin(config.REGISTER_OPEN_STATUSES)
    return df.reset_index(drop=True)


def load_sla(path: Path) -> dict[str, int]:
    """Remediation SLA maintained by the IMO in the workbook; config is the fallback."""
    try:
        sla = pd.read_excel(path, sheet_name="Severity SLA")
        return {str(r["Severity"]).strip(): int(r["Days to Remediate"]) for _, r in sla.iterrows()}
    except (ValueError, KeyError):
        return dict(config.REMEDIATION_SLA_DAYS)
