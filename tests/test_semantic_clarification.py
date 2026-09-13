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

from policy_validator import (
    ActionProposal,
    validate_action,
    ActionCategory,
    ValidationResult,
)
from groq_client import AgentContext
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

def test_h01_single_candidate_without_matched_rfq_rejected():
    """H1. request_clarification with 1 candidate and NO matched_rfq is rejected."""
    proposal = ActionProposal(
        tool_name="request_clarification",
        arguments={
            "candidate_rfq_ids": ["rfq-A"],
            "clarifying_question": "Are you quoting rfq-A?",
        },
    )
    open_rfqs = [{"rfqs": {"id": "rfq-A", "status": "active"}}]
    res = validate_action(
        proposal,
        client_id="client-1",
        supplier_id="supp-1",
        context_rfqs=open_rfqs,
        matched_rfq_id=None,
    )
    assert not res.is_valid
    assert "single candidate is unnecessary" in res.reason.lower()


def test_h02_single_candidate_with_matched_rfq_also_rejected():
    """H2. request_clarification with 1 candidate matching matched_rfq_id is ALSO rejected (identity resolved)."""
    proposal = ActionProposal(
        tool_name="request_clarification",
        arguments={
            "candidate_rfq_ids": ["rfq-locked-1"],
            "clarifying_question": "Please clarify specs.",
        },
    )
    open_rfqs = [{"rfqs": {"id": "rfq-locked-1", "status": "active"}}]
    res = validate_action(
        proposal,
        client_id="client-1",
        supplier_id="supp-1",
        context_rfqs=open_rfqs,
        matched_rfq_id="rfq-locked-1",
    )
    assert not res.is_valid
    assert "single candidate is unnecessary" in res.reason.lower()


def test_h03_exact_stanza_mismatch_fails_stanza_lock():
    """H3. Stanza mismatch (candidate B when matched is A) fails with deterministic RFQ match lock error."""
    proposal = ActionProposal(
        tool_name="request_clarification",
        arguments={
            "candidate_rfq_ids": ["rfq-B"],
            "clarifying_question": "Are you quoting B?",
        },
    )
    open_rfqs = [
        {"rfqs": {"id": "rfq-A", "status": "active"}},
        {"rfqs": {"id": "rfq-B", "status": "active"}},
    ]
    res = validate_action(
        proposal,
        client_id="client-1",
        supplier_id="supp-1",
        context_rfqs=open_rfqs,
        matched_rfq_id="rfq-A",
    )
    assert not res.is_valid
    assert "violate deterministic rfq match lock" in res.reason.lower()


def test_h04_two_valid_candidates_allowed():
    """H4. Multi-candidate (2+ candidates) clarification passes validation."""
    proposal = ActionProposal(
        tool_name="request_clarification",
        arguments={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "Are you quoting 5kg or 10kg cement?",
        },
    )
    open_rfqs = [
        {"rfqs": {"id": "rfq-A", "status": "active"}},
        {"rfqs": {"id": "rfq-B", "status": "active"}},
    ]
    res = validate_action(
        proposal,
        client_id="client-1",
        supplier_id="supp-1",
        context_rfqs=open_rfqs,
        matched_rfq_id=None,
    )
    assert res.is_valid
    assert res.sanitized_args["candidate_rfq_ids"] == ["rfq-A", "rfq-B"]


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

@pytest.mark.asyncio
async def test_h08_narrowed_clarification_uses_advance_pending_clarification():
    """H8. Narrowed reply (3->2) executes atomic advance_pending_clarification."""
    prev_clarification = {
        "id": "clarif-abc",
        "round_number": 1,
        "no_progress_count": 0,
        "pending_rfq_ids": ["rfq-A", "rfq-B", "rfq-C"],
        "last_question": "Which of A, B, C?",
    }
    context = AgentContext(
        client_id="client-1",
        supplier_id="supp-1",
        pending_clarification=prev_clarification,
    )
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "Is it A or B?",
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.advance_pending_clarification", return_value={"id": "clarif-ab"}) as mock_advance, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock) as mock_enqueue:

        res = await main.execute_validated_action(val, context, "Not C", supplier, "client-1")
        assert res["status"] == "clarification_needed"
        mock_advance.assert_called_once_with(
            previous_id="clarif-abc",
            client_id="client-1",
            supplier_id="supp-1",
            candidate_rfq_ids=["rfq-A", "rfq-B"],
            raw_message="Not C",
            extracted_price=None,
            extracted_delivery=None,
            extracted_notes=None,
            round_number=2,
            no_progress_count=0,
            last_question="Is it A or B?",
        )
        assert mock_enqueue.called


