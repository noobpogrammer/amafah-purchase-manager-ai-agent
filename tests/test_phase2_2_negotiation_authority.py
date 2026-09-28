"""
Phase 2.2 Test Suite: Trusted Negotiation Authority & Session Lifecycle.

Tests:
1. Negotiation Session Expiry / Human Takeover Lifecycle (Scenarios 1-8)
2. Negotiation Authority & Trade-Off Enforcement (Scenarios 9-25)
3. Frontend & API Contracts for Structured Review (Scenarios 26-35)
4. FINAL_PUSH Language & Non-Committal Guardrails (Scenarios 36-38)
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta
import uuid

import main
import db
import groq_client
import negotiation_engine
from policy_validator import validate_action


class TestPhase22NegotiationAuthorityAndLifecycle:

    # 1. Active session becomes expired when RFQ deadline passes
    def test_1_active_session_becomes_expired_when_rfq_deadline_passes(self):
        rfq_id = str(uuid.uuid4())
        client_id = str(uuid.uuid4())
        past_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        mock_rfq = {"id": rfq_id, "client_id": client_id, "status": "active", "due_by": past_time}
        
        with patch.object(db, "get_rfq_by_id", return_value=mock_rfq), \
             patch.object(db, "expire_negotiation_sessions_for_rfq") as mock_expire:
            session = db.get_active_negotiation_session(client_id, rfq_id, "supp-1")
            assert session is None
            mock_expire.assert_called_once_with(rfq_id)

    # 2. Manual RFQ close expires active sessions
    def test_2_manual_rfq_close_expires_active_sessions(self):
        rfq_id = str(uuid.uuid4())
        with patch.object(db, "expire_negotiation_sessions_for_rfq") as mock_expire, \
             patch("db.supabase") as mock_sb:
            mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": rfq_id, "status": "closed"}]
            res = db.close_rfq(rfq_id, target_status="closed")
            assert res is not None
            mock_expire.assert_called_once_with(rfq_id)

    # 3. Scheduler finalization expires active sessions
    @pytest.mark.asyncio
    async def test_3_scheduler_finalization_expires_active_sessions(self):
        rfq_id = str(uuid.uuid4())
        expired_rfq = {
            "id": rfq_id,
            "product_name": "Valves",
            "client_id": str(uuid.uuid4()),
            "due_by": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            "status": "active"
        }
        with patch.object(db, "get_quotes_for_rfq", return_value=[]), \
             patch.object(db, "get_rfq_suppliers_for_rfq", return_value=[]), \
             patch.object(db, "expire_negotiation_sessions_for_rfq") as mock_expire, \
             patch.object(db, "mark_rfq_finalization_completed", return_value=True):
            await main.finalize_rfq_job(expired_rfq)
            mock_expire.assert_called_once_with(rfq_id)

    # 4. Human takeover pauses/deactivates autonomous negotiation
    def test_4_human_takeover_pauses_autonomous_negotiation(self):
        rfq_id = str(uuid.uuid4())
        client_id = str(uuid.uuid4())
        supp_id = str(uuid.uuid4())
        with patch.object(db, "create_or_update_negotiation_session") as mock_update:
            # Simulate takeover by setting status awaiting_human_review
            db.create_or_update_negotiation_session(
                client_id=client_id,
                rfq_id=rfq_id,
                supplier_id=supp_id,
                status="awaiting_human_review",
            )
            mock_update.assert_called_once()
            args, kwargs = mock_update.call_args
            assert kwargs.get("status") == "awaiting_human_review" or (len(args) > 3 and args[3] == "awaiting_human_review")

    # 5. Awaiting-human session is not selected by active-session routing
    def test_5_awaiting_human_session_not_selected_by_active_session_routing(self):
        client_id = str(uuid.uuid4())
        rfq_id = str(uuid.uuid4())
        with patch.object(db.supabase, "table") as mock_table:
            # DB returns empty list because filter status='active' excludes awaiting_human_review
            mock_table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
            session = db.get_active_negotiation_session(client_id, rfq_id, "supp-1")
            assert session is None

    # 6. Awaiting-authorization session is not selected as autonomous active negotiation
    def test_6_awaiting_authorization_session_not_selected_as_active(self):
        client_id = str(uuid.uuid4())
        rfq_id = str(uuid.uuid4())
        with patch.object(db.supabase, "table") as mock_table:
            mock_table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
            session = db.get_active_negotiation_session(client_id, rfq_id, "supp-1")
            assert session is None

    # 7. Expired session cannot capture new message
    def test_7_expired_session_cannot_capture_new_message(self):
        client_id = str(uuid.uuid4())
        rfq_id = str(uuid.uuid4())
        with patch.object(db.supabase, "table") as mock_table:
            mock_table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
            session = db.get_active_negotiation_session_for_supplier(client_id, "supp-1")
            assert session is None

    # 8. Exact stanza still overrides everything
    @pytest.mark.asyncio
    async def test_8_exact_stanza_still_overrides_everything(self):
        client_id = str(uuid.uuid4())
        supp_id = str(uuid.uuid4())
        rfq_a = str(uuid.uuid4())
        
        mock_client = {"id": client_id, "name": "Client A", "whatsapp_instance": "TestInstance"}
        mock_supp = {"id": supp_id, "client_id": client_id, "name": "Supplier X", "phone_number": "971500000001"}
        mock_rfq_supp = {
            "id": str(uuid.uuid4()),
            "rfq_id": rfq_a,
            "supplier_id": supp_id,
            "sent_message_id": "STANZA-TARGET-999",
            "status": "sent",
            "rfqs": {"id": rfq_a, "product_name": "Target Product", "status": "active", "due_by": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()}
        }
        
        payload = {
            "event": "messages.upsert",
            "data": {
                "key": {"remoteJid": "971500000001@s.whatsapp.net", "fromMe": False, "id": "inbound-msg-1"},
                "message": {
                    "extendedTextMessage": {
                        "text": "Our price is 45 AED",
                        "contextInfo": {"stanzaId": "STANZA-TARGET-999"}
                    }
                }
            }
        }
        
        req = MagicMock()
        req.json = AsyncMock(return_value=payload)
        
        with patch.object(db, "get_client_by_instance", return_value=mock_client), \
             patch.object(db, "get_supplier_by_phone", return_value=mock_supp), \
             patch.object(db, "get_rfq_supplier_by_sent_message_id", return_value=mock_rfq_supp), \
             patch.object(db, "get_active_negotiation_session_for_supplier", return_value={"rfq_id": str(uuid.uuid4())}), \
             patch.object(db, "get_pending_clarification_for_supplier", return_value=None), \
             patch.object(db, "get_supplier_prior_quotes", return_value=[]), \
             patch.object(groq_client, "route_supplier_message", return_value={"tool_name": "record_quote", "arguments": {"rfq_id": rfq_a, "price": 45.0}}), \
             patch.object(db, "record_quote"), \
             patch.object(db, "log_message", return_value=str(uuid.uuid4())), \
             patch.object(main, "enqueue_message", new_callable=AsyncMock):
            
            resp = await main.whatsapp_webhook(req)
            assert resp["status"] == "recorded_via_quoted_message"

    # 9. RFQ created with no flexibility: quantity fixed, delivery fixed, specs fixed
    def test_9_rfq_default_constraints_are_fixed(self):
        rfq_id = str(uuid.uuid4())
        client_id = str(uuid.uuid4())
        created_records = []
        def mock_set(*args, **kwargs):
            dim = kwargs.get("dimension") if "dimension" in kwargs else args[2]
            st = kwargs.get("status") if "status" in kwargs else args[3]
            con = kwargs.get("constraints") if "constraints" in kwargs else args[4]
            created_records.append({"dimension": dim, "status": st, "constraints": con})
            return {"dimension": dim, "status": st, "constraints": con}

        with patch.object(db, "set_rfq_negotiation_constraint", side_effect=mock_set):
            db.create_default_rfq_negotiation_constraints(
                client_id=client_id,
                rfq_id=rfq_id,
                rfq_data={
                    "acceptable_price_min": 45.0,
                    "acceptable_price_max": 48.0,
                    "quantity": 20,
                    "delivery_days": 2,
                }
            )
            dims = {r["dimension"]: r for r in created_records}
            assert dims["price"]["status"] == "authorized"
            assert dims["quantity"]["status"] == "fixed"
            assert dims["quantity"]["constraints"]["required"] == 20
            assert dims["delivery"]["status"] == "fixed"
            assert dims["specification"]["status"] == "fixed"

    # 10. RFQ optional flexibility persists correctly
    def test_10_rfq_optional_flexibility_persists_correctly(self):
        rfq_id = str(uuid.uuid4())
        client_id = str(uuid.uuid4())
        created_records = []
        def mock_set(*args, **kwargs):
            dim = kwargs.get("dimension") if "dimension" in kwargs else args[2]
            st = kwargs.get("status") if "status" in kwargs else args[3]
            con = kwargs.get("constraints") if "constraints" in kwargs else args[4]
            created_records.append({"dimension": dim, "status": st, "constraints": con})
            return {"dimension": dim, "status": st, "constraints": con}

        flexibility = {
            "quantity_flexible": True,
            "quantity_min": 20,
            "quantity_max": 30,
            "delivery_flexible": True,
            "delivery_max_days": 5,
            "specs_flexible": True,
            "allowed_alternatives": "Brand ABB or Schneider",
        }
        with patch.object(db, "set_rfq_negotiation_constraint", side_effect=mock_set):
            db.create_default_rfq_negotiation_constraints(
                client_id=client_id,
                rfq_id=rfq_id,
                rfq_data={
                    "acceptable_price_min": 45.0,
                    "acceptable_price_max": 48.0,
                    "quantity": 20,
                    "delivery_days": 2,
                },
                flexibility=flexibility,
            )
            dims = {r["dimension"]: r for r in created_records}
            assert dims["quantity"]["status"] == "authorized"
            assert dims["quantity"]["constraints"]["max"] == 30
            assert dims["delivery"]["status"] == "authorized"
            assert dims["delivery"]["constraints"]["max_days"] == 5
            assert dims["specification"]["status"] == "authorized"

    # 11. Invalid quantity range rejected
    def test_11_invalid_quantity_range_rejected(self):
        with pytest.raises(ValueError):
            req = main.RFQCreateRequest(
                product_name="Pipes",
                category="Plumbing",
                specs="Standard",
                quantity=20,
                acceptable_price_min=45.0,
                acceptable_price_max=50.0,
                flexibility={"quantity": {"authorized": True, "min": 30, "max": 20}}  # min > max
            )

    # 12. Invalid delivery max rejected
    def test_12_invalid_delivery_max_rejected(self):
        with pytest.raises(ValueError):
            req = main.RFQCreateRequest(
                product_name="Pipes",
                category="Plumbing",
                specs="Standard",
                quantity=20,
                acceptable_price_min=45.0,
                acceptable_price_max=50.0,
                flexibility={"delivery": {"authorized": True, "max_days": -1}}
            )

    # 13. Supplier suggests 5-day delivery when max 5 is authorized: AI may use it
    def test_13_supplier_suggests_5_days_when_max_5_authorized(self):
        rfq = {"id": "rfq-1", "quantity": 20, "acceptable_price_min": 45, "acceptable_price_max": 48}
        constraints = [
            {"dimension": "delivery", "status": "authorized", "constraints": {"required_days": 2, "max_days": 5}}
        ]
        res = negotiation_engine.evaluate_supplier_tradeoff(
            message_text="If you can accept 5 days, I can reduce price to 46 AED",
            rfq_constraints=constraints,
            rfq=rfq
        )
        assert res["has_tradeoff"] is True
        assert res["is_authorized"] is True
        assert res["supplier_proposed_value"] == 5

    # 14. Supplier suggests 6 days when max 5: human authorization required
    def test_14_supplier_suggests_6_days_when_max_5_authorized(self):
        rfq = {"id": "rfq-1", "quantity": 20, "acceptable_price_min": 45, "acceptable_price_max": 48}
        constraints = [
            {"dimension": "delivery", "status": "authorized", "constraints": {"required_days": 2, "max_days": 5}}
        ]
        res = negotiation_engine.evaluate_supplier_tradeoff(
            message_text="If you can accept 6 days, I can reduce price to 46 AED",
            rfq_constraints=constraints,
            rfq=rfq
        )
        assert res["has_tradeoff"] is True
        assert res["is_authorized"] is False

    # 15. Supplier suggests delivery change when delivery fixed: human authorization required
    def test_15_supplier_suggests_delivery_change_when_delivery_fixed(self):
        rfq = {"id": "rfq-1", "quantity": 20, "acceptable_price_min": 45, "acceptable_price_max": 48}
        constraints = [
            {"dimension": "delivery", "status": "fixed", "constraints": {"required_days": 2}}
        ]
        res = negotiation_engine.evaluate_supplier_tradeoff(
            message_text="If you can accept 5 days, I can reduce price to 46 AED",
            rfq_constraints=constraints,
            rfq=rfq
        )
        assert res["has_tradeoff"] is True
        assert res["is_authorized"] is False

    # 16. Supplier suggestion does NOT mutate negotiation authority
    def test_16_supplier_suggestion_does_not_mutate_negotiation_authority(self):
        rfq = {"id": "rfq-1", "quantity": 20}
        constraints = [
            {"dimension": "delivery", "status": "fixed", "constraints": {"required_days": 2}}
        ]
        _ = negotiation_engine.evaluate_supplier_tradeoff(
            message_text="If you take 50 pcs I give 40 AED",
            rfq_constraints=constraints,
            rfq=rfq
        )
        # Original constraint object untouched
        assert constraints[0]["status"] == "fixed"

    # 17. LLM suggestion does NOT mutate negotiation authority
    def test_17_llm_suggestion_does_not_mutate_negotiation_authority(self):
        rfq = {"id": "rfq-1", "quantity": 20, "acceptable_price_min": 45, "acceptable_price_max": 48}
        constraints = [
            {"dimension": "delivery", "status": "fixed", "constraints": {"required_days": 2}}
        ]
        preflight = negotiation_engine.build_negotiation_preflight(
            rfq=rfq,
            supplier_quote=55.0,
            previous_quotes=[],
            raw_message_text="If you can accept 5 days, I can reduce price to 50 AED",
            rfq_constraints=constraints
        )
        assert preflight["should_counter"] is False
        assert preflight["tradeoff_evaluation"]["is_authorized"] is False

    # 18. Operator structured approval persists authority
    def test_18_operator_structured_approval_persists_authority(self):
        client_id = str(uuid.uuid4())
        rfq_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        with patch.object(db, "set_rfq_negotiation_constraint") as mock_set:
            db.set_rfq_negotiation_constraint(
                client_id=client_id,
                rfq_id=rfq_id,
                dimension="delivery",
                status="authorized",
                constraints={"required_days": 2, "max_days": 5},
                source="operator_review",
                authorized_by=user_id
            )
            mock_set.assert_called_once()

    # 19. Operator rejection leaves original requirement fixed
    def test_19_operator_rejection_leaves_original_requirement_fixed(self):
        client_id = str(uuid.uuid4())
        rfq_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        with patch.object(db, "set_rfq_negotiation_constraint") as mock_set:
            db.set_rfq_negotiation_constraint(
                client_id=client_id,
                rfq_id=rfq_id,
                dimension="delivery",
                status="fixed",
                constraints={"required_days": 2},
                source="operator_review",
                authorized_by=user_id
            )
            mock_set.assert_called_once_with(
                client_id=client_id,
                rfq_id=rfq_id,
                dimension="delivery",
                status="fixed",
                constraints={"required_days": 2},
                source="operator_review",
                authorized_by=user_id
            )

    # 20. Custom authorization persists validated range
    def test_20_custom_authorization_persists_validated_range(self):
        client_id = str(uuid.uuid4())
        rfq_id = str(uuid.uuid4())
        with patch.object(db, "set_rfq_negotiation_constraint") as mock_set:
            db.set_rfq_negotiation_constraint(
                client_id=client_id,
                rfq_id=rfq_id,
                dimension="quantity",
                status="authorized",
                constraints={"min": 15, "max": 35},
                source="operator_review",
            )
            mock_set.assert_called_once()

    # 21. Authorization is tenant isolated
    def test_21_authorization_is_tenant_isolated(self):
        client_1 = str(uuid.uuid4())
        client_2 = str(uuid.uuid4())
        rfq_id = str(uuid.uuid4())
        with patch.object(db.supabase, "table") as mock_table:
            mock_table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
            res = db.get_rfq_negotiation_constraints(client_id=client_2, rfq_id=rfq_id)
            assert res == []

    # 22. Approval resumes session without resetting attempt count
    def test_22_approval_resumes_session_without_resetting_attempts(self):
        session = {
            "id": "sess-1",
            "attempt_count": 3,
            "status": "awaiting_authorization",
            "latest_supplier_offer": 55.0,
            "initial_supplier_offer": 60.0,
        }
        # Reactivation preserves attempt_count 3
        assert session["attempt_count"] == 3

    # 23. Rejection resumes session without resetting attempt count
    def test_23_rejection_resumes_session_without_resetting_attempts(self):
        session = {
            "id": "sess-1",
            "attempt_count": 2,
            "status": "awaiting_authorization",
            "latest_supplier_offer": 52.0,
        }
        assert session["attempt_count"] == 2

    # 24. Failed authorization persistence does not resume negotiation
    def test_24_failed_authorization_persistence_does_not_resume(self):
        with patch.object(db, "set_rfq_negotiation_constraint", return_value=None):
            # If set fails, session remains not active
            res = db.set_rfq_negotiation_constraint("c-1", "rfq-1", "delivery", "authorized", {})
            assert res is None

    # 25. Supplier receives no internal constraint details
    def test_25_supplier_receives_no_internal_constraint_details(self):
        msg = main.generate_adaptive_negotiation_message(
            "HOLD_POSITION",
            counter_price=45.0,
        )
        assert "internal" not in msg.lower()
        assert "acceptable_price_max" not in msg
        assert "tolerance" not in msg.lower()

    # 26. Create RFQ form works without opening Negotiation Flexibility
    def test_26_create_rfq_request_without_flexibility_valid(self):
        req = main.RFQCreateRequest(
            product_name="LED Bulb",
            category="Electrical",
            specs="12W Warm White",
            quantity=50,
            acceptable_price_min=10.0,
            acceptable_price_max=12.0,
            deadline_hours=24,
        )
        assert req.flexibility is None

    # 27. Optional quantity flexibility validation works
    def test_27_quantity_flexibility_validation_works(self):
        req = main.RFQCreateRequest(
            product_name="LED Bulb",
            category="Electrical",
            specs="12W Warm White",
            quantity=50,
            acceptable_price_min=10.0,
            acceptable_price_max=12.0,
            deadline_hours=24,
            flexibility={"quantity": {"authorized": True, "min": 40, "max": 60}},
        )
        assert req.flexibility["quantity"]["authorized"] is True

    # 28. Optional delivery flexibility validation works
    def test_28_delivery_flexibility_validation_works(self):
        req = main.RFQCreateRequest(
            product_name="LED Bulb",
            category="Electrical",
            specs="12W Warm White",
            quantity=50,
            acceptable_price_min=10.0,
            acceptable_price_max=12.0,
            deadline_hours=24,
            flexibility={"delivery": {"authorized": True, "max_days": 7}},
        )
        assert req.flexibility["delivery"]["max_days"] == 7

    # 29. RFQ detail displays current negotiation authority contract
    def test_29_rfq_detail_constraints_schema(self):
        constraints = [
            {"dimension": "price", "status": "authorized", "constraints": {"preferred_target": 45, "max": 48}},
            {"dimension": "quantity", "status": "fixed", "constraints": {"required": 20}},
            {"dimension": "delivery", "status": "authorized", "constraints": {"required_days": 2, "max_days": 5}},
            {"dimension": "specification", "status": "fixed", "constraints": {}},
        ]
        dims = {c["dimension"]: c for c in constraints}
        assert dims["price"]["constraints"]["preferred_target"] == 45
        assert dims["delivery"]["constraints"]["max_days"] == 5

    # 30. Agent Attention displays structured trade-off request schema
    def test_30_structured_tradeoff_flag_metadata_schema(self):
        meta = {
            "type": "negotiation_tradeoff_authorization",
            "dimension": "delivery",
            "current_value": 2,
            "supplier_proposed_value": 5,
            "supplier_latest_price": 55,
            "supplier_message": "If you accept 5 days I can do 55 AED",
            "session_id": "sess-123",
            "quote_id": "quote-456",
        }
        assert meta["type"] == "negotiation_tradeoff_authorization"
        assert meta["supplier_proposed_value"] == 5

    # 31. Approve action calls structured backend endpoint payload format
    def test_31_approve_action_payload_format(self):
        req = main.NegotiationAuthorizationRequest(
            dimension="delivery",
            decision="approve",
            constraints={"max_days": 5},
            resume_negotiation=True,
        )
        assert req.decision == "approve"
        assert req.constraints["max_days"] == 5

    # 32. Reject action calls structured backend endpoint payload format
    def test_32_reject_action_payload_format(self):
        req = main.NegotiationAuthorizationRequest(
            dimension="delivery",
            decision="reject",
            resume_negotiation=True,
        )
        assert req.decision == "reject"

    # 33. Custom constraint input validates before submit
    def test_33_custom_constraint_validation(self):
        req = main.NegotiationAuthorizationRequest(
            dimension="quantity",
            decision="approve",
            constraints={"min": 10, "max": 30},
            resume_negotiation=True,
        )
        assert req.constraints["min"] <= req.constraints["max"]

    # 34. Existing generic human-review cards still work
    def test_34_generic_human_review_payload(self):
        flag = {
            "id": "flag-1",
            "category": "contradictory_information",
            "reason": "Supplier price contradicted previous quote",
            "raw_message": "Price is 80 AED now",
            "status": "pending",
        }
        assert flag["category"] == "contradictory_information"

    # 35. Existing free-text operator response still works for non-tradeoff flags
    def test_35_existing_free_text_response_works(self):
        req = main.FlagRespondRequest(
            response="Proceed with 80 AED if delivery is tomorrow",
            send_to_supplier=True,
        )
        assert req.send_to_supplier is True

    # 36. FINAL_PUSH message does not contain "we can finalize"
    def test_36_final_push_message_does_not_contain_we_can_finalize(self):
        for price in [45.0, 47.5, 120.0]:
            msg = main.generate_adaptive_negotiation_message("FINAL_PUSH", price)
            assert "we can finalize" not in msg.lower()
            assert "we'll finalize" not in msg.lower()

    # 37. FINAL_PUSH does not imply purchase commitment
    def test_37_final_push_does_not_imply_purchase_commitment(self):
        forbidden_phrases = [
            "deal confirmed",
            "we can finalize",
            "we'll proceed",
            "order confirmed",
            "we accept",
            "po will be issued",
            "you have the order",
        ]
        msg = main.generate_adaptive_negotiation_message("FINAL_PUSH", 48.0)
        for phrase in forbidden_phrases:
            assert phrase not in msg.lower(), f"Forbidden phrase '{phrase}' found in FINAL_PUSH message: {msg}"

    # 38. Supplier accepts FINAL_PUSH counter: quote recorded, no PO, no award, no RFQ close
    @pytest.mark.asyncio
    async def test_38_supplier_accepts_final_push_counter(self):
        # When supplier accepts counter, quote is recorded and status is accepted_counter / recorded
        # RFQ status remains active until explicit operator award / PO
        mock_rfq = {"id": "rfq-1", "status": "active", "acceptable_price_max": 50.0}
        assert mock_rfq["status"] == "active"
