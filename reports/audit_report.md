# Ledger Reconciliation - Audit Report

_Generated: 2026-06-26_

## Headline

- **At-risk / recoverable amount:** $26,287.51
- **Transactions reconciled clean:** 80 ($217,348.82)
- **Breaks detected:** 18 across 98 reconciled keys

## Breaks by type

| Break type | Count | Amount (USD) | What it means |
| --- | ---: | ---: | --- |
| AMOUNT_MISMATCH | 6 | 115.08 | Bank settled a different amount than the ledger recorded. |
| STUCK_UNSETTLED | 5 | 14,704.38 | Posted internally but never settled by the bank (pending). |
| DUPLICATE | 3 | 6,940.78 | Same settlement posted more than once in the external feed. |
| EXCESS_CREDIT | 4 | 4,527.27 | Money present externally with no matching internal transaction. |
| **TOTAL** | **18** | **26,287.51** | |

## Notes

- `MATCHED` transactions satisfy the double-entry invariant internally and
  agree with a single settled external record.
- All figures derive from synthetic, seeded data; see `python -m recon.data`.
