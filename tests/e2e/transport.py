"""
tests/e2e/transport.py
Safe in-memory test transport for WhatsApp message capturing and Evolution API response simulation.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import main


class TestWhatsAppTransport(main.WhatsAppTransport):
    """
    In-memory WhatsApp transport that captures all outbound procurement AI messages.
    Generates realistic Evolution API response structures and message IDs
    (e.g., TEST-WA-OUT-0001) for correlation and quoted replies.
    """

    def __init__(self):
        self.captured_messages: List[Dict[str, Any]] = []
        self._counter = 0

    def send(self, phone_number: str, message: str) -> dict:
        self._counter += 1
        msg_id = f"TEST-WA-OUT-{self._counter:04d}"
        now_ts = datetime.now(timezone.utc)
        record = {
            "timestamp": now_ts.isoformat(),
            "direction": "AI_TO_SUPPLIER",
            "phone": phone_number,
            "message": message,
            "generated_message_id": msg_id,
        }
        self.captured_messages.append(record)
        return {
            "status": "PENDING",
            "key": {
                "remoteJid": f"{phone_number}@s.whatsapp.net",
                "fromMe": True,
                "id": msg_id,
            },
            "message": {
                "conversation": message,
            },
            "messageTimestamp": int(now_ts.timestamp()),
        }

    def clear(self):
        self.captured_messages.clear()
        self._counter = 0

    def get_latest_message(self) -> Optional[Dict[str, Any]]:
        return self.captured_messages[-1] if self.captured_messages else None

    def get_messages_for_phone(self, phone: str) -> List[Dict[str, Any]]:
        return [m for m in self.captured_messages if m["phone"] == phone]


def assert_test_transport_active():
    """Asserts that the test suite is running under a safe TestWhatsAppTransport."""
    transport = main.get_whatsapp_transport()
    if isinstance(transport, main.ProductionWhatsAppTransport):
        raise RuntimeError("FATAL SAFETY CHECK: ProductionWhatsAppTransport is active during E2E test execution! Aborting.")
    if not isinstance(transport, TestWhatsAppTransport):
        raise RuntimeError(f"FATAL SAFETY CHECK: Unknown WhatsApp transport '{type(transport)}' active during E2E testing!")
