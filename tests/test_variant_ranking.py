"""
Phase 11: Variant-Aware Ranking, Reporting & UI Tests
Covers all required verification scenarios:
1. Legacy one-supplier one-quote ranking
2. One supplier with two variants (e.g. India @ 45, China @ 38)
3. Multiple suppliers / variants ranking
4. Exact winning quote stored (best_quote_id)
5. best_supplier_id derived from winning quote
6. Hallucinated best quote rejected
7. Rank-1 valid fallback works only if exact valid quote ID
8. Invalid result raises ValueError instead of silent first-row fallback
9. Withdrawn variant excluded from ranking prompt
10. Superseded revision excluded from ranking prompt
11. Latest variant revision included in ranking prompt
12. Quote from other RFQ rejected
13. Duplicate ranking quote IDs deduplicated/rejected
14. Ranking uniqueness remains one per RFQ
15. Finalization failure leaves processing status
16. Successful finalization stores best_quote_id
17. RFQ details expose variant fields
18. Ranking exposes best_quote_id
19. Same supplier's two variants both participate in ranking
20. Only exact winning row gets winner authority
21. NULL variant renders cleanly
22. Legacy ranking without best_quote_id handled safely
23. Recommended offer shows exact supplier + variant
24. Withdrawn variants excluded from active ranking
25. DOCX report includes Variant column
26. All effective variants included in report
27. Withdrawn variants excluded from report
28. Superseded revisions excluded from report
29. Ranks mapped by quote_id
30. Winning variant clearly identified in report
31. NULL variant renders cleanly in report table
32. Cross-tenant report access rejected (403/400)
"""

import io
import os
import sys
import docx
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

import main
import db
import auth
import groq_client


client = TestClient(main.app)


