import pytest
from unittest.mock import AsyncMock, patch

import main
from policy_validator import ValidationResult, ActionCategory


@pytest.mark.asyncio
async def test_multi_rfq_batch_records_each_rfq_once():
    open_rfqs = [
        {
            "rfq_id": "rfq-a",
            "supplier_id": "supp-1",
            "status": "sent",
            "rfqs": {
                "id": "rfq-a",
                "client_id": "client-1",
                "status": "active",
                "product_name": "FLEXIBLE WIRE 6MMX5CORE",
            },
        },
        {
            "rfq_id": "rfq-b",
            "supplier_id": "supp-1",
            "status": "sent",
            "rfqs": {
                "id": "rfq-b",
                "client_id": "client-1",
                "status": "active",
                "product_name": "FLEXIBLE WIRE 6MMX3CORE",
            },
        },
    ]
    items = [
        {
            "candidate_rfq_id": "rfq-a",
            "product_reference": "6mmx5c",
            "price": 1248,
            "variant_label": "EZFLEX",
            "confidence": 0.98,
        },
        {
            "candidate_rfq_id": "rfq-b",
            "product_reference": "6mmx3c",
            "price": 743,
            "variant_label": "EZFLEX",
            "confidence": 0.97,
        },
    ]

    def approve(proposal, **kwargs):
        return ValidationResult(
            is_valid=True,
            action="record_quote",
            category=ActionCategory.MUTATION,
            sanitized_args=proposal.arguments,
        )

    with patch.object(main, "validate_action", side_effect=approve), \
         patch.object(main.db, "record_quotes_batch", side_effect=[
             [{"id": "quote-a"}],
             [{"id": "quote-b"}],
         ]) as record_batch, \
         patch.object(main.db, "record_agent_decision", return_value={"id": "decision-1"}), \
         patch.object(main.db, "log_message", return_value="msg-1"), \
         patch.object(main, "enqueue_message", new_callable=AsyncMock) as enqueue:
        result = await main.process_multi_rfq_quote_batch(
            parsed_items=items,
            all_open_rfqs=open_rfqs,
            supplier={"id": "supp-1", "phone_number": "971500000000"},
            client_id="client-1",
            raw_message="Brand EZFLEX\n6mmx5c 1248/-\n6mmx3c 743/-",
            inbound_log_id="inbound-1",
        )

    assert result["status"] == "multi_rfq_quotes_recorded"
    assert result["rfq_count"] == 2
    assert record_batch.call_count == 2
    enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_multi_rfq_batch_refuses_uncertain_mapping():
    result = await main.process_multi_rfq_quote_batch(
        parsed_items=[
            {
                "candidate_rfq_id": "rfq-a",
                "price": 1248,
                "variant_label": "EZFLEX",
                "confidence": 0.98,
            },
            {
                "candidate_rfq_id": None,
                "price": 743,
                "variant_label": "EZFLEX",
                "confidence": 0.4,
            },
        ],
        all_open_rfqs=[],
        supplier={"id": "supp-1", "phone_number": "971500000000"},
        client_id="client-1",
        raw_message="ambiguous batch",
        inbound_log_id="inbound-1",
    )

    assert result is None
