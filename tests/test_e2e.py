"""End-to-end: fixed seed produces the expected break counts."""

from __future__ import annotations

from recon.data import GenConfig, generate
from recon.reconcile import reconcile


def test_end_to_end_expected_breaks():
    config = GenConfig()
    entries, external = generate(config)
    result = reconcile(entries, external)

    assert result.counts_by_type() == config.expected_breaks
    assert len(result.matched) == config.n_clean
    assert result.at_risk_amount > 0

    # Total reconciled keys = internal txns + external-only orphans.
    internal_txns = config.n_clean + config.n_amount_mismatch + config.n_stuck + config.n_duplicate
    assert len(result.rows) == internal_txns + config.n_excess_credit


def test_reports_written(tmp_path):
    from datetime import date

    from recon.reconcile import write_reports

    entries, external = generate(GenConfig())
    result = reconcile(entries, external)
    write_reports(result, tmp_path, run_date=date(2026, 6, 26))

    assert (tmp_path / "audit_report.md").exists()
    assert (tmp_path / "breaks.csv").exists()
    assert (tmp_path / "summary.csv").exists()
    report = (tmp_path / "audit_report.md").read_text()
    assert "At-risk" in report
