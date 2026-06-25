"""Generator determinism and shape."""

from __future__ import annotations

from recon.data import GenConfig, generate


def _dump(entries, external):
    return (
        [e.model_dump() for e in entries],
        [r.model_dump() for r in external],
    )


def test_generation_is_deterministic():
    a_entries, a_external = generate(GenConfig())
    b_entries, b_external = generate(GenConfig())
    assert _dump(a_entries, a_external) == _dump(b_entries, b_external)


def test_different_seed_changes_output():
    base_entries, _ = generate(GenConfig(seed=1))
    other_entries, _ = generate(GenConfig(seed=2))
    assert [e.model_dump() for e in base_entries] != [e.model_dump() for e in other_entries]


def test_internal_ledger_is_fully_balanced():
    config = GenConfig()
    entries, _ = generate(config)
    debits = round(sum(e.amount for e in entries if e.side == "debit"), 2)
    credits = round(sum(e.amount for e in entries if e.side == "credit"), 2)
    assert debits == credits


def test_expected_counts():
    config = GenConfig()
    entries, external = generate(config)
    internal_txns = {e.txn_id for e in entries}
    # clean + mismatch + stuck + duplicate transactions have internal legs.
    expected_internal = (
        config.n_clean + config.n_amount_mismatch + config.n_stuck + config.n_duplicate
    )
    assert len(internal_txns) == expected_internal
    # Two legs per transaction.
    assert len(entries) == 2 * expected_internal
    # External rows: one per internal txn, plus duplicate copies and orphans.
    expected_external = expected_internal + config.n_duplicate + config.n_excess_credit
    assert len(external) == expected_external
