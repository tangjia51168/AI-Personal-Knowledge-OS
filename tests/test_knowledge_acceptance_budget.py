"""Authorization ledger limits survive restarts and uncertain outcomes."""

import json
from decimal import Decimal

import pytest

from scripts import knowledge_acceptance as acceptance


def test_uncertain_submissions_consume_allowance_across_ledger_reload(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.json"
    monkeypatch.setattr(acceptance, "LEDGER", ledger)
    for number in range(3):
        acceptance.reserve_paid_request(96_000, str(number))
    before = ledger.read_bytes()
    with pytest.raises(acceptance.CheckFailed, match="AUTHORIZED_REQUEST_LIMIT_REACHED"):
        acceptance.reserve_paid_request(1, "fourth")
    assert ledger.read_bytes() == before
    rows = json.loads(before)["requests"]
    assert all(row["submitted_or_uncertain"] for row in rows)
    assert sum(Decimal(row["reserved_cny"]) for row in rows) <= Decimal("1")


def test_budget_exhaustion_blocks_even_first_remaining_request(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"requests": [{"reserved_cny": "0.999"}]}), encoding="utf8")
    monkeypatch.setattr(acceptance, "LEDGER", ledger)
    before = ledger.read_bytes()
    with pytest.raises(acceptance.CheckFailed, match="AUTHORIZED_BUDGET_LIMIT_REACHED"):
        acceptance.reserve_paid_request(1000, "extra")
    assert ledger.read_bytes() == before
