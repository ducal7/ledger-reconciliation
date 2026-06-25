"""Each break type is detected on a crafted fixture."""

from __future__ import annotations

from datetime import date

from recon.models import ExternalRecord, LedgerEntry
from recon.reconcile import reconcile


def _txn(txn_id, amount):
    return [
        LedgerEntry(
            entry_id=f"{txn_id}-D",
            txn_id=txn_id,
            date=date(2026, 1, 1),
            account="1000:cash",
            side="debit",
            amount=amount,
        ),
        LedgerEntry(
            entry_id=f"{txn_id}-C",
            txn_id=txn_id,
            date=date(2026, 1, 1),
            account="4000:revenue",
            side="credit",
            amount=amount,
        ),
    ]


def _ext(txn_id, amount, status="settled", suffix=""):
    return ExternalRecord(
        record_id=f"EXT-{txn_id}{suffix}",
        txn_id=txn_id,
        date=date(2026, 1, 1),
        amount=amount,
        status=status,
    )


def _status_map(result):
    return {row.txn_id: row.status for row in result.rows}


def test_all_break_types_detected():
    entries = []
    external = []

    # MATCHED
    entries += _txn("OK", 100.0)
    external.append(_ext("OK", 100.0))

    # AMOUNT_MISMATCH
    entries += _txn("MIS", 200.0)
    external.append(_ext("MIS", 175.0))

    # STUCK_UNSETTLED (pending external)
    entries += _txn("STUCK", 300.0)
    external.append(_ext("STUCK", 300.0, status="pending"))

    # DUPLICATE (two settled external records)
    entries += _txn("DUP", 50.0)
    external.append(_ext("DUP", 50.0))
    external.append(_ext("DUP", 50.0, suffix="-DUP"))

    # EXCESS_CREDIT (external only)
    external.append(_ext("ORPHAN", 999.0))

    result = reconcile(entries, external)
    statuses = _status_map(result)

    assert statuses["OK"] == "MATCHED"
    assert statuses["MIS"] == "AMOUNT_MISMATCH"
    assert statuses["STUCK"] == "STUCK_UNSETTLED"
    assert statuses["DUP"] == "DUPLICATE"
    assert statuses["ORPHAN"] == "EXCESS_CREDIT"

    counts = result.counts_by_type()
    assert counts == {
        "AMOUNT_MISMATCH": 1,
        "STUCK_UNSETTLED": 1,
        "DUPLICATE": 1,
        "EXCESS_CREDIT": 1,
    }


def test_break_amounts():
    entries = _txn("MIS", 200.0)
    external = [_ext("MIS", 175.0)]
    result = reconcile(entries, external)
    (row,) = result.breaks
    assert row.status == "AMOUNT_MISMATCH"
    assert row.break_amount == 25.0
    assert result.at_risk_amount == 25.0


def test_missing_external_is_stuck():
    # No external record at all -> still flagged as unsettled.
    entries = _txn("LONELY", 10.0)
    result = reconcile(entries, [])
    (row,) = result.breaks
    assert row.status == "STUCK_UNSETTLED"
    assert row.break_amount == 10.0
