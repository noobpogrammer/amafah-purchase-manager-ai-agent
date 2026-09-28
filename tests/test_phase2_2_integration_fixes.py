"""
Integration Test Suite for Phase 2.2 Post-Integration Fixes:
- Integration Test A: RFQ Flexibility Contract (Nested payload via HTTP POST /rfq/create)
- Integration Test B: Default Fixed Authority via HTTP POST /rfq/create
- Integration Test C: Structured Approval Endpoint (POST /flags/{flag_id}/negotiation-authorization)
- Integration Test D: Approval Failure Handling (Safe rollback, flag remains pending)
- Integration Test E: Delivery Unknown Baseline (NULL required_delivery_days, no 2-day assumption)
- Integration Test F: Delivery Known Baseline (2 days required, authorized within max, review beyond)
- Integration Test G: Atomic RFQ + Constraints Creation Failure
- Integration Test H: Real Webhook Chain (Quote -> Trade-Off -> Flag -> Structured Approval -> Resumed Negotiation)
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import uuid
from fastapi.testclient import TestClient

import main
import db
import groq_client
import negotiation_engine


@pytest.fixture
def auth_client():
    client_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    
    app_client = TestClient(main.app)
    main.app.dependency_overrides[main.get_current_user] = lambda: {
        "id": user_id,
        "client_id": client_id,
        "email": "procurement@amafah.com",
    }
    yield app_client, client_id, user_id
    main.app.dependency_overrides.clear()


class TestPhase22IntegrationFixes:

    # ============================================================
    # INTEGRATION TEST A — RFQ FLEXIBILITY CONTRACT
    # ============================================================
    def test_integration_a_rfq_flexibility_contract_nested(self, auth_client):
        client, client_id, user_id = auth_client
        rfq_id = str(uuid.uuid4())

        payload = {
            "product_name": "PVC Pipe",
            "category": "Plumbing",
            "specs": "Schedule 40",
            "quantity": 20,
            "acceptable_price_min": 45,
            "acceptable_price_max": 48,
            "deadline_hours": 2,
            "required_delivery_days": 2,
            "flexibility": {
                "quantity": {
                    "authorized": True,
                    "min": 20,
                    "max": 30,
                },
                "delivery": {
                    "authorized": True,
                    "max_days": 5,
                },
            },
        }

        # Mock supplier matching and RPC response
        mock_rfq_row = {
            "id": rfq_id,
            "client_id": client_id,
            "product_name": "PVC Pipe",
            "category": "Plumbing",
            "specs": "Schedule 40",
            "quantity": 20,
            "acceptable_price_min": 45.0,
            "acceptable_price_max": 48.0,
            "deadline_hours": 2,
            "required_delivery_days": 2,
            "status": "active",
        }
        mock_matched_suppliers = [{"id": "supp-1", "name": "Pipe Supplier LLC", "phone_number": "+971501111111"}]

        with patch("db.supabase") as mock_sb, \
             patch.object(db, "get_suppliers_by_category", return_value=mock_matched_suppliers), \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enqueue:
            
            # Setup RPC mock return
            mock_sb.rpc.return_value.execute.return_value.data = mock_rfq_row
            mock_sb.table.return_value.insert.return_value.execute.return_value.data = [{"id": "rfq_supp_1"}]

            response = client.post("/rfq/create", json=payload)
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"
            assert data["rfq_id"] == rfq_id

            # Verify the RPC received the canonical nested transformation
            mock_sb.rpc.assert_called_once()
            rpc_call_args = mock_sb.rpc.call_args[0]
            assert rpc_call_args[0] == "create_rfq_with_constraints_rpc"
            rpc_params = rpc_call_args[1]

            assert rpc_params["p_product_name"] == "PVC Pipe"
            assert rpc_params["p_required_delivery_days"] == 2
            
            constraints = rpc_params["p_constraints"]
            constraints_by_dim = {c["dimension"]: c for c in constraints}

            # 1. Quantity: authorized, min=20, max=30
            assert "quantity" in constraints_by_dim
            qty_c = constraints_by_dim["quantity"]
            assert qty_c["status"] == "authorized"
            assert qty_c["constraints"]["min"] == 20
            assert qty_c["constraints"]["max"] == 30
            assert qty_c["constraints"]["required"] == 20

            # 2. Delivery: authorized, required_days=2, max_days=5
            assert "delivery" in constraints_by_dim
            deliv_c = constraints_by_dim["delivery"]
            assert deliv_c["status"] == "authorized"
            assert deliv_c["constraints"]["required_days"] == 2
            assert deliv_c["constraints"]["max_days"] == 5

            # 3. Specification: fixed
            assert "specification" in constraints_by_dim
            assert constraints_by_dim["specification"]["status"] == "fixed"

            # 4. Price: authorized, preferred_target=45, max=48
            assert "price" in constraints_by_dim
            price_c = constraints_by_dim["price"]
            assert price_c["status"] == "authorized"
            assert price_c["constraints"]["preferred_target"] == 45.0
            assert price_c["constraints"]["max"] == 48.0

    # ============================================================
    # INTEGRATION TEST B — DEFAULT FIXED AUTHORITY
    # ============================================================
    def test_integration_b_default_fixed_authority(self, auth_client):
        client, client_id, user_id = auth_client
        rfq_id = str(uuid.uuid4())

        payload = {
            "product_name": "Copper Fitting",
            "category": "Plumbing",
            "specs": "1/2 inch",
            "quantity": 100,
            "acceptable_price_min": 10.0,
            "acceptable_price_max": 12.0,
            "deadline_hours": 24,
            "required_delivery_days": 3,
            # No flexibility supplied
        }

        mock_rfq_row = {
            "id": rfq_id,
            "client_id": client_id,
            "product_name": "Copper Fitting",
            "category": "Plumbing",
            "specs": "1/2 inch",
            "quantity": 100,
            "acceptable_price_min": 10.0,
            "acceptable_price_max": 12.0,
            "deadline_hours": 24,
            "required_delivery_days": 3,
            "status": "active",
        }

        with patch("db.supabase") as mock_sb, \
             patch("main.enqueue_message", new_callable=AsyncMock):
            
            mock_sb.rpc.return_value.execute.return_value.data = mock_rfq_row
            mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{"id": "s1", "name": "S1", "phone_number": "+971501111111"}]
            mock_sb.table.return_value.insert.return_value.execute.return_value.data = [{"id": "rfq_supp_1"}]

            response = client.post("/rfq/create", json=payload)
            assert response.status_code == 200

            rpc_params = mock_sb.rpc.call_args[0][1]
            constraints = {c["dimension"]: c for c in rpc_params["p_constraints"]}

            # Quantity fixed
            assert constraints["quantity"]["status"] == "fixed"
            assert constraints["quantity"]["constraints"]["required"] == 100

            # Delivery fixed with required_days=3
            assert constraints["delivery"]["status"] == "fixed"
            assert constraints["delivery"]["constraints"]["required_days"] == 3

            # Specification fixed
            assert constraints["specification"]["status"] == "fixed"

            # Price authorized
            assert constraints["price"]["status"] == "authorized"
            assert constraints["price"]["constraints"]["preferred_target"] == 10.0
            assert constraints["price"]["constraints"]["max"] == 12.0

    # ============================================================
    # INTEGRATION TEST C — STRUCTURED APPROVAL
    # ============================================================
    def test_integration_c_structured_approval_endpoint(self, auth_client):
        client, client_id, user_id = auth_client
        flag_id = str(uuid.uuid4())
        rfq_id = str(uuid.uuid4())
        supp_id = str(uuid.uuid4())

        mock_flag = {
            "id": flag_id,
            "client_id": client_id,
            "rfq_id": rfq_id,
            "supplier_id": supp_id,
            "status": "pending",
            "category": "negotiation_tradeoff_authorization",
            "metadata": {"dimension": "delivery", "supplier_proposed_value": 5},
            "suppliers": {"id": supp_id, "name": "Global Pipes", "phone_number": "+971500000002"},
        }
        mock_rfq = {
            "id": rfq_id,
            "client_id": client_id,
            "product_name": "PVC Pipe",
            "status": "active",
        }

        approval_payload = {
            "dimension": "delivery",
            "decision": "approve",
            "constraints": {"max_days": 5},
            "resume_negotiation": False,
        }

        with patch.object(db, "get_flag_by_id", return_value=mock_flag) as mock_get_flag, \
             patch.object(db, "get_rfq_by_id", return_value=mock_rfq), \
             patch.object(db, "set_rfq_negotiation_constraint", return_value={"id": "c1", "dimension": "delivery", "status": "authorized"}) as mock_set_c, \
             patch.object(db, "resolve_flag_with_response", return_value=[{"id": flag_id, "status": "resolved"}]) as mock_resolve:

            response = client.post(f"/flags/{flag_id}/negotiation-authorization", json=approval_payload)
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "resolved"
            assert data["decision"] == "approve"
            assert data["dimension"] == "delivery"

            # Verify constraint persisted first with operator_review and authorized_by
            mock_set_c.assert_called_once_with(
                client_id=client_id,
                rfq_id=rfq_id,
                dimension="delivery",
                status="authorized",
                constraints={"max_days": 5},
                source="operator_review",
                authorized_by=user_id,
            )

            # Verify resolve_flag_with_response called with tenant scoping and human_response
            mock_resolve.assert_called_once_with(
                flag_id=flag_id,
                human_response="Operator approved delivery trade-off",
                client_id=client_id,
            )

            # Verify tenant isolation: Flag from another tenant returns 404
            mock_get_flag.return_value = None
            diff_flag_resp = client.post(f"/flags/{str(uuid.uuid4())}/negotiation-authorization", json=approval_payload)
            assert diff_flag_resp.status_code == 404

    # ============================================================
    # INTEGRATION TEST D — APPROVAL FAILURE HANDLING
    # ============================================================
    def test_integration_d_approval_persistence_failure_leaves_flag_pending(self, auth_client):
        client, client_id, user_id = auth_client
        flag_id = str(uuid.uuid4())
        rfq_id = str(uuid.uuid4())
        supp_id = str(uuid.uuid4())

        mock_flag = {
            "id": flag_id,
            "client_id": client_id,
            "rfq_id": rfq_id,
            "supplier_id": supp_id,
            "status": "pending",
        }
        mock_rfq = {"id": rfq_id, "client_id": client_id, "status": "active"}

        approval_payload = {
            "dimension": "delivery",
            "decision": "approve",
            "constraints": {"max_days": 5},
            "resume_negotiation": True,
        }

        # Force constraint persistence failure (returns None)
        with patch.object(db, "get_flag_by_id", return_value=mock_flag), \
             patch.object(db, "get_rfq_by_id", return_value=mock_rfq), \
             patch.object(db, "set_rfq_negotiation_constraint", return_value=None), \
             patch.object(db, "resolve_flag_with_response") as mock_resolve, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enqueue:

            response = client.post(f"/flags/{flag_id}/negotiation-authorization", json=approval_payload)
            assert response.status_code == 500

            # Verify flag was NOT resolved
            mock_resolve.assert_not_called()
            # Verify no outbound supplier message was sent
            mock_enqueue.assert_not_called()

    # ============================================================
    # INTEGRATION TEST E — DELIVERY UNKNOWN (NULL BASELINE)
    # ============================================================
    def test_integration_e_delivery_unknown_does_not_assume_2_days(self):
        # RFQ with NO required_delivery_days specified
        rfq = {
            "id": "rfq-test",
            "product_name": "Steel Rods",
            "acceptable_price_min": 100.0,
            "acceptable_price_max": 120.0,
            "required_delivery_days": None,
        }
        # Delivery constraint is fixed without baseline
        constraints = [
            {"dimension": "price", "status": "authorized", "constraints": {"preferred_target": 100.0, "max": 120.0}},
            {"dimension": "delivery", "status": "fixed", "constraints": {"required_days": None}},
            {"dimension": "quantity", "status": "fixed", "constraints": {"required": 50}},
        ]

        supplier_msg = "If you accept 5 days delivery I can reduce the price to 110."
        tradeoff = negotiation_engine.evaluate_supplier_tradeoff(
            message_text=supplier_msg,
            rfq_constraints=constraints,
            rfq=rfq,
        )

        assert tradeoff["has_tradeoff"] is True
        assert tradeoff["is_authorized"] is False
        assert tradeoff["dimension"] == "delivery"
        assert tradeoff["current_value"] is None  # UNKNOWN baseline, NOT 2
        assert tradeoff["supplier_proposed_value"] == 5
        assert "not specified" in tradeoff["reason"].lower()
        assert "2 days" not in tradeoff["reason"]

    # ============================================================
    # INTEGRATION TEST F — DELIVERY KNOWN
    # ============================================================
    def test_integration_f_delivery_known_authorized_vs_unauthorized(self):
        rfq = {
            "id": "rfq-known",
            "product_name": "Valves",
            "required_delivery_days": 2,
        }
        # Delivery is authorized up to 5 days
        constraints = [
            {"dimension": "delivery", "status": "authorized", "constraints": {"required_days": 2, "max_days": 5}},
        ]

        # Case 1: 4 days proposed (within authorized max 5)
        msg_4 = "If you accept 4 days delivery I can give 95 AED"
        tradeoff_4 = negotiation_engine.evaluate_supplier_tradeoff(
            message_text=msg_4,
            rfq_constraints=constraints,
            rfq=rfq,
        )
        assert tradeoff_4["has_tradeoff"] is True
        assert tradeoff_4["is_authorized"] is True
        assert tradeoff_4["supplier_proposed_value"] == 4

        # Case 2: 7 days proposed (exceeds authorized max 5)
        msg_7 = "If you can accept 7 days delivery I can give 90 AED"
        tradeoff_7 = negotiation_engine.evaluate_supplier_tradeoff(
            message_text=msg_7,
            rfq_constraints=constraints,
            rfq=rfq,
        )
        assert tradeoff_7["has_tradeoff"] is True
        assert tradeoff_7["is_authorized"] is False
        assert tradeoff_7["supplier_proposed_value"] == 7
        assert "exceeding authorized maximum (5 days)" in tradeoff_7["reason"]

    # ============================================================
    # INTEGRATION TEST G — ATOMIC CREATION FAILURE
    # ============================================================
    def test_integration_g_atomic_creation_failure_rolls_back(self, auth_client):
        client, client_id, user_id = auth_client

        payload = {
            "product_name": "Cement Bags",
            "category": "Building Materials",
            "specs": "50kg Portland",
            "quantity": 100,
            "deadline_hours": 24,
        }

        with patch("db.supabase") as mock_sb, \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enqueue:
            
            # Simulate DB transaction/RPC exception
            mock_sb.rpc.return_value.execute.side_effect = Exception("DB Transaction Aborted: constraint violation")

            with pytest.raises(Exception) as exc_info:
                db.create_rfq_and_match_suppliers(
                    client_id=client_id,
                    product_name="Cement Bags",
                    category="Building Materials",
                    specs="50kg Portland",
                    quantity=100,
                )
            assert "DB Transaction Aborted" in str(exc_info.value)
            
            # Ensure no supplier messages enqueued
            mock_enqueue.assert_not_called()

    # ============================================================
    # INTEGRATION TEST H — REAL WEBHOOK CHAIN
    # ============================================================
    @pytest.mark.asyncio
    async def test_integration_h_real_webhook_chain_unauthorized_to_approval(self, auth_client):
        client, client_id, user_id = auth_client
        rfq_id = str(uuid.uuid4())
        supp_id = str(uuid.uuid4())
        flag_id = str(uuid.uuid4())
        quote_id = str(uuid.uuid4())

        # 1. Context: Active RFQ with fixed delivery requirement (2 days)
        mock_rfq = {
            "id": rfq_id,
            "client_id": client_id,
            "product_name": "Gate Valve 2 inch",
            "specs": "Brass PN16",
            "quantity": 25,
            "acceptable_price_min": 50.0,
            "acceptable_price_max": 65.0,
            "required_delivery_days": 2,
            "status": "active",
        }
        mock_constraints = [
            {"dimension": "price", "status": "authorized", "constraints": {"preferred_target": 50.0, "max": 65.0}},
            {"dimension": "delivery", "status": "fixed", "constraints": {"required_days": 2}},
            {"dimension": "quantity", "status": "fixed", "constraints": {"required": 25}},
            {"dimension": "specification", "status": "fixed", "constraints": {"allowed_alternatives": None}},
        ]
        mock_supplier = {
            "id": supp_id,
            "name": "Al Valve Supplies",
            "phone_number": "+971509999999",
            "client_id": client_id,
            "is_active": True,
        }

        # 2. Supplier sends quote proposing unauthorized 5-day delivery
        inbound_text = "Rate is 55 AED each if you can accept 5 days delivery"
        open_rfq_entry = {
            "id": "rfq_supp_1",
            "rfq_id": rfq_id,
            "supplier_id": supp_id,
            "status": "sent",
            "rfqs": mock_rfq,
        }
        
        reasoning_decision = {
            "tool_name": "record_quote",
            "arguments": {
                "rfq_id": rfq_id,
                "price": 55.0,
                "delivery_time": "5 days",
                "quantity": 25,
            },
            "reasoning": "Supplier offered quote with delivery trade-off",
        }

        msg_key_id = f"wamid-{uuid.uuid4()}"
        with patch.object(db, "get_client_by_instance", return_value={"id": client_id, "name": "Test Client"}), \
             patch.object(db, "get_supplier_by_phone", return_value=mock_supplier), \
             patch.object(db, "claim_webhook_message", return_value={"id": "msg_proc_1"}), \
             patch.object(db, "complete_webhook_message", return_value=True), \
             patch.object(db, "get_open_rfqs_for_supplier", return_value=[open_rfq_entry]), \
             patch.object(db, "get_rfq_by_id", return_value=mock_rfq), \
             patch.object(db, "get_rfq_negotiation_constraints", return_value=mock_constraints), \
             patch.object(db, "record_quote", return_value={"id": quote_id, "price": 55.0, "rfq_id": rfq_id}) as mock_save_quote, \
             patch.object(db, "create_or_update_negotiation_session", return_value={"id": "sess-1", "attempt_count": 0}) as mock_update_session, \
             patch.object(db, "flag_for_human_review", return_value=[{"id": flag_id}]) as mock_flag, \
             patch.object(db, "log_message", return_value=str(uuid.uuid4())), \
             patch.object(db, "record_agent_decision", return_value={"id": str(uuid.uuid4())}), \
             patch.object(db, "update_agent_decision", return_value=True), \
             patch.object(groq_client, "reason_about_procurement_message", return_value=reasoning_decision), \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enqueue:

            webhook_payload = {
                "event": "messages.upsert",
                "data": {
                    "key": {
                        "remoteJid": "+971509999999@s.whatsapp.net",
                        "fromMe": False,
                        "id": msg_key_id,
                    },
                    "message": {
                        "conversation": inbound_text,
                    },
                },
            }
            # Run inbound message processor through HTTP webhook endpoint
            resp = client.post("/webhook/whatsapp", json=webhook_payload)
            assert resp.status_code == 200

            # 3. Verify quote is stored first
            mock_save_quote.assert_called_once()

            # 4. Verify session is paused (awaiting_authorization)
            mock_update_session.assert_called_once()
            session_args = mock_update_session.call_args[1]
            assert session_args.get("status") == "awaiting_authorization"

            # 5. Verify human review flag is created
            mock_flag.assert_called_once()
            flag_kwargs = mock_flag.call_args[1]
            assert flag_kwargs["category"] == "negotiation_tradeoff_authorization"
            assert flag_kwargs["metadata"]["dimension"] == "delivery"
            assert flag_kwargs["metadata"]["supplier_proposed_value"] == 5

            # 6. Verify holding message is sent instead of autonomous counteroffer
            mock_enqueue.assert_called_once()
            sent_msg = mock_enqueue.call_args[0][1]
            assert "confirm internally" in sent_msg.lower()
            assert "can work" in sent_msg.lower()

        # 7. Operator approves the 5-day delivery via Structured Authorization Endpoint
        mock_flag_obj = {
            "id": flag_id,
            "client_id": client_id,
            "rfq_id": rfq_id,
            "supplier_id": supp_id,
            "status": "pending",
            "suppliers": mock_supplier,
        }

        with patch.object(db, "get_flag_by_id", return_value=mock_flag_obj), \
             patch.object(db, "get_rfq_by_id", return_value=mock_rfq), \
             patch.object(db, "set_rfq_negotiation_constraint", return_value={"id": "c-del", "dimension": "delivery", "status": "authorized"}) as mock_set_c, \
             patch.object(db, "resolve_flag_with_response", return_value=[{"id": flag_id, "status": "resolved"}]) as mock_resolve, \
             patch.object(db, "get_active_negotiation_session", return_value={"id": "sess-1", "attempt_count": 1}), \
             patch.object(db, "log_message", return_value="msg-log-resume"), \
             patch("main.enqueue_message", new_callable=AsyncMock) as mock_enqueue_resume:

            approval_resp = client.post(
                f"/flags/{flag_id}/negotiation-authorization",
                json={
                    "dimension": "delivery",
                    "decision": "approve",
                    "constraints": {"max_days": 5},
                    "resume_negotiation": True,
                }
            )

            assert approval_resp.status_code == 200
            appr_data = approval_resp.json()
            assert appr_data["status"] == "resolved"
            assert appr_data["outbound_sent"] is True

            # Verify constraint persisted
            mock_set_c.assert_called_once_with(
                client_id=client_id,
                rfq_id=rfq_id,
                dimension="delivery",
                status="authorized",
                constraints={"max_days": 5},
                source="operator_review",
                authorized_by=user_id,
            )

            # Verify flag resolved
            mock_resolve.assert_called_once_with(
                flag_id=flag_id,
                human_response="Operator approved delivery trade-off",
                client_id=client_id,
            )

            # Verify resumed negotiation message sent with new authority
            mock_enqueue_resume.assert_called_once()
            resumed_text = mock_enqueue_resume.call_args[0][1]
            assert "5 days" in resumed_text
            assert "best rate" in resumed_text.lower()