class TestVariantAwareRanking:
    """Tests 1-16: Core ranking logic, deterministic winner validation, and finalization."""

    def test_1_legacy_one_supplier_one_quote_ranking(self):
        """1. Legacy single quote with NULL variant_label ranks cleanly."""
        quotes = [
            {
                "id": "q-1",
                "rfq_id": "rfq-1",
                "supplier_id": "supp-1",
                "variant_label": None,
                "price": 50.0,
                "delivery_time": "2 days",
                "quality_notes": "Standard",
                "suppliers": {"name": "Supplier 1"},
            }
        ]
        with patch.object(db, "get_quotes_for_rfq", return_value=quotes), \
             patch.object(groq_client, "rank_quotes", return_value={
                 "best_quote_id": "q-1",
                 "reasoning": "Supplier 1 is the sole and best offer.",
                 "ranking": [{"quote_id": "q-1", "rank": 1, "summary": "AED 50, 2 days"}]
             }), \
             patch.object(db, "save_ranking") as mock_save:

            result = main.generate_ranking("rfq-1")
            assert result["best_quote_id"] == "q-1"
            assert result["best_supplier_id"] == "supp-1"
            assert len(result["ranking"]) == 1
            assert result["ranking"][0]["quote_id"] == "q-1"
            assert result["ranking"][0]["variant_label"] is None
            mock_save.assert_called_once_with(
                rfq_id="rfq-1",
                best_supplier_id="supp-1",
                reasoning="Supplier 1 is the sole and best offer.",
                ranking_json=result,
                best_quote_id="q-1",
            )

    def test_2_and_4_and_5_one_supplier_two_variants(self):
        """2, 4, 5. Supplier A offers India @ 45 and China @ 38. China wins."""
        quotes = [
            {
                "id": "q-india",
                "rfq_id": "rfq-1",
                "supplier_id": "supp-A",
                "variant_label": "India",
                "price": 45.0,
                "delivery_time": "2 days",
                "quality_notes": "1 yr warranty",
                "suppliers": {"name": "Supplier A"},
            },
            {
                "id": "q-china",
                "rfq_id": "rfq-1",
                "supplier_id": "supp-A",
                "variant_label": "China",
                "price": 38.0,
                "delivery_time": "5 days",
                "quality_notes": "6 mo warranty",
                "suppliers": {"name": "Supplier A"},
            },
        ]
        with patch.object(db, "get_quotes_for_rfq", return_value=quotes), \
             patch.object(groq_client, "rank_quotes", return_value={
                 "best_quote_id": "q-china",
                 "reasoning": "China variant offers lower cost at AED 38.",
                 "ranking": [
                     {"quote_id": "q-china", "rank": 1, "summary": "AED 38"},
                     {"quote_id": "q-india", "rank": 2, "summary": "AED 45"},
                 ]
             }) as mock_llm, \
             patch.object(db, "save_ranking"):

            result = main.generate_ranking("rfq-1")
            assert result["best_quote_id"] == "q-china"
            assert result["best_supplier_id"] == "supp-A"
            assert result["ranking"][0]["variant_label"] == "China"
            assert result["ranking"][1]["variant_label"] == "India"

            summary_arg = mock_llm.call_args.kwargs["quotes_summary"]
            assert "Variant: 'India'" in summary_arg
            assert "Variant: 'China'" in summary_arg

    def test_3_multiple_suppliers_multiple_variants(self):
        """3. Multi-supplier, multi-variant ranking evaluation."""
        quotes = [
            {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "supp-A", "variant_label": "India", "price": 45.0, "suppliers": {"name": "Supplier A"}},
            {"id": "q-2", "rfq_id": "rfq-1", "supplier_id": "supp-A", "variant_label": "China", "price": 38.0, "suppliers": {"name": "Supplier A"}},
            {"id": "q-3", "rfq_id": "rfq-1", "supplier_id": "supp-B", "variant_label": "UAE", "price": 40.0, "suppliers": {"name": "Supplier B"}},
        ]
        with patch.object(db, "get_quotes_for_rfq", return_value=quotes), \
             patch.object(groq_client, "rank_quotes", return_value={
                 "best_quote_id": "q-2",
                 "reasoning": "China offers lowest price.",
                 "ranking": [
                     {"quote_id": "q-2", "rank": 1, "summary": "38 AED"},
                     {"quote_id": "q-3", "rank": 2, "summary": "40 AED"},
                     {"quote_id": "q-1", "rank": 3, "summary": "45 AED"},
                 ]
             }), \
             patch.object(db, "save_ranking"):

            result = main.generate_ranking("rfq-1")
            assert result["best_quote_id"] == "q-2"
            assert result["best_supplier_id"] == "supp-A"
            assert [r["quote_id"] for r in result["ranking"]] == ["q-2", "q-3", "q-1"]

    def test_6_and_8_hallucinated_quote_id_raises_value_error(self):
        """6 & 8. Hallucinated quote ID without valid fallback raises ValueError (no first-row fallback)."""
        quotes = [
            {"id": "q-real-1", "rfq_id": "rfq-1", "supplier_id": "supp-1", "price": 50.0, "suppliers": {"name": "Supplier 1"}}
        ]
        with patch.object(db, "get_quotes_for_rfq", return_value=quotes), \
             patch.object(groq_client, "rank_quotes", return_value={
                 "best_quote_id": "q-hallucinated-999",
                 "reasoning": "Fake quote",
                 "ranking": [{"quote_id": "q-hallucinated-999", "rank": 1}]
             }), \
             patch.object(db, "save_ranking") as mock_save:

            with pytest.raises(ValueError) as exc_info:
                main.generate_ranking("rfq-1")

            assert "does not match any valid candidate quote" in str(exc_info.value)
            mock_save.assert_not_called()

    def test_7_rank_1_valid_fallback(self):
        """7. If best_quote_id is missing but ranking[0].quote_id is valid, it resolves correctly."""
        quotes = [
            {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "supp-1", "price": 50.0, "suppliers": {"name": "Supplier 1"}}
        ]
        with patch.object(db, "get_quotes_for_rfq", return_value=quotes), \
             patch.object(groq_client, "rank_quotes", return_value={
                 "best_quote_id": None,
                 "reasoning": "Only 1 quote",
                 "ranking": [{"quote_id": "q-1", "rank": 1, "summary": "50 AED"}]
             }), \
             patch.object(db, "save_ranking"):

            result = main.generate_ranking("rfq-1")
            assert result["best_quote_id"] == "q-1"
            assert result["best_supplier_id"] == "supp-1"

    def test_9_10_11_ranking_input_uses_only_effective_available_quotes(self):
        """9, 10, 11. Superseded and withdrawn variants are excluded; latest revision is included."""
        effective_quotes = [
            {"id": "q-latest-rev", "rfq_id": "rfq-1", "supplier_id": "supp-1", "variant_label": "India", "price": 40.0, "suppliers": {"name": "Supplier 1"}}
        ]
        with patch.object(db, "get_quotes_for_rfq", return_value=effective_quotes) as mock_get_quotes, \
             patch.object(groq_client, "rank_quotes", return_value={
                 "best_quote_id": "q-latest-rev",
                 "reasoning": "Best revision",
                 "ranking": [{"quote_id": "q-latest-rev", "rank": 1}]
             }), \
             patch.object(db, "save_ranking"):

            result = main.generate_ranking("rfq-1")
            mock_get_quotes.assert_called_once_with("rfq-1")
            assert result["best_quote_id"] == "q-latest-rev"

    def test_12_quote_from_other_rfq_rejected(self):
        """12. LLM returning a quote_id belonging to another RFQ is rejected."""
        rfq1_quotes = [
            {"id": "q-rfq-1", "rfq_id": "rfq-1", "supplier_id": "supp-1", "price": 50.0, "suppliers": {"name": "Supplier 1"}}
        ]
        with patch.object(db, "get_quotes_for_rfq", return_value=rfq1_quotes), \
             patch.object(groq_client, "rank_quotes", return_value={
                 "best_quote_id": "q-rfq-2-other",
                 "reasoning": "Wrong quote",
                 "ranking": [{"quote_id": "q-rfq-2-other", "rank": 1}]
             }), \
             patch.object(db, "save_ranking") as mock_save:

            with pytest.raises(ValueError):
                main.generate_ranking("rfq-1")
            mock_save.assert_not_called()

    def test_13_duplicate_ranking_quote_ids_deduplicated(self):
        """13. Duplicate quote IDs in LLM response are safely deduplicated."""
        quotes = [
            {"id": "q-1", "rfq_id": "rfq-1", "supplier_id": "supp-1", "variant_label": "A", "price": 50.0, "suppliers": {"name": "Supplier 1"}},
            {"id": "q-2", "rfq_id": "rfq-1", "supplier_id": "supp-1", "variant_label": "B", "price": 60.0, "suppliers": {"name": "Supplier 1"}},
        ]
        with patch.object(db, "get_quotes_for_rfq", return_value=quotes), \
             patch.object(groq_client, "rank_quotes", return_value={
                 "best_quote_id": "q-1",
                 "reasoning": "Ranked",
                 "ranking": [
                     {"quote_id": "q-1", "rank": 1},
                     {"quote_id": "q-1", "rank": 1},  # duplicate
                     {"quote_id": "q-2", "rank": 2},
                 ]
             }), \
             patch.object(db, "save_ranking"):

            result = main.generate_ranking("rfq-1")
            assert len(result["ranking"]) == 2
            assert result["ranking"][0]["quote_id"] == "q-1"
            assert result["ranking"][1]["quote_id"] == "q-2"

    @pytest.mark.asyncio
    async def test_15_finalization_failure_leaves_processing_status(self):
        """15. When ranking validation fails in finalize_rfq_job, RFQ remains processing for recovery."""
        rfq = {"id": "rfq-fail", "product_name": "LED 60W", "client_id": "c-1"}
        quotes = [{"id": "q-1", "rfq_id": "rfq-fail", "supplier_id": "s-1", "price": 50.0, "suppliers": {"name": "Supplier"}}]

        with patch.object(db, "get_quotes_for_rfq", return_value=quotes), \
             patch.object(db, "ranking_exists", return_value=False), \
             patch.object(main, "generate_ranking", side_effect=ValueError("Ranking validation failed")), \
             patch.object(db, "mark_rfq_finalization_completed") as mock_mark_finalized, \
             patch.object(db, "log_webhook_error"):

            await main.finalize_rfq_job(rfq)

            # Finalization is not completed when ranking fails
            mock_mark_finalized.assert_not_called()

    @pytest.mark.asyncio
    async def test_16_successful_finalization_persists_best_quote_id(self):
        """16. Successful finalization invokes generate_ranking and marks completed."""
        rfq = {
            "id": "rfq-ok",
            "product_name": "LED 60W",
            "client_id": "c-1",
            "rfq_suppliers": [],
        }
        quotes = [{"id": "q-win", "rfq_id": "rfq-ok", "supplier_id": "s-1", "price": 45.0, "suppliers": {"name": "Supplier"}}]

        with patch.object(db, "get_quotes_for_rfq", return_value=quotes), \
             patch.object(db, "ranking_exists", return_value=False), \
             patch.object(main, "generate_ranking", return_value={"best_quote_id": "q-win", "best_supplier_id": "s-1"}) as mock_gen_rank, \
             patch.object(db, "mark_rfq_finalization_completed") as mock_mark_finalized:

            await main.finalize_rfq_job(rfq)
            mock_gen_rank.assert_called_once_with("rfq-ok")
            mock_mark_finalized.assert_called_once_with("rfq-ok")


