"""
Evolution API WhatsApp client for sending text messages to suppliers.
"""

import os
import requests


def send_whatsapp_message(phone_number: str, text: str) -> dict:
    """Sends a WhatsApp text message via Evolution API (/message/sendText/{instance})."""
    url = os.environ.get("EVOLUTION_API_URL", "").rstrip("/")
    api_key = os.environ.get("EVOLUTION_API_KEY", "")
    instance = os.environ.get("EVOLUTION_INSTANCE", "default")
    if not url:
        print("EVOLUTION_API_URL not configured, skipping WhatsApp send")
        return {"status": "skipped", "reason": "EVOLUTION_API_URL missing"}

    endpoint = f"{url}/message/sendText/{instance}"
    headers = {
        "apikey": api_key,
        "Content-Type": "application/json",
    }
    payload = {"number": phone_number, "text": text}
    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
        return response.json()
    except Exception as e:
        print(f"Error sending Evolution API message: {e}")
        return {"status": "error", "error": str(e)}
