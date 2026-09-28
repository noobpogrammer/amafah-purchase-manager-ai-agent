"""
tests/e2e/conftest.py
Fixtures for E2E acceptance tests, ensuring isolated client/supplier setups,
instant zero-delay outbound dispatch, and strict TestWhatsAppTransport safety enforcement.
"""

import os
import uuid
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

import db
import main
import groq_client
from tests.e2e.transport import TestWhatsAppTransport, assert_test_transport_active
from tests.e2e.supplier_simulator import deterministic_reasoner_extract


@pytest.fixture
def e2e_harness():
    """
    Sets up the complete E2E testing harness:
    1. Installs TestWhatsAppTransport (fails safe if real transport is active)
    2. Configures zero outbound delay for instant message handling
    3. Provisions test tenant client and test supplier with unique test UUIDs
    4. Automatically patches groq reasoner with deterministic reasoner (unless RUN_LIVE_LLM_E2E=1)
    5. Cleans up all test database entities upon completion
    """
    # 1. Transport safety injection
    test_transport = TestWhatsAppTransport()
    main.set_whatsapp_transport(test_transport)
    assert_test_transport_active()

    # Zero delay for instant test execution
    orig_min_delay = main.OUTBOUND_MIN_DELAY
    orig_max_delay = main.OUTBOUND_MAX_DELAY
    main.OUTBOUND_MIN_DELAY = 0.0
    main.OUTBOUND_MAX_DELAY = 0.0

    # 2. Database isolation: create test client & test supplier
    test_run_id = str(uuid.uuid4())[:8]
    test_client_id = str(uuid.uuid4())
    test_user_id = str(uuid.uuid4())
    test_supplier_id = str(uuid.uuid4())
    test_phone = f"+97159{uuid.uuid4().int % 10000000:07d}"
    test_instance = f"test_inst_{test_run_id}"

    # Insert test client
    try:
        db.supabase.table("clients").insert({
            "id": test_client_id,
            "name": f"E2E Test Tenant {test_run_id}",
            "whatsapp_instance": test_instance,
        }).execute()
    except Exception as e:
        print(f"[E2E Setup] Note: client insert: {e}")

    # Insert test supplier
    try:
        db.supabase.table("suppliers").insert({
            "id": test_supplier_id,
            "client_id": test_client_id,
            "name": f"E2E Pipe Supplier {test_run_id}",
            "phone_number": test_phone,
            "category": ["Plumbing"],
            "is_active": True,
        }).execute()
    except Exception as e:
        print(f"[E2E Setup] Note: supplier insert: {e}")

    # Test client app with auth override
    app_client = TestClient(main.app)
    main.app.dependency_overrides[main.get_current_user] = lambda: {
        "id": test_user_id,
        "client_id": test_client_id,
        "email": f"tester_{test_run_id}@amafah.test",
        "role": "admin",
    }

    # Deterministic reasoning patch (active by default, bypassed when RUN_LIVE_LLM_E2E=1)
    is_live_llm = os.getenv("RUN_LIVE_LLM_E2E") == "1"
    reasoner_patch = None
    if not is_live_llm:
        reasoner_patch = patch.object(
            groq_client,
            "reason_about_procurement_message",
            side_effect=deterministic_reasoner_extract,
        )
        reasoner_patch.start()

    supplier_info = {
        "id": test_supplier_id,
        "name": f"E2E Pipe Supplier {test_run_id}",
        "phone_number": test_phone,
        "category": "Plumbing",
    }

    yield {
        "app_client": app_client,
        "client_id": test_client_id,
        "user_id": test_user_id,
        "supplier": supplier_info,
        "instance_name": test_instance,
        "transport": test_transport,
        "is_live_llm": is_live_llm,
    }

    # Teardown
    if reasoner_patch:
        reasoner_patch.stop()

    main.app.dependency_overrides.clear()
    main.OUTBOUND_MIN_DELAY = orig_min_delay
    main.OUTBOUND_MAX_DELAY = orig_max_delay

    # Clean up test database records strictly scoped to test client
    try:
        db.supabase.table("rfqs").delete().eq("client_id", test_client_id).execute()
        db.supabase.table("suppliers").delete().eq("client_id", test_client_id).execute()
        db.supabase.table("clients").delete().eq("id", test_client_id).execute()
    except Exception as e:
        print(f"[E2E Teardown] Note: cleanup: {e}")
