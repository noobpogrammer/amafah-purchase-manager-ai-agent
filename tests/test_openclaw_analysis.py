"""Verify migrated OpenClaw transport, output checks and failure behavior without live I/O."""
import os
from unittest.mock import MagicMock, patch
import pytest
import openclaw_bridge as analysis


def test_analysis_uses_authenticated_openclaw_endpoint():
    response = MagicMock()
    response.json.return_value = {"ok": True, "result": {"items": []}}
    with patch.dict(os.environ, {"OPENCLAW_BASE_URL": "https://openclaw.test/", "OPENCLAW_TOOL_API_KEY": "test-key"}), patch.object(analysis.requests, "post", return_value=response) as post:
        assert analysis._request_openclaw_analysis("multi_rfq_quote_parser", {"message": "two prices"}, {}) == {"items": []}
    response.raise_for_status.assert_called_once()
    assert post.call_args.args[0] == "https://openclaw.test/internal/procurement/analyze"
    assert post.call_args.kwargs["headers"]["X-OpenClaw-Key"] == "test-key"
    assert post.call_args.kwargs["timeout"] == 120


def test_multi_item_output_cannot_introduce_an_unknown_rfq_or_infinite_price():
    output = {"items": [{"candidate_rfq_id": "other-tenant", "price": float("inf"), "confidence": .99}]}
    with patch.object(analysis, "_request_openclaw_analysis", return_value=output):
        result = analysis.parse_multi_rfq_quotes("A 10, B 20", [{"id": "a"}, {"id": "b"}])
    assert result["items"][0]["candidate_rfq_id"] is None
    assert result["items"][0]["price"] is None


def test_commercial_parser_uses_openclaw_facts():
    with patch.object(analysis, "_request_openclaw_analysis", return_value={"delivery": {"days": 8}, "tradeoff": {"present": True, "dimension": "delivery"}}) as call:
        result = analysis.parse_commercial_message("A later dispatch is required")
    assert result["delivery"]["days"] == 8
    assert result["tradeoff"]["present"] is True
    assert call.call_args.args[0] == "commercial_parse"


def test_failed_parsing_does_not_invent_multi_item_quotes():
    with patch.object(analysis, "_request_openclaw_analysis", side_effect=RuntimeError("offline")):
        assert analysis.parse_multi_rfq_quotes("A 10, B 20", [{"id": "a"}, {"id": "b"}]) == {"items": []}
        facts = analysis.parse_commercial_message("72 AED per piece, delivery in 2 days")
    assert facts["price"]["amount"] == 72
    assert facts["delivery"]["days"] == 2


def test_ranking_uses_openclaw_and_rejects_malformed_output():
    valid = {"best_quote_id": "q", "reasoning": "Lowest price", "ranking": [{"quote_id": "q", "rank": 1}]}
    with patch.object(analysis, "_request_openclaw_analysis", return_value=valid) as call:
        assert analysis.rank_quotes("RFQ", "quotes")["best_quote_id"] == "q"
    assert call.call_args.args[0] == "quote_ranking"
    with patch.object(analysis, "_request_openclaw_analysis", return_value={"ranking": []}), pytest.raises(ValueError):
        analysis.rank_quotes("RFQ", "quotes")