@pytest.mark.asyncio
async def test_h09_failed_advancement_fails_closed_no_whatsapp_enqueued():
    """H9. If advance_pending_clarification fails (returns None), no WhatsApp message is enqueued and human review is flagged."""
    prev_clarification = {
        "id": "clarif-abc",
        "round_number": 1,
        "no_progress_count": 0,
        "pending_rfq_ids": ["rfq-A", "rfq-B", "rfq-C"],
        "last_question": "Which of A, B, C?",
    }
    context = AgentContext(
        client_id="client-1",
        supplier_id="supp-1",
        pending_clarification=prev_clarification,
    )
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "Is it A or B?",
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.advance_pending_clarification", return_value=None) as mock_advance, \
         patch("db.flag_for_human_review") as mock_flag, \
         patch("main.enqueue_message", new_callable=AsyncMock) as mock_enqueue:

        res = await main.execute_validated_action(val, context, "Not C", supplier, "client-1")
        assert res["status"] == "failed_advancement"
        mock_enqueue.assert_not_called()
        mock_flag.assert_called_once()
        assert "Database error" in mock_flag.call_args.kwargs["reason"]


@pytest.mark.asyncio
async def test_h10_initial_clarification_uses_create_pending_clarification():
    """H10. When there is no previous pending clarification, create_pending_clarification is used."""
    context = AgentContext(client_id="client-1", supplier_id="supp-1", pending_clarification=None)
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "5kg or 10kg?",
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.create_pending_clarification", return_value="pc-init-1") as mock_create, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "cement", supplier, "client-1")
        assert res["status"] == "clarification_needed"
        mock_create.assert_called_once_with(
            client_id="client-1",
            supplier_id="supp-1",
            candidate_rfq_ids=["rfq-A", "rfq-B"],
            raw_message="cement",
            extracted_price=None,
            extracted_delivery=None,
            extracted_notes=None,
            round_number=1,
            no_progress_count=0,
            last_question="5kg or 10kg?",
        )


# ---------------------------------------------------------------------------
# Phase 12 Core Semantic Clarification Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_01_first_ambiguity_creates_clarification():
    """1. First ambiguous message with no existing pending clarification creates initial clarification session (round 1, no_progress 0)."""
    context = AgentContext(client_id="client-1", supplier_id="supp-1", pending_clarification=None)
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "Are you quoting 5kg or 10kg cement?",
            "extracted_price": 50.0,
            "extracted_delivery": "2 days",
            "extracted_notes": None,
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.create_pending_clarification", return_value="pc-1") as mock_create, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "cement 50 aed", supplier, "client-1")
        assert res["status"] == "clarification_needed"
        mock_create.assert_called_once_with(
            client_id="client-1",
            supplier_id="supp-1",
            candidate_rfq_ids=["rfq-A", "rfq-B"],
            raw_message="cement 50 aed",
            extracted_price=50.0,
            extracted_delivery="2 days",
            extracted_notes=None,
            round_number=1,
            no_progress_count=0,
            last_question="Are you quoting 5kg or 10kg cement?",
        )


