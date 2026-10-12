"""API-level Phase Two regression tests: no real Supabase or WhatsApp I/O."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SUPABASE_URL", "https://localhost.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "phase2-fake-service-role-key")
os.environ.setdefault("GROQ_API_KEY", "phase2-fake-groq-key")
os.environ["OPENCLAW_TOOL_API_KEY"] = "phase2-test-tool-key"

from fastapi import FastAPI
from fastapi.testclient import TestClient
import openclaw_bridge as bridge


class MultiItemBridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(bridge.router)
        cls.client = TestClient(app)
        cls.headers = {"X-OpenClaw-Key": "phase2-test-tool-key"}

    def sample(self):
        return {
            "client_id": "11111111-1111-4111-8111-111111111111",
            "supplier_id": "22222222-2222-4222-8222-222222222222",
            "message": "Noora Brush AED 15, PVC Pipe AED 45",
            "source_message_id": "33333333-3333-4333-8333-333333333333",
        }

    def test_full_batch_is_recorded_exactly_once(self):
        parsed = {
            "status": "MAPPED",
            "bundle_id": "quoted-bundle",
            "items": [
                {"candidate_rfq_id": "a", "price": 15, "confidence": 0.95},
                {"candidate_rfq_id": "b", "price": 45, "confidence": 0.96},
            ],
        }
        with patch.object(bridge, "parse_multi_item_quotes", return_value=parsed) as parse, \
             patch.object(bridge, "record_multi_item_quotes", return_value={"inserted": 2, "duplicates": 0}) as persist:
            response = self.client.post("/openclaw/process-multi-item-message", headers=self.headers, json=self.sample())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "MULTI_RFQ_RECORDED")
        self.assertEqual(response.json()["recording"]["inserted"], 2)
        parse.assert_called_once()
        persist.assert_called_once()
        self.assertEqual(len(persist.call_args.args[0].items), 2)

    def test_ambiguous_item_saves_draft_without_recording(self):
        parsed = {
            "status": "CLARIFICATION_REQUIRED",
            "resolved_items": [{"candidate_rfq_id": "a", "price": 15}],
            "unresolved_items": [{"product_reference": "pipe", "price": 45, "reason": "unsure"}],
            "candidate_rfqs": [
                {"rfq_id": "a", "product_name": "Brush"},
                {"rfq_id": "b", "product_name": "Pipe"},
            ],
            "bundle_id": "quoted-bundle",
        }
        with patch.object(bridge, "parse_multi_item_quotes", return_value=parsed), \
             patch.object(bridge, "save_pending_commercial_draft", return_value={"draft": {"id": "draft-id"}}) as save, \
             patch.object(bridge, "record_multi_item_quotes") as persist:
            response = self.client.post("/openclaw/process-multi-item-message", headers=self.headers, json=self.sample())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "CLARIFICATION_REQUIRED")
        self.assertEqual(response.json()["draft_id"], "draft-id")
        save.assert_called_once()
        facts = save.call_args.args[0].extracted_facts
        self.assertEqual(facts["resolved_items"][0]["price"], 15)
        self.assertEqual(facts["unresolved_items"][0]["price"], 45)
        persist.assert_not_called()

    def test_requires_persisted_source_message_id(self):
        payload = self.sample()
        payload.pop("source_message_id")
        with patch.object(bridge, "parse_multi_item_quotes", return_value={
            "status": "MAPPED", "items": [{"candidate_rfq_id": "a", "price": 15}, {"candidate_rfq_id": "b", "price": 45}]
        }), patch.object(bridge, "record_multi_item_quotes") as persist:
            response = self.client.post("/openclaw/process-multi-item-message", headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 422)
        persist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
