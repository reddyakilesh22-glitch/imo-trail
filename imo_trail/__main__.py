"""Command line entry point.

    python -m imo_trail run --asof 2026-11-13            # build the weekly report
    python -m imo_trail run --asof 2026-11-13 --ai       # + Claude-drafted summary (guarded)
    python -m imo_trail explain --asof 2026-11-13 overdue  # why did a KPI move?
    python -m imo_trail fetch-jira --asof 2026-11-13     # pull live from Jira Cloud (needs env vars)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from . import config, pipeline, sources
from .metrics import METRICS_BY_ID


def _print_run(r: pipeline.RunResult) -> None:
    print(f"\n== IMO Issue Report  as of {r.asof}  (vs {r.prior_asof or 'no prior'})  -> {r.status}\n")
    for b in r.summary:
        print(f"  - {b}")
    print("\n  Findings:")
    for f in r.findings:
        print(f"   [{f.severity:<5}] {f.check:<42} {f.count:>3}  {f.detail}")
    if r.outputs:
        print("\n  Outputs:")
        for k, v in r.outputs.items():
            print(f"   {k:<6} {v}")
    print(f"\n  Summary source: {r.summary_source}   Run time: {r.manifest['total_s']}s\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="imo_trail", description="IMO weekly issue reporting")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="build the report")
    p_run.add_argument("--asof", required=True, type=date.fromisoformat)
    p_run.add_argument("--data-dir")
    p_run.add_argument("--prior", type=date.fromisoformat, help="compare with this snapshot (default: latest earlier)")
    p_run.add_argument("--ai", action="store_true", help="draft the summary with Claude (numbers verified)")
    p_run.add_argument("--strict", action="store_true", help="fail (exit 1) on any ERROR finding")
    p_ex = sub.add_parser("explain", help="explain week-over-week change in one KPI")
    p_ex.add_argument("--asof", required=True, type=date.fromisoformat)
    p_ex.add_argument("metric", choices=sorted(METRICS_BY_ID))
    p_fj = sub.add_parser("fetch-jira", help="pull issues from Jira Cloud into data/<asof>/jira_issues.json")
    p_fj.add_argument("--asof", required=True, type=date.fromisoformat)
    p_fj.add_argument("--jql", default=f"project = {config.JIRA_PROJECT} ORDER BY created ASC")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    try:
        if a.cmd == "run":
            r = pipeline.run(a.asof, data_dir=a.data_dir, prior_asof=a.prior, use_ai=a.ai, strict=a.strict)
            _print_run(r)
            return 1 if r.status == "FAILED" else 0
        if a.cmd == "explain":
            r = pipeline.run(a.asof)
            e = next((x for x in r.explanations if x.metric_id == a.metric), None)
            if e is None:
                print("No prior snapshot to compare against - run the prior week first.")
                return 1
            print(e.headline())
            print(e.records.to_string(index=False) if len(e.records) else "(no record-level changes)")
            return 0
        if a.cmd == "fetch-jira":
            out = config.DATA_DIR / a.asof.isoformat()
            out.mkdir(parents=True, exist_ok=True)
            print(sources.fetch_jira_live(a.jql, out / "jira_issues.json"))
            return 0
    except sources.SchemaError as e:
        print(f"BLOCKER: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
