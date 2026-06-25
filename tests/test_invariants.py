"""Double-entry invariant checking."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from recon.models import LedgerEntry, Transaction
from recon.reconcile import build_transactions, check_global_rollup


def _leg(txn_id, account, side, amount):
    return LedgerEntry(
        entry_id=f"{txn_id}-{side}",
        txn_id=txn_id,
        date=date(2026, 1, 1),
        account=account,
        side=side,
        amount=amount,
    )


def test_balanced_transaction_is_valid():
    txn = Transaction(
        txn_id="TXN-1",
        legs=[
            _leg("TXN-1", "1000:cash", "debit", 100.0),
            _leg("TXN-1", "4000:revenue", "credit", 100.0),
        ],
    )
    assert txn.amount == 100.0


def test_unbalanced_transaction_is_rejected():
    with pytest.raises(ValidationError) as exc:
        Transaction(
            txn_id="TXN-BAD",
            legs=[
                _leg("TXN-BAD", "1000:cash", "debit", 100.0),
                _leg("TXN-BAD", "4000:revenue", "credit", 90.0),
            ],
        )
    assert "double-entry invariant" in str(exc.value)


def test_build_transactions_catches_unbalanced_leg_set():
    entries = [
        _leg("TXN-1", "1000:cash", "debit", 50.0),
        _leg("TXN-1", "4000:revenue", "credit", 49.0),
    ]
    with pytest.raises(ValidationError):
        build_transactions(entries)


def test_global_rollup_detects_imbalance():
    # Two separately-balanced txns roll up to zero globally.
    good = [
        _leg("A", "1000:cash", "debit", 10.0),
        _leg("A", "4000:revenue", "credit", 10.0),
    ]
    check_global_rollup(good)  # should not raise

    bad = [_leg("B", "1000:cash", "debit", 10.0)]
    with pytest.raises(ValueError, match="roll-up is not balanced"):
        check_global_rollup(bad)
