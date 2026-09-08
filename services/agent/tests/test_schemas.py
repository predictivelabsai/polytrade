from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from polytrade_agent.schemas import TradingActionProposal

adapter = TypeAdapter(TradingActionProposal)


def base() -> dict:
    return {
        "action": "create",
        "token_id": "123",
        "market_id": "condition",
        "market_question": "Will the contract validate?",
        "outcome": "Yes",
        "side": "BUY",
        "rationale": "test",
        "observed_at": datetime.now(UTC),
    }


def test_valid_gtc_and_fok() -> None:
    adapter.validate_python({**base(), "execution": "GTC", "price": "0.45", "size": "10"})
    adapter.validate_python(
        {**base(), "execution": "FOK", "amount": "10", "limit_price": "0.5"}
    )


def test_gtd_requires_expiration_and_immediate_cannot_be_post_only() -> None:
    with pytest.raises(ValidationError):
        adapter.validate_python({**base(), "execution": "GTD", "price": "0.45", "size": "10"})
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                **base(),
                "execution": "FAK",
                "amount": "10",
                "limit_price": "0.5",
                "post_only": True,
            }
        )


def test_contract_serializes_camel_case_and_rejects_noncanonical_decimals() -> None:
    proposal = adapter.validate_python(
        {**base(), "execution": "FOK", "amount": "10", "limit_price": "0.5"}
    )
    dumped = proposal.model_dump(by_alias=True, mode="json")
    assert dumped["tokenId"] == "123"
    assert dumped["limitPrice"] == "0.5"
    assert "token_id" not in dumped

    with pytest.raises(ValidationError):
        adapter.validate_python({**base(), "execution": "GTC", "price": "0.1234567", "size": "10"})
    with pytest.raises(ValidationError):
        adapter.validate_python({**base(), "execution": "FOK", "amount": "0", "limit_price": "0.5"})
