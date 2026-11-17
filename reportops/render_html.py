"""Self-contained HTML version of the report (one file, no external assets) - easy to
email, drop on SharePoint/Confluence, or open on a phone before SteerCo."""
from __future__ import annotations

import html
from pathlib import Path

import pandas as pd

CSS = """
:root{--bg:#f7f8fa;--card:#fff;--ink:#1c2430;--muted:#5b6675;--line:#e3e7ec;--accent:#1f3a5f;
--good:#1e7b4f;--good-bg:#e3f4ea;--bad:#b42335;--bad-bg:#fbe7ea;--warn-bg:#fff4d6;--info-bg:#eef0f3}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1b2129;--ink:#e6eaf0;--muted:#9aa5b4;--line:#2b333e;
--accent:#8fb3e0;--good:#6fd39f;--good-bg:#173326;--bad:#ff8a98;--bad-bg:#3a1c22;--warn-bg:#3a3117;--info-bg:#232a33}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px;color:var(--accent)}
.meta{color:var(--muted)}.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:600;font-size:12px}
.REVIEW{background:var(--warn-bg)}.PUBLISHED{background:var(--good-bg);color:var(--good)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.kv{font-size:24px;font-weight:700}.kl{color:var(--muted);font-size:12px}.d{font-size:12px;font-weight:600}
.better{color:var(--good)}.worse{color:var(--bad)}.flat,.neutral,.n\\/a{color:var(--muted)}
ul.sum{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 12px 12px 30px;margin:0}
.tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{background:var(--info-bg);font-weight:600}td.wrap{white-space:normal}
tr.ERROR td,tr.BLOCKER td{background:var(--bad-bg)}tr.WARN td{background:var(--warn-bg)}
.h0{}.h1{background:var(--warn-bg)}.h2{background:var(--bad-bg)}
footer{margin-top:32px;color:var(--muted);font-size:12px}
"""


def _e(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, pd.Timestamp):
        return v.date().isoformat()
    return html.escape(str(v))


def _table(df: pd.DataFrame, row_class: str | None = None, wrap: tuple = ()) -> str:
    if df is None or df.empty:
        return '<p class="meta">None.</p>'
    head = "".join(f"<th>{_e(c)}</th>" for c in df.columns)
    body = []
    for _, r in df.iterrows():
        cls = f' class="{_e(r[row_class])}"' if row_class else ""
        cells = "".join(f'<td class="wrap">{_e(r[c])}</td>' if c in wrap else f"<td>{_e(r[c])}</td>" for c in df.columns)
        body.append(f"<tr{cls}>{cells}</tr>")
    return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _heat(aging: pd.DataFrame) -> str:
    cols = list(aging.columns)
    head = "<th>Workstream</th>" + "".join(f"<th>{_e(c)}</th>" for c in cols)
    rows = []
    for ws, r in aging.iterrows():
        cells = []
        for c in cols:
            v = int(r[c])
            lvl = 0 if c == "Total" or ws == "Total" else (2 if (c in ("61-90", "90+") and v) else 1 if (c == "31-60" and v) else 0)
            cells.append(f'<td class="h{lvl}">{v}</td>')
        rows.append(f"<tr><td>{_e(ws)}</td>{''.join(cells)}</tr>")
    return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def write_html(r, path: Path) -> Path:
    cards = "".join(
        f'<div class="card"><div class="kl">{_e(k["label"])}</div><div class="kv">{_e(k["current_fmt"])}</div>'
        f'<div class="d {k["trend"]}">{_e(k["delta_fmt"])} vs {_e(k["prior_fmt"])}</div></div>' for k in r.kpis)
    summary = "".join(f"<li>{_e(b)}</li>" for b in r.summary)
    explain = "".join(f"<li>{_e(e.headline())}</li>" for e in r.explanations if e.delta or e.drivers)
    findings = pd.DataFrame([f.to_dict() for f in r.findings]) if r.findings else pd.DataFrame()
    if not findings.empty:
        findings["sample_keys"] = findings["sample_keys"].map(lambda ks: ", ".join(ks[:5]))
    high_overdue = r.tables["overdue"][r.tables["overdue"]["severity"].isin(["Critical", "High"])]
    pill = "REVIEW" if r.status != "PUBLISHED" else "PUBLISHED"
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IMO Issue Report {r.asof}</title><style>{CSS}</style></head><body><main>
<h1>IMO Issue Report - as of {r.asof}</h1>
<div class="meta"><span class="pill {pill}">{_e(r.status)}</span> &nbsp;Compared with {_e(r.prior_asof or 'no prior run')} &middot; Summary: {_e(r.summary_source)}</div>
<h2>Summary</h2><ul class="sum">{summary}</ul>
<h2>Key metrics</h2><div class="grid">{cards}</div>
<h2>Why the numbers moved (week over week)</h2><ul class="sum">{explain or '<li>No prior snapshot to compare.</li>'}</ul>
<h2>Overdue Critical/High</h2>{_table(high_overdue, wrap=("summary",))}
<h2>Target dates moved this week</h2>{_table(r.slips, wrap=("summary",))}
<h2>Open issues by workstream</h2>{_table(r.tables["workstreams"])}
<h2>Aging (days open)</h2>{_heat(r.tables["aging"])}
<h2>Register vs Jira breaks</h2>{_table(r.recon_table, wrap=("suggested_action",))}
<h2>Data quality</h2>{_table(findings, row_class="severity", wrap=("detail",))}
<footer>Generated by reportops &middot; synthetic sample data &middot; inputs, hashes and timings in run_manifest.json</footer>
</main></body></html>"""
    path.write_text(doc)
    return path