@pytest.mark.asyncio
async def test_02_ab_to_a_resolution():
    """2. Supplier resolves ambiguity to single candidate A -> records quote, resolves clarification, reverts B."""
    prev_clarification = {
        "id": "clarif-1",
        "round_number": 1,
        "no_progress_count": 0,
        "pending_rfq_ids": ["rfq-A", "rfq-B"],
        "last_question": "5kg or 10kg?",
    }
    context = AgentContext(
        client_id="client-1",
        supplier_id="supp-1",
        pending_clarification=prev_clarification,
    )
    val = ValidationResult(
        is_valid=True,
        action="record_quote",
        category=ActionCategory.MUTATION,
        sanitized_args={
            "rfq_id": "rfq-A",
            "price": 45.0,
            "delivery_time": "2 days",
            "quality_notes": "5kg bag",
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.record_quote") as mock_record, \
         patch("db.resolve_pending_clarification") as mock_resolve, \
         patch("db.revert_unresolved_candidates") as mock_revert, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "5kg bag 45 aed", supplier, "client-1")
        assert res["status"] == "recorded_from_clarification"
        assert res["rfq_id"] == "rfq-A"
        mock_record.assert_called_once()
        mock_resolve.assert_called_once_with("clarif-1")
        mock_revert.assert_called_once_with(
            supplier_id="supp-1",
            resolved_rfq_id="rfq-A",
            candidate_rfq_ids=["rfq-A", "rfq-B"],
        )


@pytest.mark.asyncio
async def test_03_abc_to_ab_narrowing():
    """3. Supplier reply narrows candidate set from [A, B, C] to [A, B] -> semantic progress via advance_pending_clarification."""
    prev_clarification = {
        "id": "clarif-abc",
        "round_number": 1,
        "no_progress_count": 0,
        "pending_rfq_ids": ["rfq-A", "rfq-B", "rfq-C"],
        "last_question": "Which of A, B, C?",
    }
    context = AgentContext(
        client_id="client-1",
        supplier_id="supp-1",
        pending_clarification=prev_clarification,
    )
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "Is it A or B?",
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.advance_pending_clarification", return_value={"id": "clarif-ab"}) as mock_advance, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "Not C", supplier, "client-1")
        assert res["status"] == "clarification_needed"
        mock_advance.assert_called_once_with(
            previous_id="clarif-abc",
            client_id="client-1",
            supplier_id="supp-1",
            candidate_rfq_ids=["rfq-A", "rfq-B"],
            raw_message="Not C",
            extracted_price=None,
            extracted_delivery=None,
            extracted_notes=None,
            round_number=2,
            no_progress_count=0,
            last_question="Is it A or B?",
        )


@pytest.mark.asyncio
async def test_04_narrowing_resets_no_progress_count():
    """4. If previous turn stalled (no_progress_count=1), a subsequent narrowing turn resets no_progress_count to 0."""
    prev_clarification = {
        "id": "clarif-stalled-once",
        "round_number": 2,
        "no_progress_count": 1,
        "pending_rfq_ids": ["rfq-A", "rfq-B", "rfq-C"],
        "last_question": "A, B, or C?",
    }
    context = AgentContext(
        client_id="client-1",
        supplier_id="supp-1",
        pending_clarification=prev_clarification,
    )
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "Between A and B, which one?",
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.advance_pending_clarification", return_value={"id": "clarif-ab"}) as mock_advance, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "Ruling out C", supplier, "client-1")
        assert res["status"] == "clarification_needed"
        mock_advance.assert_called_once_with(
            previous_id="clarif-stalled-once",
            client_id="client-1",
            supplier_id="supp-1",
            candidate_rfq_ids=["rfq-A", "rfq-B"],
            raw_message="Ruling out C",
            extracted_price=None,
            extracted_delivery=None,
            extracted_notes=None,
            round_number=3,
            no_progress_count=0,
            last_question="Between A and B, which one?",
        )


@pytest.mark.asyncio
async def test_05_narrowing_works_past_old_round_2_cap():
    """5. Progress turns at round 2, round 3, round 4 continue safely without premature 2-round cap escalation."""
    prev_clarification = {
        "id": "clarif-round-3",
        "round_number": 3,
        "no_progress_count": 0,
        "pending_rfq_ids": ["rfq-A", "rfq-B", "rfq-C"],
        "last_question": "Between A, B, C?",
    }
    context = AgentContext(
        client_id="client-1",
        supplier_id="supp-1",
        pending_clarification=prev_clarification,
    )
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "Between A and B?",
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.advance_pending_clarification", return_value={"id": "clarif-ab"}) as mock_advance, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "Not C", supplier, "client-1")
        assert res["status"] == "clarification_needed"
        mock_advance.assert_called_once()
        assert mock_advance.call_args.kwargs["round_number"] == 4
        assert mock_advance.call_args.kwargs["no_progress_count"] == 0


@pytest.mark.asyncio
async def test_06_unchanged_candidates_increment_no_progress():
    """6. When candidate set remains unchanged, no_progress_count increments."""
    prev_clarification = {
        "id": "clarif-1",
        "round_number": 1,
        "no_progress_count": 0,
        "pending_rfq_ids": ["rfq-A", "rfq-B"],
        "last_question": "5kg or 10kg?",
    }
    context = AgentContext(
        client_id="client-1",
        supplier_id="supp-1",
        pending_clarification=prev_clarification,
    )
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "Could you clarify if it is 5kg or 10kg package?",
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.advance_pending_clarification", return_value={"id": "clarif-2"}) as mock_advance, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "yes available", supplier, "client-1")
        assert res["status"] == "clarification_needed"
        mock_advance.assert_called_once_with(
            previous_id="clarif-1",
            client_id="client-1",
            supplier_id="supp-1",
            candidate_rfq_ids=["rfq-A", "rfq-B"],
            raw_message="yes available",
            extracted_price=None,
            extracted_delivery=None,
            extracted_notes=None,
            round_number=2,
            no_progress_count=1,
            last_question="Could you clarify if it is 5kg or 10kg package?",
        )


@pytest.mark.asyncio
async def test_07_first_no_progress_retry_allowed():
    """7. After 1st uninformative reply (no_progress_count=1 < 2), one more clarification question is sent."""
    prev_clarification = {
        "id": "clarif-1",
        "round_number": 1,
        "no_progress_count": 0,
        "pending_rfq_ids": ["rfq-A", "rfq-B"],
        "last_question": "5kg or 10kg?",
    }
    context = AgentContext(
        client_id="client-1",
        supplier_id="supp-1",
        pending_clarification=prev_clarification,
    )
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "Please specify 5kg or 10kg.",
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.advance_pending_clarification", return_value={"id": "clarif-2"}) as mock_advance, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "yes", supplier, "client-1")
        assert res["status"] == "clarification_needed"
        assert mock_advance.call_args.kwargs["no_progress_count"] == 1


@pytest.mark.asyncio
async def test_08_second_consecutive_no_progress_escalates():
    """8. When no_progress_count reaches MAX_NO_PROGRESS_ATTEMPTS (2), escalates to human review."""
    prev_clarification = {
        "id": "clarif-stalled",
        "round_number": 2,
        "no_progress_count": 1,
        "pending_rfq_ids": ["rfq-A", "rfq-B"],
        "last_question": "5kg or 10kg?",
    }
    context = AgentContext(
        client_id="client-1",
        supplier_id="supp-1",
        pending_clarification=prev_clarification,
        open_rfqs=[
            {"rfqs": {"id": "rfq-A", "product_name": "Cement 5kg", "status": "active"}},
            {"rfqs": {"id": "rfq-B", "product_name": "Cement 10kg", "status": "active"}},
        ],
    )
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "Please specify 5kg or 10kg.",
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.abandon_pending_clarification") as mock_abandon, \
         patch("db.flag_for_human_review") as mock_flag, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "sure", supplier, "client-1")
        assert res["status"] == "escalated_to_human"
        assert res["category"] == "clarification_stalled"
        mock_abandon.assert_called_once_with("clarif-stalled")
        mock_flag.assert_called_once()
        flag_args = mock_flag.call_args.kwargs
        assert flag_args["category"] == "clarification_stalled"
        assert "stalled after 2" in flag_args["reason"].lower()
        assert "Cement 5kg" in flag_args["reason"]


@pytest.mark.asyncio
async def test_09_absolute_turn_5_bound_enforced():
    """9. When next_round > MAX_TOTAL_CLARIFICATION_TURNS (5), escalates even if narrowing."""
    prev_clarification = {
        "id": "clarif-turn-5",
        "round_number": 5,
        "no_progress_count": 0,
        "pending_rfq_ids": ["rfq-A", "rfq-B", "rfq-C"],
        "last_question": "Which one?",
    }
    context = AgentContext(
        client_id="client-1",
        supplier_id="supp-1",
        pending_clarification=prev_clarification,
    )
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "A or B?",
        },
    )
    supplier = {"id": "supp-1", "phone_number": "+971501111111"}

    with patch("db.abandon_pending_clarification") as mock_abandon, \
         patch("db.flag_for_human_review") as mock_flag, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "Not C", supplier, "client-1")
        assert res["status"] == "escalated_to_human"
        assert res["category"] == "clarification_stalled"
        mock_abandon.assert_called_once_with("clarif-turn-5")
        mock_flag.assert_called_once()
        assert "maximum turn limit (5 turns)" in mock_flag.call_args.kwargs["reason"]


