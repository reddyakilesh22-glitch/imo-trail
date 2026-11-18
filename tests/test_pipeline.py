"""End-to-end: run both sample weeks and check every planted defect is caught."""
from datetime import date

import json

import pytest

from imo_trail import config, pipeline

PRIOR, CURRENT = date(2026, 11, 6), date(2026, 11, 13)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("run")
    snaps = tmp / "snapshots"
    r1 = pipeline.run(PRIOR, data_dir=config.DATA_DIR / PRIOR.isoformat(), out_dir=tmp / "out1", snap_dir=snaps)
    r2 = pipeline.run(CURRENT, data_dir=config.DATA_DIR / CURRENT.isoformat(), out_dir=tmp / "out2", snap_dir=snaps)
    return r1, r2, tmp


def test_clean_week_publishes(runs):
    r1, _, _ = runs
    assert r1.status == "PUBLISHED"
    assert not [f for f in r1.findings if f.severity in ("ERROR", "BLOCKER")]


def test_every_planted_defect_is_caught(runs):
    _, r2, _ = runs
    checks = {f.check: f.count for f in r2.findings}
    expected = {
        "register_exact_duplicate_rows": 3, "register_target_date_text_coerced": 2, "register_status_normalized": 2,
        "recon_status_register_closed_jira_open": 2, "recon_status_jira_closed_register_open": 1,
        "recon_severity_mismatch": 2, "recon_key_not_in_jira": 1, "recon_missing_from_register": 2,
        "recon_target_date_mismatch": 1, "jira_done_without_resolution": 1, "jira_high_sev_without_target": 1,
    }
    for check, n in expected.items():
        assert checks.get(check) == n, f"{check}: expected {n}, got {checks.get(check)}"
    assert r2.status == "REVIEW REQUIRED"


def test_week_over_week_is_explained_and_adds_up(runs):
    _, r2, _ = runs
    assert r2.prior_asof == PRIOR
    for e in r2.explanations:
        assert sum(d["impact"] for d in e.drivers) == pytest.approx(e.delta), e.metric_id
    assert len(r2.slips) >= 7


def test_outputs_and_manifest(runs):
    _, r2, tmp = runs
    m = json.loads((tmp / "out2" / "run_manifest.json").read_text())
    assert m["inputs"]["register"]["rows"] - m["inputs"]["register"]["rows_after_dedupe"] == 3
    assert set(r2.outputs) == {"excel", "html"}


def test_strict_mode_fails_and_writes_nothing(tmp_path):
    r = pipeline.run(CURRENT, data_dir=config.DATA_DIR / CURRENT.isoformat(), out_dir=tmp_path / "o",
                     snap_dir=tmp_path / "s", strict=True)
    assert r.status == "FAILED" and not r.outputs
    assert not (tmp_path / "s").exists()
