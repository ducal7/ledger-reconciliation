"""Seeded synthetic data generator.

Produces two artefacts:

1. An internal double-entry ledger (``ledger.csv``) where every transaction
   is balanced (debits == credits).
2. An external bank/settlement feed (``external.csv``) into which a fixed,
   deterministic set of anomalies are injected:

   * ``AMOUNT_MISMATCH``  - the settled amount drifts from the ledger value.
   * ``STUCK_UNSETTLED``  - a transaction the bank never settled (pending).
   * ``DUPLICATE``        - the same settlement posted twice.
   * ``EXCESS_CREDIT``    - money appears externally with no internal txn.

The generator is fully deterministic for a given :class:`GenConfig`.
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from recon.models import ExternalRecord, LedgerEntry

ACCOUNTS = [
    "1000:cash",
    "1100:accounts_receivable",
    "2000:accounts_payable",
    "4000:revenue",
    "5000:cost_of_goods_sold",
    "6000:operating_expense",
    "2100:customer_wallet",
    "3000:settlement_clearing",
]

START_DATE = date(2026, 1, 1)
HORIZON_DAYS = 90


@dataclass(frozen=True)
class GenConfig:
    """Knobs controlling generation. Defaults give a modest, non-trivial set."""

    seed: int = 20260626
    n_clean: int = 80
    n_amount_mismatch: int = 6
    n_stuck: int = 5
    n_duplicate: int = 3
    n_excess_credit: int = 4

    @property
    def expected_breaks(self) -> dict[str, int]:
        """Ground-truth break counts the reconciler should recover."""
        return {
            "AMOUNT_MISMATCH": self.n_amount_mismatch,
            "STUCK_UNSETTLED": self.n_stuck,
            "DUPLICATE": self.n_duplicate,
            "EXCESS_CREDIT": self.n_excess_credit,
        }


def _balanced_legs(rng: random.Random, txn_id: str, day: date) -> tuple[float, list[LedgerEntry]]:
    """Build a single balanced debit/credit pair for ``txn_id``."""
    amount = round(rng.uniform(10.0, 5000.0), 2)
    debit_acct, credit_acct = rng.sample(ACCOUNTS, 2)
    legs = [
        LedgerEntry(
            entry_id=f"{txn_id}-D",
            txn_id=txn_id,
            date=day,
            account=debit_acct,
            side="debit",
            amount=amount,
        ),
        LedgerEntry(
            entry_id=f"{txn_id}-C",
            txn_id=txn_id,
            date=day,
            account=credit_acct,
            side="credit",
            amount=amount,
        ),
    ]
    return amount, legs


def generate(
    config: GenConfig | None = None,
) -> tuple[list[LedgerEntry], list[ExternalRecord]]:
    """Generate the internal ledger and external feed deterministically."""
    config = config or GenConfig()
    rng = random.Random(config.seed)
    entries: list[LedgerEntry] = []
    external: list[ExternalRecord] = []
    counter = 0

    def next_txn_id() -> str:
        nonlocal counter
        counter += 1
        return f"TXN-{counter:05d}"

    def random_day() -> date:
        return START_DATE + timedelta(days=rng.randint(0, HORIZON_DAYS - 1))

    # 1. Clean, fully reconciling transactions.
    for _ in range(config.n_clean):
        txn_id = next_txn_id()
        day = random_day()
        amount, legs = _balanced_legs(rng, txn_id, day)
        entries.extend(legs)
        external.append(
            ExternalRecord(
                record_id=f"EXT-{txn_id}",
                txn_id=txn_id,
                date=day,
                amount=amount,
                status="settled",
            )
        )

    # 2. Amount mismatches: ledger is correct, the bank settled a wrong amount.
    for _ in range(config.n_amount_mismatch):
        txn_id = next_txn_id()
        day = random_day()
        amount, legs = _balanced_legs(rng, txn_id, day)
        entries.extend(legs)
        drift = round(rng.uniform(1.0, 75.0), 2) * rng.choice([-1, 1])
        ext_amount = round(max(amount + drift, 0.5), 2)
        external.append(
            ExternalRecord(
                record_id=f"EXT-{txn_id}",
                txn_id=txn_id,
                date=day,
                amount=ext_amount,
                status="settled",
            )
        )

    # 3. Stuck / unsettled: posted internally, bank record stuck in `pending`.
    for _ in range(config.n_stuck):
        txn_id = next_txn_id()
        day = random_day()
        amount, legs = _balanced_legs(rng, txn_id, day)
        entries.extend(legs)
        external.append(
            ExternalRecord(
                record_id=f"EXT-{txn_id}",
                txn_id=txn_id,
                date=day,
                amount=amount,
                status="pending",
            )
        )

    # 4. Duplicates: one ledger txn, two settled external records.
    for _ in range(config.n_duplicate):
        txn_id = next_txn_id()
        day = random_day()
        amount, legs = _balanced_legs(rng, txn_id, day)
        entries.extend(legs)
        external.append(
            ExternalRecord(
                record_id=f"EXT-{txn_id}",
                txn_id=txn_id,
                date=day,
                amount=amount,
                status="settled",
            )
        )
        external.append(
            ExternalRecord(
                record_id=f"EXT-{txn_id}-DUP",
                txn_id=txn_id,
                date=day,
                amount=amount,
                status="settled",
            )
        )

    # 5. Excess credits: external-only settlements with no internal txn.
    for i in range(config.n_excess_credit):
        orphan_id = f"TXN-ORPHAN-{i + 1:05d}"
        day = random_day()
        amount = round(rng.uniform(10.0, 5000.0), 2)
        external.append(
            ExternalRecord(
                record_id=f"EXT-{orphan_id}",
                txn_id=orphan_id,
                date=day,
                amount=amount,
                status="settled",
            )
        )

    # Realistic feeds are unordered; shuffle deterministically.
    rng.shuffle(external)
    return entries, external


def write_ledger_csv(entries: list[LedgerEntry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["entry_id", "txn_id", "date", "account", "side", "amount", "currency"])
        for e in entries:
            writer.writerow(
                [e.entry_id, e.txn_id, e.date.isoformat(), e.account, e.side, e.amount, e.currency]
            )


def write_external_csv(external: list[ExternalRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["record_id", "txn_id", "date", "amount", "status", "currency"])
        for r in external:
            writer.writerow(
                [r.record_id, r.txn_id, r.date.isoformat(), r.amount, r.status, r.currency]
            )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic ledger + external feed.")
    parser.add_argument("--out-dir", type=Path, default=Path("data"))
    parser.add_argument("--seed", type=int, default=GenConfig().seed)
    args = parser.parse_args(argv)

    config = GenConfig(seed=args.seed)
    entries, external = generate(config)
    write_ledger_csv(entries, args.out_dir / "ledger.csv")
    write_external_csv(external, args.out_dir / "external.csv")
    print(
        f"Generated {len(entries)} ledger legs and {len(external)} external records "
        f"into {args.out_dir}/ (seed={config.seed})."
    )


if __name__ == "__main__":
    main()
