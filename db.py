"""
Supabase data access layer. Plain env-var config so credentials can be
plugged in whenever the Supabase project is ready.
"""

import os
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_supplier_by_phone(client_id: str, phone_number: str):
    res = (
        supabase.table("suppliers")
        .select("*")
        .eq("client_id", client_id)
        .eq("phone_number", phone_number)
        .execute()
    )
    return res.data[0] if res.data else None


def create_supplier(client_id: str, name: str, phone_number: str, categories: list[str] = None, notes: str = None):
    """Creates a new supplier. categories accepts a list of strings (e.g. ['Electronics', 'Hardware'])."""
    return supabase.table("suppliers").insert({
        "client_id": client_id,
        "name": name,
        "phone_number": phone_number,
        "category": categories or [],
        "notes": notes,
    }).execute().data


def update_supplier_categories(supplier_id: str, categories: list[str]):
    """Updates a supplier's categories list in Supabase."""
    return supabase.table("suppliers").update({
        "category": categories or []
    }).eq("id", supplier_id).execute().data


def format_supplier_categories(categories) -> str:
    """Formats a supplier's category array/list into a readable comma-separated string."""
    if not categories:
        return ""
    if isinstance(categories, list):
        return ", ".join(categories)
    return str(categories)


def get_open_rfqs_for_supplier(supplier_id: str):
    """All active RFQs currently sent to this supplier, awaiting a reply."""
    res = (
        supabase.table("rfq_suppliers")
        .select("*, rfqs(*)")
        .eq("supplier_id", supplier_id)
        .in_("status", ["sent", "clarifying"])
        .execute()
    )
    return res.data


def record_quote(rfq_id: str, supplier_id: str, price: float,
                  delivery_time: str = None, quality_notes: str = None,
                  raw_message: str = None, confidence: str = "high"):
    supabase.table("quotes").insert({
        "rfq_id": rfq_id,
        "supplier_id": supplier_id,
        "price": price,
        "delivery_time": delivery_time,
        "quality_notes": quality_notes,
        "raw_message": raw_message,
        "confidence": confidence,
    }).execute()

    supabase.table("rfq_suppliers").update({"status": "responded"}).eq(
        "rfq_id", rfq_id
    ).eq("supplier_id", supplier_id).execute()


def create_pending_clarification(client_id: str, supplier_id: str,
                                  candidate_rfq_ids: list, raw_message: str):
    supabase.table("pending_clarifications").insert({
        "client_id": client_id,
        "supplier_id": supplier_id,
        "pending_rfq_ids": candidate_rfq_ids,
        "raw_message": raw_message,
    }).execute()

    for rfq_id in candidate_rfq_ids:
        supabase.table("rfq_suppliers").update({"status": "clarifying"}).eq(
            "rfq_id", rfq_id
        ).eq("supplier_id", supplier_id).execute()


def get_pending_clarification_for_supplier(supplier_id: str):
    """Queries pending_clarifications for an unresolved ('awaiting_reply') row."""
    res = (
        supabase.table("pending_clarifications")
        .select("*")
        .eq("supplier_id", supplier_id)
        .eq("status", "awaiting_reply")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def resolve_pending_clarification(clarification_id: str):
    from datetime import datetime, timezone
    supabase.table("pending_clarifications").update({
        "status": "resolved",
        "resolved_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", clarification_id).execute()


def abandon_pending_clarification(clarification_id: str):
    supabase.table("pending_clarifications").update({
        "status": "abandoned"
    }).eq("id", clarification_id).execute()


def get_rfqs_by_ids(rfq_ids: list):
    if not rfq_ids:
        return []
    res = (
        supabase.table("rfqs")
        .select("*")
        .in_("id", rfq_ids)
        .execute()
    )
    return [{"rfqs": rfq} for rfq in res.data]


def log_message(client_id: str, supplier_id: str, direction: str,
                 body: str, related_rfq_id: str = None):
    supabase.table("message_log").insert({
        "client_id": client_id,
        "supplier_id": supplier_id,
        "direction": direction,
        "body": body,
        "related_rfq_id": related_rfq_id,
    }).execute()


def get_quotes_for_rfq(rfq_id: str):
    res = supabase.table("quotes").select("*, suppliers(name)").eq(
        "rfq_id", rfq_id
    ).execute()
    return res.data


def save_ranking(rfq_id: str, best_supplier_id: str, reasoning: str, ranking_json: dict):
    supabase.table("rfq_rankings").insert({
        "rfq_id": rfq_id,
        "best_supplier_id": best_supplier_id,
        "reasoning": reasoning,
        "ranking_json": ranking_json,
    }).execute()