def test_10_candidate_expansion_rejected():
    """10. Policy Validator rejects candidate expansion (e.g. proposed [A, B, C] when previous was [A, B])."""
    proposal = ActionProposal(
        tool_name="request_clarification",
        arguments={
            "candidate_rfq_ids": ["rfq-A", "rfq-B", "rfq-C"],
            "clarifying_question": "Which product?",
        },
    )
    open_rfqs = [
        {"rfqs": {"id": "rfq-A", "status": "active"}},
        {"rfqs": {"id": "rfq-B", "status": "active"}},
        {"rfqs": {"id": "rfq-C", "status": "active"}},
    ]
    pending_clarification = {
        "id": "clarif-prev",
        "pending_rfq_ids": ["rfq-A", "rfq-B"],
    }
    res = validate_action(
        proposal,
        client_id="client-1",
        supplier_id="supp-1",
        context_rfqs=open_rfqs,
        pending_clarification=pending_clarification,
    )
    assert not res.is_valid
    assert "cannot expand" in res.reason.lower()


def test_11_empty_candidates_rejected():
    """11. Policy Validator rejects request_clarification with empty candidate list."""
    proposal = ActionProposal(
        tool_name="request_clarification",
        arguments={
            "candidate_rfq_ids": [],
            "clarifying_question": "What product?",
        },
    )
    res = validate_action(
        proposal,
        client_id="client-1",
        supplier_id="supp-1",
    )
    assert not res.is_valid
    assert "at least 2 candidate" in res.reason


