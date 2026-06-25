"""Reconciliation engine.

Pipeline:

1. Load and validate the internal ledger and external feed with pydantic.
2. Enforce double-entry invariants (per-transaction balance and a global
   debit==credit roll-up across all accounts).
3. Use DuckDB for the set-based reconciliation: a ``FULL OUTER JOIN`` of the
   internal transaction roll-up against the aggregated external feed, with
   each break classified by type.
4. Emit a markdown + CSV audit report with a headline at-risk amount.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from recon.models import CENTS, ExternalRecord, LedgerEntry, Transaction

BREAK_TYPES = ["AMOUNT_MISMATCH", "STUCK_UNSETTLED", "DUPLICATE", "EXCESS_CREDIT"]

_RECON_SQL = """
WITH internal AS (
    SELECT
        txn_id,
        ROUND(SUM(CASE WHEN side = 'debit' THEN amount ELSE 0 END), 2) AS internal_amount
    FROM ledger
    GROUP BY txn_id
),
ext AS (
    SELECT
        txn_id,
        COUNT(*) FILTER (WHERE status = 'settled') AS settled_count,
        ROUND(MAX(CASE WHEN status = 'settled' THEN amount END), 2) AS settled_amount
    FROM external
    GROUP BY txn_id
),
joined AS (
    SELECT
        COALESCE(i.txn_id, e.txn_id) AS txn_id,
        i.internal_amount AS internal_amount,
        COALESCE(e.settled_count, 0) AS settled_count,
        e.settled_amount AS settled_amount
    FROM internal i
    FULL OUTER JOIN ext e ON i.txn_id = e.txn_id
),
classified AS (
    SELECT
        *,
        CASE
            WHEN internal_amount IS NULL THEN 'EXCESS_CREDIT'
            WHEN settled_count = 0 THEN 'STUCK_UNSETTLED'
            WHEN settled_count > 1 THEN 'DUPLICATE'
            WHEN ABS(settled_amount - internal_amount) > {tol} THEN 'AMOUNT_MISMATCH'
            ELSE 'MATCHED'
        END AS status
    FROM joined
)
SELECT
    txn_id,
    status,
    internal_amount,
    settled_amount,
    settled_count,
    CASE status
        WHEN 'EXCESS_CREDIT'   THEN settled_amount
        WHEN 'STUCK_UNSETTLED' THEN internal_amount
        WHEN 'DUPLICATE'       THEN settled_amount * (settled_count - 1)
        WHEN 'AMOUNT_MISMATCH' THEN ABS(settled_amount - internal_amount)
        ELSE 0
    END AS break_amount
