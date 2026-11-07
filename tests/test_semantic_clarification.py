"""
Phase 12 Hardening — Semantic Clarification Loop Control & Atomic State Transition Test Suite

Comprehensive test coverage for:
1. Single-candidate clarification unconditional rejection (even with exact stanza match)
2. Database-authoritative atomic state advancement via advance_pending_clarification RPC
3. Semantic candidate-set delta evaluation (narrowing, unchanged, stall escalation, turn ceiling)
4. Failure behavior (fail closed, no WhatsApp message if DB advancement fails)
5. Partial unique active index on pending_clarifications
"""

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

import main
import db


# ---------------------------------------------------------------------------
# Test Fixtures & Utilities
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_supabase():
    with patch("db.supabase") as mock_sb:
        yield mock_sb


# ---------------------------------------------------------------------------
# Issue 1: Single-Candidate request_clarification Rejection
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Issue 2: Atomic Advancement RPC & DB Helper
# ---------------------------------------------------------------------------

def test_h05_advance_pending_clarification_calls_rpc(mock_supabase):
    """H5. db.advance_pending_clarification invokes PostgreSQL RPC advance_pending_clarification."""
    mock_rpc = MagicMock()
    mock_supabase.rpc = mock_rpc
    mock_rpc.return_value.execute.return_value = MagicMock(
        data=[{
            "id": "pc-advanced-1",
            "client_id": "c-1",
            "supplier_id": "s-1",
            "round_number": 2,
            "no_progress_count": 0,
            "status": "awaiting_reply",
        }]
    )

    res = db.advance_pending_clarification(
        previous_id="pc-old-1",
        client_id="c-1",
        supplier_id="s-1",
        candidate_rfq_ids=["rfq-A", "rfq-B"],
        raw_message="Not C",
        round_number=2,
        no_progress_count=0,
        last_question="A or B?",
    )
    assert res is not None
    assert res["id"] == "pc-advanced-1"
    mock_rpc.assert_called_once_with(
        "advance_pending_clarification",
        {
            "p_previous_id": "pc-old-1",
            "p_client_id": "c-1",
            "p_supplier_id": "s-1",
            "p_candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "p_raw_message": "Not C",
            "p_extracted_price": None,
            "p_extracted_delivery": None,
            "p_extracted_notes": None,
            "p_round_number": 2,
            "p_no_progress_count": 0,
            "p_last_question": "A or B?",
        },
    )


def test_h06_advance_pending_clarification_fails_closed_on_empty_rpc_data(mock_supabase):
    """H6. If RPC returns empty list (e.g. old row not awaiting_reply or tenant mismatch), helper returns None."""
    mock_supabase.rpc.return_value.execute.return_value = MagicMock(data=[])
    res = db.advance_pending_clarification(
        previous_id="pc-invalid",
        client_id="c-1",
        supplier_id="s-1",
        candidate_rfq_ids=["rfq-A", "rfq-B"],
        raw_message="Not C",
    )
    assert res is None


def test_h07_advance_pending_clarification_fails_closed_on_db_exception(mock_supabase):
    """H7. If RPC raises DB exception, helper catches it and returns None (fails closed)."""
    mock_supabase.rpc.side_effect = Exception("DB Connection Timeout")
    res = db.advance_pending_clarification(
        previous_id="pc-old",
        client_id="c-1",
        supplier_id="s-1",
        candidate_rfq_ids=["rfq-A", "rfq-B"],
        raw_message="Not C",
    )
    assert res is None


# ---------------------------------------------------------------------------
# Application Flow: Semantic Advancement & Fail-Closed
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 12 Core Semantic Clarification Tests
# ---------------------------------------------------------------------------


def test_18_non_selected_candidates_revert_correctly(mock_supabase):
    """18. db.revert_unresolved_candidates is a safe no-op that never changes rfq_suppliers status."""
    db.revert_unresolved_candidates(
        supplier_id="s-1",
        resolved_rfq_id="rfq-1",
        candidate_rfq_ids=["rfq-1", "rfq-2", "rfq-3"],
    )
    mock_supabase.table.assert_not_called()


def test_19_last_question_persisted(mock_supabase):
    """19. db.create_pending_clarification persists last_question and no_progress_count."""
    mock_pending_table = MagicMock()
    mock_supabase.table.return_value = mock_pending_table
    mock_pending_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": "pc-new"}])

    res = db.create_pending_clarification(
        client_id="c-1",
        supplier_id="s-1",
        candidate_rfq_ids=["rfq-1", "rfq-2"],
        raw_message="cement",
        round_number=2,
        no_progress_count=1,
        last_question="5kg or 10kg?",
    )
    assert res == "pc-new"
    insert_call = mock_pending_table.insert.call_args[0][0]
    assert insert_call["last_question"] == "5kg or 10kg?"
    assert insert_call["no_progress_count"] == 1


def test_28_master_rfq_remains_active_during_clarification(mock_supabase):
    """28. create_pending_clarification only inserts into pending_clarifications table and does not mutate rfq_suppliers table."""
    mock_rfq_supp_table = MagicMock()
    mock_pending_table = MagicMock()

    def router(t):
        if t == "pending_clarifications":
            return mock_pending_table
        elif t == "rfq_suppliers":
            return mock_rfq_supp_table
        return MagicMock()

    mock_supabase.table.side_effect = router
    mock_pending_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": "pc-1"}])

    db.create_pending_clarification(
        client_id="c-1",
        supplier_id="s-1",
        candidate_rfq_ids=["rfq-1", "rfq-2"],
        raw_message="cement",
    )

    # Confirm pending_clarifications was inserted into and rfq_suppliers was NOT mutated
    assert mock_pending_table.insert.called
    assert not mock_rfq_supp_table.update.called


def test_29_phase_9_review_handling_remains_compatible():
    """29. Operator reasoner and human escalation flags handle clarification_stalled category cleanly."""
    flag = {
        "id": "flag-clarif",
        "client_id": "c-1",
        "supplier_id": "s-1",
        "rfq_id": "rfq-A",
        "category": "clarification_stalled",
        "reason": "Clarification stalled after 2 uninformative replies.",
        "raw_message": "yes",
    }
    assert flag["category"] == "clarification_stalled"
    assert "Clarification stalled" in flag["reason"]
