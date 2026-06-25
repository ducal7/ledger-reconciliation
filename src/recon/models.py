"""Typed, validated domain models for the reconciliation pipeline.

Two worlds are modelled:

* The *internal* double-entry ledger. Each economic event is a
  :class:`Transaction` composed of two or more :class:`LedgerEntry` legs.
  A transaction is only valid if its debits equal its credits.
* The *external* bank/settlement feed, modelled as flat
  :class:`ExternalRecord` rows that reference an internal ``txn_id``.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Side = Literal["debit", "credit"]
ExternalStatus = Literal["settled", "pending"]

#: Monetary comparison tolerance (one cent).
CENTS = 0.01


class LedgerEntry(BaseModel):
    """A single leg of a double-entry transaction."""

    model_config = {"frozen": True}

    entry_id: str
    txn_id: str
    date: date
    account: str
    side: Side
    amount: float = Field(gt=0, description="Positive magnitude of the leg in `currency`.")
    currency: str = "USD"

    @field_validator("amount")
    @classmethod
    def _round_amount(cls, value: float) -> float:
        return round(value, 2)


class Transaction(BaseModel):
    """A balanced double-entry transaction made of two or more legs."""

    txn_id: str
    legs: list[LedgerEntry] = Field(min_length=2)

    @model_validator(mode="after")
    def _check_balanced(self) -> Transaction:
        debits = round(sum(leg.amount for leg in self.legs if leg.side == "debit"), 2)
        credits = round(sum(leg.amount for leg in self.legs if leg.side == "credit"), 2)
        if abs(debits - credits) > CENTS:
            raise ValueError(
                f"Transaction {self.txn_id} violates the double-entry invariant: "
                f"debits={debits:.2f} != credits={credits:.2f}"
            )
        bad = [leg.txn_id for leg in self.legs if leg.txn_id != self.txn_id]
        if bad:
            raise ValueError(f"Transaction {self.txn_id} contains foreign legs: {sorted(set(bad))}")
        return self

    @property
    def amount(self) -> float:
        """Transaction value (total debits, which equals total credits)."""
        return round(sum(leg.amount for leg in self.legs if leg.side == "debit"), 2)


class ExternalRecord(BaseModel):
    """A single row from the external bank/settlement feed."""

    model_config = {"frozen": True}

    record_id: str
    txn_id: str
    date: date
    amount: float = Field(gt=0)
    status: ExternalStatus
    currency: str = "USD"

    @field_validator("amount")
    @classmethod
    def _round_amount(cls, value: float) -> float:
        return round(value, 2)