FROM classified
ORDER BY status, txn_id
"""


@dataclass(frozen=True)
class BreakRow:
    """One reconciled transaction outcome."""

    txn_id: str
    status: str
    internal_amount: float | None
    settled_amount: float | None
    settled_count: int
    break_amount: float


@dataclass
class ReconResult:
    """Outcome of a reconciliation run."""

    rows: list[BreakRow]

    @property
    def matched(self) -> list[BreakRow]:
        return [r for r in self.rows if r.status == "MATCHED"]

    @property
    def breaks(self) -> list[BreakRow]:
        return [r for r in self.rows if r.status != "MATCHED"]

    def counts_by_type(self) -> dict[str, int]:
        counts = dict.fromkeys(BREAK_TYPES, 0)
        for row in self.breaks:
            counts[row.status] += 1
        return counts

    def amount_by_type(self) -> dict[str, float]:
        amounts = dict.fromkeys(BREAK_TYPES, 0.0)
        for row in self.breaks:
            amounts[row.status] += row.break_amount
        return {k: round(v, 2) for k, v in amounts.items()}

    @property
    def at_risk_amount(self) -> float:
        return round(sum(r.break_amount for r in self.breaks), 2)

    @property
    def reconciled_amount(self) -> float:
        return round(sum(r.internal_amount or 0.0 for r in self.matched), 2)


# --------------------------------------------------------------------------- #
# Invariant checks
# --------------------------------------------------------------------------- #
def build_transactions(entries: list[LedgerEntry]) -> list[Transaction]:
    """Group legs into transactions; raises if any transaction is unbalanced."""
    grouped: dict[str, list[LedgerEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.txn_id].append(entry)
    # Transaction's model validator enforces the double-entry invariant.
    return [Transaction(txn_id=txn_id, legs=legs) for txn_id, legs in grouped.items()]


def account_rollup(entries: list[LedgerEntry]) -> dict[str, float]:
    """Net balance (debits - credits) per account."""
    net: dict[str, float] = defaultdict(float)
    for entry in entries:
        signed = entry.amount if entry.side == "debit" else -entry.amount
        net[entry.account] += signed
    return {acct: round(value, 2) for acct, value in net.items()}


def check_global_rollup(entries: list[LedgerEntry]) -> None:
    """The signed sum across all accounts must be zero in a closed ledger."""
    total = round(sum(account_rollup(entries).values()), 2)
    if abs(total) > CENTS:
        raise ValueError(f"Global account roll-up is not balanced: net={total:.2f}")


# --------------------------------------------------------------------------- #
# Reconciliation (DuckDB)
# --------------------------------------------------------------------------- #
def reconcile(entries: list[LedgerEntry], external: list[ExternalRecord]) -> ReconResult:
    """Validate invariants then reconcile internal vs external using DuckDB."""
    # Fail fast on broken double-entry data before reconciling.
    build_transactions(entries)
    check_global_rollup(entries)

    ledger = pd.DataFrame(
        [e.model_dump() for e in entries],
        columns=list(LedgerEntry.model_fields),
    )
    external_df = pd.DataFrame(
        [r.model_dump() for r in external],
        columns=list(ExternalRecord.model_fields),
    )

    con = duckdb.connect()
    try:
        con.register("ledger", ledger)
        con.register("external", external_df)
        result = con.execute(_RECON_SQL.format(tol=CENTS)).fetchall()
    finally:
        con.close()

    rows = [
        BreakRow(
            txn_id=txn_id,
            status=status,
            internal_amount=None if internal_amount is None else round(internal_amount, 2),
            settled_amount=None if settled_amount is None else round(settled_amount, 2),
            settled_count=int(settled_count),
            break_amount=round(break_amount or 0.0, 2),
        )
        for (txn_id, status, internal_amount, settled_amount, settled_count, break_amount) in result
    ]
    return ReconResult(rows=rows)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def render_markdown(result: ReconResult, run_date: date) -> str:
    counts = result.counts_by_type()
    amounts = result.amount_by_type()
    total_breaks = len(result.breaks)
    total_txns = len(result.rows)

    lines = [
        "# Ledger Reconciliation - Audit Report",
        "",
        f"_Generated: {run_date.isoformat()}_",
        "",
        "## Headline",
        "",
        f"- **At-risk / recoverable amount:** ${result.at_risk_amount:,.2f}",
        f"- **Transactions reconciled clean:** {len(result.matched)} "
        f"(${result.reconciled_amount:,.2f})",
        f"- **Breaks detected:** {total_breaks} across {total_txns} reconciled keys",
        "",
        "## Breaks by type",
        "",
        "| Break type | Count | Amount (USD) | What it means |",
        "| --- | ---: | ---: | --- |",
    ]
    descriptions = {
        "AMOUNT_MISMATCH": "Bank settled a different amount than the ledger recorded.",
        "STUCK_UNSETTLED": "Posted internally but never settled by the bank (pending).",
        "DUPLICATE": "Same settlement posted more than once in the external feed.",
        "EXCESS_CREDIT": "Money present externally with no matching internal transaction.",
    }
    for break_type in BREAK_TYPES:
        lines.append(
            f"| {break_type} | {counts[break_type]} | {amounts[break_type]:,.2f} "
            f"| {descriptions[break_type]} |"
        )
    lines += [
        f"| **TOTAL** | **{total_breaks}** | **{result.at_risk_amount:,.2f}** | |",
        "",
        "## Notes",
        "",
        "- `MATCHED` transactions satisfy the double-entry invariant internally and",
        "  agree with a single settled external record.",
        "- All figures derive from synthetic, seeded data; see `python -m recon.data`.",
        "",
    ]
    return "\n".join(lines)


def write_reports(result: ReconResult, out_dir: Path, run_date: date | None = None) -> None:
    run_date = run_date or date.today()
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "audit_report.md").write_text(render_markdown(result, run_date))

    with (out_dir / "breaks.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "txn_id",
                "status",
                "internal_amount",
                "settled_amount",
                "settled_count",
                "break_amount",
            ]
        )
        for row in result.breaks:
            writer.writerow(
                [
                    row.txn_id,
                    row.status,
                    row.internal_amount,
                    row.settled_amount,
                    row.settled_count,
                    row.break_amount,
                ]
            )

    counts = result.counts_by_type()
    amounts = result.amount_by_type()
    with (out_dir / "summary.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["break_type", "count", "amount"])
        for break_type in BREAK_TYPES:
            writer.writerow([break_type, counts[break_type], amounts[break_type]])
        writer.writerow(["TOTAL", len(result.breaks), result.at_risk_amount])


# --------------------------------------------------------------------------- #
# Loading + CLI
# --------------------------------------------------------------------------- #
def load_ledger_csv(path: Path) -> list[LedgerEntry]:
    with path.open(newline="") as fh:
        return [LedgerEntry(**row) for row in csv.DictReader(fh)]


def load_external_csv(path: Path) -> list[ExternalRecord]:
    with path.open(newline="") as fh:
        return [ExternalRecord(**row) for row in csv.DictReader(fh)]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Reconcile internal ledger vs external feed.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)

    entries = load_ledger_csv(args.data_dir / "ledger.csv")
    external = load_external_csv(args.data_dir / "external.csv")
    result = reconcile(entries, external)
    write_reports(result, args.out_dir)

    counts = result.counts_by_type()
    print(f"Reconciled {len(result.matched)} clean / {len(result.breaks)} breaks.")
    for break_type in BREAK_TYPES:
        print(f"  {break_type:<16} {counts[break_type]:>3}")
    print(f"At-risk amount: ${result.at_risk_amount:,.2f}")
    print(f"Report written to {args.out_dir}/audit_report.md")


if __name__ == "__main__":
    main()