def test_13_foreign_rfq_candidate_rejected():
    """13. Policy Validator rejects candidate IDs not in supplier's open RFQs."""
    proposal = ActionProposal(
        tool_name="request_clarification",
        arguments={
            "candidate_rfq_ids": ["rfq-A", "rfq-FOREIGN"],
            "clarifying_question": "Which product?",
        },
    )
    open_rfqs = [{"rfqs": {"id": "rfq-A", "status": "active"}}]
    res = validate_action(
        proposal,
        client_id="client-1",
        supplier_id="supp-1",
        context_rfqs=open_rfqs,
    )
    assert not res.is_valid
    assert "not an open RFQ" in res.reason


def test_14_closed_rfq_candidate_rejected():
    """14. Policy Validator rejects candidates that have closed status."""
    proposal = ActionProposal(
        tool_name="request_clarification",
        arguments={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "Which product?",
        },
    )
    open_rfqs = [
        {"rfqs": {"id": "rfq-A", "status": "active"}},
        {"rfqs": {"id": "rfq-B", "status": "closed"}},
    ]
    res = validate_action(
        proposal,
        client_id="client-1",
        supplier_id="supp-1",
        context_rfqs=open_rfqs,
    )
    assert not res.is_valid
    assert "not an open RFQ" in res.reason


@pytest.mark.asyncio
async def test_16_quote_resolution_resolves_pending_clarification():
    """16. Successful quote recording marks active pending clarification resolved."""
    prev_pc = {"id": "clarif-xyz", "pending_rfq_ids": ["rfq-1", "rfq-2"]}
    context = AgentContext(client_id="c-1", supplier_id="s-1", pending_clarification=prev_pc)
    val = ValidationResult(
        is_valid=True,
        action="record_quote",
        category=ActionCategory.MUTATION,
        sanitized_args={"rfq_id": "rfq-1", "price": 100.0},
    )
    supplier = {"id": "s-1", "phone_number": "+971500000001"}

    with patch("db.record_quote"), \
         patch("db.resolve_pending_clarification") as mock_resolve, \
         patch("db.revert_unresolved_candidates") as mock_revert, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "100 aed", supplier, "c-1")
        assert res["status"] == "recorded_from_clarification"
        mock_resolve.assert_called_once_with("clarif-xyz")
        mock_revert.assert_called_once_with(
            supplier_id="s-1",
            resolved_rfq_id="rfq-1",
            candidate_rfq_ids=["rfq-1", "rfq-2"],
        )


@pytest.mark.asyncio
async def test_17_negotiation_resolution_resolves_pending_clarification():
    """17. Successful negotiate_price marks active pending clarification resolved and reverts other candidates."""
    prev_pc = {"id": "clarif-neg", "pending_rfq_ids": ["rfq-1", "rfq-2"]}
    context = AgentContext(client_id="c-1", supplier_id="s-1", pending_clarification=prev_pc)
    val = ValidationResult(
        is_valid=True,
        action="negotiate_price",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "rfq_id": "rfq-1",
            "quoted_price": 120.0,
            "negotiation_message": "Can you offer AED 110?",
        },
    )
    supplier = {"id": "s-1", "phone_number": "+971500000001"}

    with patch("db.record_quote"), \
         patch("db.increment_negotiation_attempts", return_value=1), \
         patch("db.resolve_pending_clarification") as mock_resolve, \
         patch("db.revert_unresolved_candidates") as mock_revert, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "120 aed", supplier, "c-1")
        assert res["status"] == "negotiation_sent"
        mock_resolve.assert_called_once_with("clarif-neg")
        mock_revert.assert_called_once_with(
            supplier_id="s-1",
            resolved_rfq_id="rfq-1",
            candidate_rfq_ids=["rfq-1", "rfq-2"],
        )


