# Runbook — weekly IMO issue report

## Weekly run (Friday, before Monday SteerCo prep)

1. Drop the inputs into `data/<as-of date>/`:
   - `jira_issues.json`: from `python -m imo_trail fetch-jira --asof <date>`, or a Jira export
   - `issue_register.xlsx`: the IMO register (the title rows above the header are fine)
2. `python -m imo_trail run --asof <date>`
3. Read the console status line:
   - **PUBLISHED**: send the HTML/Excel from `output/<date>/`.
   - **REVIEW REQUIRED**: open the *Data Quality* and *Reconciliation* tabs and resolve
     the ERRORs with their owners. Re-run after fixes.
   - **BLOCKER** (exit code 2): an input is unusable, for example a renamed column. Fix the input and re-run.
4. The run writes a snapshot to `snapshots/<date>/`. Next week's run compares against it automatically.

## Severity policy

| Severity | Meaning | Pipeline behaviour |
|---|---|---|
| BLOCKER | Can't trust anything (schema broken) | Stops, exit 2 |
| ERROR | A reported number may be wrong | Publishes with REVIEW banner; `--strict` fails |
| WARN | Fixed automatically (e.g. exact duplicate rows) | Fix logged with Excel row numbers |
| INFO | Cosmetic (casing, whitespace) | Normalized |

## Troubleshooting a number

*"Overdue in the report doesn't match what I see in Jira."*

1. `python -m imo_trail explain --asof <date> overdue` lists every issue that entered or left the metric since last week, and why.
2. Check the definition in `imo_trail/metrics.py` (`Metric("overdue", ...)`). Jira's due date is the source of truth, not the register's Target Date.
3. Check *Data Quality* for `jira_high_sev_without_target`. An issue with no due date can't be overdue.
4. Compare `run_manifest.json` input hashes and row counts with what you expect. A stale export is the most common cause.

## Changing a business rule

- SLA days, statuses, workstreams, Jira custom-field IDs: `imo_trail/config.py`
- A KPI definition: `imo_trail/metrics.py`. Add a test in `tests/` that pins the new behaviour.
- Open a PR. CI runs the tests and a full demo run.

## Common input problems (all caught automatically)

| Symptom | Check that flags it |
|---|---|
| Rows pasted twice after a re-sort | `register_exact_duplicate_rows` (auto-removed) |
| Dates typed as text (`12/15/2026`) | `register_target_date_text_coerced` |
| `OPEN`, `in remediation ` | `register_status_normalized` |
| Register closed, Jira still open | `recon_status_register_closed_jira_open` |
| New Critical/High not added to the register | `recon_missing_from_register` |
| Jira key typo in register | `recon_key_not_in_jira` |
| Register missed a Jira re-baseline | `recon_target_date_mismatch` |
| Jira Closed with no resolution date | `jira_done_without_resolution` |