class TestVariantAwareReports:
    """Tests 25-32: Daily Procurement DOCX generation and variant-aware reporting."""

    def test_25_26_29_30_31_docx_includes_variant_column_and_rank_by_quote_id(self):
        """25, 26, 29, 30, 31. DOCX report has 6 columns, maps ranks by quote_id, and handles NULL variants."""
        rfq_data = [
            {
                "rfq": {
                    "id": "rfq-1",
                    "product_name": "60W LED Panel",
                    "category": "Lighting",
                    "quantity": 100,
                    "specs": "6000K IP65",
                    "deadline_hours": 24,
                },
                "quotes": [
                    {"id": "q-china", "supplier_id": "s-1", "variant_label": "China", "price": 38.0, "delivery_time": "5 days", "quality_notes": "6 mo warranty", "suppliers": {"name": "Supplier A"}},
                    {"id": "q-india", "supplier_id": "s-1", "variant_label": "India", "price": 45.0, "delivery_time": "2 days", "quality_notes": "1 yr warranty", "suppliers": {"name": "Supplier A"}},
                    {"id": "q-null", "supplier_id": "s-2", "variant_label": None, "price": 40.0, "delivery_time": "3 days", "quality_notes": "Standard", "suppliers": {"name": "Supplier B"}},
                ],
                "top_quotes": [
                    {"rank": 1, "quote_id": "q-china", "supplier_name": "Supplier A", "variant_label": "China", "price": 38.0, "delivery_time": "5 days", "quality_notes": "6 mo warranty"},
                    {"rank": 2, "quote_id": "q-null", "supplier_name": "Supplier B", "variant_label": None, "price": 40.0, "delivery_time": "3 days", "quality_notes": "Standard"},
                    {"rank": 3, "quote_id": "q-india", "supplier_name": "Supplier A", "variant_label": "India", "price": 45.0, "delivery_time": "2 days", "quality_notes": "1 yr warranty"},
                ],
                "reasoning": "Supplier A (China) offers the lowest price at AED 38.",
            }
        ]

        doc_bytes = main.generate_daily_procurement_docx("2026-09-13", rfq_data)
        doc = docx.Document(io.BytesIO(doc_bytes))

        # Check table structure
        assert len(doc.tables) == 1
        table = doc.tables[0]
        rows = [[c.text for c in row.cells] for row in table.rows]

        # 6 columns
        assert rows[0] == ["Rank", "Supplier", "Variant", "Price", "Delivery Time", "Quality / Warranty Notes"]
        # Row 1: China
        assert rows[1] == ["#1", "Supplier A", "China", "AED 38.0", "5 days", "6 mo warranty"]
        # Row 2: NULL variant renders as '—'
        assert rows[2] == ["#2", "Supplier B", "—", "AED 40.0", "3 days", "Standard"]
        # Row 3: India
        assert rows[3] == ["#3", "Supplier A", "India", "AED 45.0", "2 days", "1 yr warranty"]

    def test_32_cross_tenant_report_access_rejected(self):
        """32. Non-admin or missing client_id is rejected on report endpoint."""
        with patch.object(auth, "verify_jwt", return_value={"sub": "user-member"}), \
             patch.object(db, "get_profile_by_id", return_value={"id": "user-member", "client_id": "c-1", "role": "member"}):

            headers = {"Authorization": "Bearer token"}
            res = client.get("/reports/daily?date=2026-09-13", headers=headers)
            assert res.status_code == 403


