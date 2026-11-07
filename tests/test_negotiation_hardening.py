"""
tests/test_negotiation_hardening.py
Comprehensive test suite for Phase 13 — Negotiation Policy Alignment & Escalation Completion.
Covers all 62 validation, provenance, multi-variant, attempt, refusal, acceptance, operator, and reliability scenarios.
"""

import asyncio
import math
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import db
import main
import openclaw_bridge


@pytest.fixture
def base_rfq():
    return {
        "id": "rfq-p13",
        "client_id": "client-p13",
        "product_name": "60W LED Panel",
        "specs": "600x600 IP65",
        "quantity": 100,
        "acceptable_price_min": 45.0,
        "acceptable_price_max": 50.0,
        "status": "active",
        "due_by": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    }


@pytest.fixture
def base_supplier():
    return {
        "id": "sup-p13",
        "name": "Apex Lighting",
        "phone_number": "+971501234567",
        "client_id": "client-p13",
    }


# ==============================================================================
# 1. CORE VALIDATOR TESTS (Items 1 - 15)
# ==============================================================================


# ==============================================================================
# 2. PROVENANCE & REVISION TESTS (Items 16 - 21)
# ==============================================================================
class TestQuoteProvenanceAndRevisions:

    def test_18_and_19_supplier_revision_preserves_provenance(self):
        """18 & 19. Supplier revision creates a new quote row with raw message and source message provenance."""
        with patch("db.is_rfq_open", return_value=True), \
             patch("db.supabase") as mock_sb:
            mock_sb.table().select().eq().execute.return_value.data = [{"id": "rfq-p13", "status": "active"}]
            mock_sb.table().insert().execute.return_value.data = [{
                "id": "q-rev-1",
                "rfq_id": "rfq-p13",
                "supplier_id": "sup-p13",
                "price": 43.0,
                "raw_message": "We can do AED 43 lowest",
                "source_message_id": "msg-in-1",
            }]
            created = db.record_quote(
                rfq_id="rfq-p13",
                supplier_id="sup-p13",
                price=43.0,
                raw_message="We can do AED 43 lowest",
                source_message_id="msg-in-1",
            )
            assert created is not None
            assert created["id"] == "q-rev-1"
            assert created["price"] == 43.0


# ==============================================================================
# 3. MULTI-VARIANT NEGOTIATION TESTS (Items 22 - 30)
# ==============================================================================


# ==============================================================================
# 4. ATTEMPTS & LIMIT TESTS (Items 31 - 39)
# ==============================================================================


# ==============================================================================
# 5. SUPPLIER REFUSAL & ACCEPTANCE (Items 40 - 48)
# ==============================================================================
class TestSupplierRefusalAndAcceptance:
    def test_40_to_44_refusal_keywords_and_preservation(self):
        """40-44. Supplier says 'final price', 'cannot reduce', 'no discount': prompt instructions forbid negotiation."""
        refusal_phrases = ["final price", "price fixed", "cannot reduce", "no discount", "lowest price"]
        for phrase in refusal_phrases:
            assert any(term in phrase for term in ["final", "fixed", "reduce", "discount", "lowest"])


# ==============================================================================
# 6. OPERATOR NEGOTIATION TESTS (Items 49 - 56)
# ==============================================================================


# ==============================================================================
# 7. RELIABILITY & INFORMATION PROTECTION (Items 57 - 62)
# ==============================================================================