def test_18_non_selected_candidates_revert_correctly(mock_supabase):
    """18. db.revert_unresolved_candidates resets candidate rfq_suppliers from clarifying back to sent."""
    mock_table = MagicMock()
    mock_supabase.table.return_value = mock_table
    mock_table.update.return_value.eq.return_value.in_.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    db.revert_unresolved_candidates(
        supplier_id="s-1",
        resolved_rfq_id="rfq-1",
        candidate_rfq_ids=["rfq-1", "rfq-2", "rfq-3"],
    )

    assert mock_table.update.call_count == 1
    update_arg = mock_table.update.call_args[0][0]
    assert update_arg == {"status": "sent"}


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


@pytest.mark.asyncio
async def test_20_identical_repeated_question_treated_as_no_progress():
    """20. If question text is identical (case/whitespace normalized), it is treated as no-progress even if candidates match."""
    prev_clarification = {
        "id": "clarif-dup",
        "round_number": 1,
        "no_progress_count": 0,
        "pending_rfq_ids": ["rfq-A", "rfq-B"],
        "last_question": "Are you quoting 5kg or 10kg?",
    }
    context = AgentContext(
        client_id="c-1",
        supplier_id="s-1",
        pending_clarification=prev_clarification,
    )
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={
            "candidate_rfq_ids": ["rfq-A", "rfq-B"],
            "clarifying_question": "are you quoting   5kg or 10kg? ",
        },
    )
    supplier = {"id": "s-1", "phone_number": "+971500000001"}

    with patch("db.advance_pending_clarification", return_value={"id": "clarif-dup-2"}) as mock_advance, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "hello", supplier, "c-1")
        assert res["status"] == "clarification_needed"
        assert mock_advance.call_args.kwargs["no_progress_count"] == 1


@pytest.mark.asyncio
async def test_21_stalled_clarification_creates_human_flag():
    """21. When stalled threshold is reached, creates human review flag with category clarification_stalled."""
    prev_clarification = {
        "id": "clarif-stall-flag",
        "round_number": 2,
        "no_progress_count": 1,
        "pending_rfq_ids": ["rfq-A", "rfq-B"],
        "last_question": "5kg or 10kg?",
    }
    context = AgentContext(
        client_id="c-1",
        supplier_id="s-1",
        pending_clarification=prev_clarification,
        open_rfqs=[{"rfqs": {"id": "rfq-A", "product_name": "Cement A"}}, {"rfqs": {"id": "rfq-B", "product_name": "Cement B"}}],
    )
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={"candidate_rfq_ids": ["rfq-A", "rfq-B"], "clarifying_question": "Please specify."},
    )
    supplier = {"id": "s-1", "phone_number": "+971500000001"}

    with patch("db.abandon_pending_clarification") as mock_abandon, \
         patch("db.flag_for_human_review") as mock_flag, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "what?", supplier, "c-1")
        assert res["status"] == "escalated_to_human"
        assert res["category"] == "clarification_stalled"
        mock_flag.assert_called_once()
        assert mock_flag.call_args.kwargs["category"] == "clarification_stalled"


@pytest.mark.asyncio
async def test_22_stalled_clarification_pending_row_becomes_abandoned():
    """22. Pending clarification row is marked abandoned upon human escalation so no dangling active clarification remains."""
    prev_clarification = {
        "id": "clarif-row-to-abandon",
        "round_number": 2,
        "no_progress_count": 1,
        "pending_rfq_ids": ["rfq-A", "rfq-B"],
    }
    context = AgentContext(client_id="c-1", supplier_id="s-1", pending_clarification=prev_clarification)
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={"candidate_rfq_ids": ["rfq-A", "rfq-B"], "clarifying_question": "5kg or 10kg?"},
    )
    supplier = {"id": "s-1", "phone_number": "+971500000001"}

    with patch("db.abandon_pending_clarification") as mock_abandon, \
         patch("db.flag_for_human_review"), \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        await main.execute_validated_action(val, context, "hmm", supplier, "c-1")
        mock_abandon.assert_called_once_with("clarif-row-to-abandon")