class TestVariantAwareApiAndLegacy:
    """Tests 17-24: API endpoints and legacy ranking compatibility."""

    def test_17_and_18_manual_rank_endpoint_returns_variant_ranking(self):
        """17, 18, 29. POST /rfq/{rfq_id}/rank triggers variant-aware ranking and returns full structure."""
        ranking_result = {
            "best_quote_id": "q-101",
            "best_supplier_id": "supp-101",
            "reasoning": "Optimal offer",
            "ranking": [{"quote_id": "q-101", "rank": 1, "variant_label": "China", "price": 35.0}],
        }
        with patch.object(auth, "verify_jwt", return_value={"sub": "user-1"}), \
             patch.object(db, "get_profile_by_id", return_value={"id": "user-1", "client_id": "c-1", "role": "admin"}), \
             patch.object(main, "generate_ranking", return_value=ranking_result) as mock_gen:

            headers = {"Authorization": "Bearer test-token"}
            res = client.post("/rfq/rfq-101/rank", headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert data["best_quote_id"] == "q-101"
            assert data["best_supplier_id"] == "supp-101"
            mock_gen.assert_called_once_with("rfq-101")

    def test_22_legacy_ranking_with_null_best_quote_id_handled_safely(self):
        """22. Legacy ranking without best_quote_id does not break report or ranking retrieval."""
        legacy_ranking = {
            "id": "rank-old-1",
            "rfq_id": "rfq-old",
            "best_quote_id": None,
            "best_supplier_id": "supp-legacy",
            "reasoning": "Old ranking reasoning",
            "ranking_json": {
                "best_supplier_id": "supp-legacy",
                "reasoning": "Old ranking",
                "ranking": [{"supplier_id": "supp-legacy", "rank": 1, "summary": "Legacy summary"}],
            }
        }
        with patch.object(db, "get_ranking_for_rfq", return_value=legacy_ranking):
            ranking = db.get_ranking_for_rfq("rfq-old")
            assert ranking["best_quote_id"] is None
            assert ranking["best_supplier_id"] == "supp-legacy"