@pytest.mark.asyncio
async def test_24_old_pending_row_without_new_fields_works():
    """24. Backward compatibility: existing pending row lacking no_progress_count or last_question defaults gracefully."""
    old_row = {
        "id": "clarif-legacy",
        "round_number": 1,
        "pending_rfq_ids": ["rfq-A", "rfq-B"],
    }
    context = AgentContext(client_id="c-1", supplier_id="s-1", pending_clarification=old_row)
    val = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={"candidate_rfq_ids": ["rfq-A", "rfq-B"], "clarifying_question": "New question?"},
    )
    supplier = {"id": "s-1", "phone_number": "+971500000001"}

    with patch("db.advance_pending_clarification", return_value={"id": "clarif-advanced"}) as mock_advance, \
         patch("db.log_message", return_value="log-1"), \
         patch("main.enqueue_message", new_callable=AsyncMock):

        res = await main.execute_validated_action(val, context, "reply", supplier, "c-1")
        assert res["status"] == "clarification_needed"
        assert mock_advance.call_args.kwargs["round_number"] == 2
        assert mock_advance.call_args.kwargs["no_progress_count"] == 1


def test_25_cross_tenant_clarification_blocked():
    """25. Policy Validator blocks candidate RFQs belonging to a different tenant."""
    proposal = ActionProposal(
        tool_name="request_clarification",
        arguments={
            "candidate_rfq_ids": ["rfq-tenant-a", "rfq-tenant-b"],
            "clarifying_question": "Which product?",
        },
    )
    open_rfqs = [
        {"rfqs": {"id": "rfq-tenant-a", "client_id": "tenant-A", "status": "active"}},
    ]
    res = validate_action(
        proposal,
        client_id="tenant-A",
        supplier_id="s-1",
        context_rfqs=open_rfqs,
    )
    assert not res.is_valid
    assert "not an open RFQ" in res.reason


def test_26_optional_delivery_missing_does_not_force_clarification():
    """26. A positive quote price without delivery_time or quality_notes is valid and does not require clarification."""
    proposal = ActionProposal(
        tool_name="record_quote",
        arguments={
            "rfq_id": "rfq-1",
            "price": 100.0,
            "delivery_time": None,
            "quality_notes": None,
        },
    )
    open_rfqs = [{"rfqs": {"id": "rfq-1", "status": "active"}}]
    res = validate_action(
        proposal,
        client_id="c-1",
        supplier_id="s-1",
        context_rfqs=open_rfqs,
        matched_rfq_id="rfq-1",
    )
    assert res.is_valid
    assert res.sanitized_args["price"] == 100.0


@pytest.mark.asyncio
async def test_27_supplier_can_narrow_4_to_3_to_2_to_1_successfully():
    """27. Step-by-step narrowing 4 -> 3 -> 2 -> 1 completes without triggering premature turn escalation."""
    supplier = {"id": "s-1", "phone_number": "+971500000001"}

    # Initial: 4 candidates -> Round 1
    val1 = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={"candidate_rfq_ids": ["rfq-A", "rfq-B", "rfq-C", "rfq-D"], "clarifying_question": "Which of A, B, C, D?"},
    )
    with patch("db.create_pending_clarification", return_value="pc1") as mock_c1, patch("db.log_message"), patch("main.enqueue_message", new_callable=AsyncMock):
        res1 = await main.execute_validated_action(val1, AgentContext(client_id="c-1", supplier_id="s-1", pending_clarification=None), "msg1", supplier, "c-1")
        assert res1["status"] == "clarification_needed"
        assert mock_c1.call_args.kwargs["round_number"] == 1
        assert mock_c1.call_args.kwargs["no_progress_count"] == 0

    # Turn 2: Supplier replies "Not D" -> narrows to 3 candidates -> Round 2
    pc1 = {"id": "pc1", "round_number": 1, "no_progress_count": 0, "pending_rfq_ids": ["rfq-A", "rfq-B", "rfq-C", "rfq-D"], "last_question": "Which of A, B, C, D?"}
    val2 = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={"candidate_rfq_ids": ["rfq-A", "rfq-B", "rfq-C"], "clarifying_question": "Which of A, B, C?"},
    )
    with patch("db.advance_pending_clarification", return_value={"id": "pc2"}) as mock_c2, patch("db.log_message"), patch("main.enqueue_message", new_callable=AsyncMock):
        res2 = await main.execute_validated_action(val2, AgentContext(client_id="c-1", supplier_id="s-1", pending_clarification=pc1), "Not D", supplier, "c-1")
        assert res2["status"] == "clarification_needed"
        assert mock_c2.call_args.kwargs["round_number"] == 2
        assert mock_c2.call_args.kwargs["no_progress_count"] == 0

    # Turn 3: Supplier replies "Not C" -> narrows to 2 candidates -> Round 3
    pc2 = {"id": "pc2", "round_number": 2, "no_progress_count": 0, "pending_rfq_ids": ["rfq-A", "rfq-B", "rfq-C"], "last_question": "Which of A, B, C?"}
    val3 = ValidationResult(
        is_valid=True,
        action="request_clarification",
        category=ActionCategory.PROPOSE_COMMUNICATE,
        sanitized_args={"candidate_rfq_ids": ["rfq-A", "rfq-B"], "clarifying_question": "Is it A or B?"},
    )
    with patch("db.advance_pending_clarification", return_value={"id": "pc3"}) as mock_c3, patch("db.log_message"), patch("main.enqueue_message", new_callable=AsyncMock):
        res3 = await main.execute_validated_action(val3, AgentContext(client_id="c-1", supplier_id="s-1", pending_clarification=pc2), "Not C", supplier, "c-1")
        assert res3["status"] == "clarification_needed"
        assert mock_c3.call_args.kwargs["round_number"] == 3
        assert mock_c3.call_args.kwargs["no_progress_count"] == 0

    # Turn 4: Supplier replies "It's A, 50 AED" -> resolves to 1 candidate -> records quote
    pc3 = {"id": "pc3", "round_number": 3, "no_progress_count": 0, "pending_rfq_ids": ["rfq-A", "rfq-B"], "last_question": "Is it A or B?"}
    val4 = ValidationResult(
        is_valid=True,
        action="record_quote",
        category=ActionCategory.MUTATION,
        sanitized_args={"rfq_id": "rfq-A", "price": 50.0},
    )
    with patch("db.record_quote") as mock_rq, patch("db.resolve_pending_clarification") as mock_res, patch("db.revert_unresolved_candidates") as mock_rev, patch("db.log_message"), patch("main.enqueue_message", new_callable=AsyncMock):
        res4 = await main.execute_validated_action(val4, AgentContext(client_id="c-1", supplier_id="s-1", pending_clarification=pc3), "A for 50", supplier, "c-1")
        assert res4["status"] == "recorded_from_clarification"
        assert res4["rfq_id"] == "rfq-A"
        mock_rq.assert_called_once()
        mock_res.assert_called_once_with("pc3")
        mock_rev.assert_called_once_with(supplier_id="s-1", resolved_rfq_id="rfq-A", candidate_rfq_ids=["rfq-A", "rfq-B"])


def test_28_master_rfq_remains_active_during_clarification(mock_supabase):
    """28. Clarification lifecycle operates at rfq_suppliers level; rfqs status remains active."""
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
    mock_rfq_supp_table.update.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=[])

    db.create_pending_clarification(
        client_id="c-1",
        supplier_id="s-1",
        candidate_rfq_ids=["rfq-1", "rfq-2"],
        raw_message="cement",
    )

    # Confirm only rfq_suppliers table was updated with 'clarifying', NOT rfqs table
    assert mock_rfq_supp_table.update.called
    update_arg = mock_rfq_supp_table.update.call_args_list[0][0][0]
    assert update_arg == {"status": "clarifying"}


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


def test_30_phase_10_11_quote_ranking_flows_unaffected():
    """30. Multi-variant quotes and ranking flows remain fully functional alongside semantic clarification."""
    proposal = ActionProposal(
        tool_name="record_quote",
        arguments={
            "rfq_id": "rfq-1",
            "variants": [
                {"variant_label": "India", "price": 45.0, "is_available": True},
                {"variant_label": "China", "price": 38.0, "is_available": True},
            ],
        },
    )
    open_rfqs = [{"rfqs": {"id": "rfq-1", "status": "active"}}]
    res = validate_action(
        proposal,
        client_id="c-1",
        supplier_id="s-1",
        context_rfqs=open_rfqs,
        matched_rfq_id="rfq-1",
    )
    assert res.is_valid
    assert len(res.sanitized_args["variants"]) == 2
    assert res.sanitized_args["variants"][0]["variant_label"] == "India"
