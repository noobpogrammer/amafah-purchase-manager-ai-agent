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


def clean_phone(phone: str) -> str:
    """Strips all non-digit characters from a phone number string."""
    if not phone:
        return ""
    return "".join(c for c in phone if c.isdigit())


def get_supplier_by_phone(client_id: str, phone_number: str):
    target_digits = clean_phone(phone_number)
    if not target_digits:
        return None

    res = (
        supabase.table("suppliers")
        .select("*")
        .eq("client_id", client_id)
        .eq("phone_number", phone_number)
        .execute()
    )
    if res.data:
        return res.data[0]

    # Fallback: match by cleaned digits if stored with spaces, +, or dashes
    all_suppliers = (
        supabase.table("suppliers")
        .select("*")
        .eq("client_id", client_id)
        .execute()
        .data
    )
    for s in all_suppliers:
        if clean_phone(s.get("phone_number")) == target_digits:
            return s

    return None


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
    # Strictly filter only entries where the underlying RFQ is active
    return [entry for entry in res.data if entry.get("rfqs", {}).get("status") == "active"]


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
                                  candidate_rfq_ids: list, raw_message: str,
                                  extracted_price: float = None,
                                  extracted_delivery: str = None,
                                  extracted_notes: str = None,
                                  round_number: int = 1):
    supabase.table("pending_clarifications").insert({
        "client_id": client_id,
        "supplier_id": supplier_id,
        "pending_rfq_ids": candidate_rfq_ids,
        "raw_message": raw_message,
        "extracted_price": extracted_price,
        "extracted_delivery": extracted_delivery,
        "extracted_notes": extracted_notes,
        "round_number": round_number,
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


def count_clarification_rounds(supplier_id: str, client_id: str) -> int:
    """Counts how many pending_clarifications entries exist for this supplier."""
    res = (
        supabase.table("pending_clarifications")
        .select("id", count="exact")
        .eq("supplier_id", supplier_id)
        .eq("client_id", client_id)
        .execute()
    )
    return res.count or 0


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


def get_active_rfq_suppliers_with_deadlines():
    """Gets all rfq_suppliers with status 'sent' joined with rfq & supplier details."""
    res = (
        supabase.table("rfq_suppliers")
        .select("*, rfqs(*), suppliers(*)")
        .eq("status", "sent")
        .execute()
    )
    # Strictly filter only entries where the underlying RFQ is active
    return [item for item in res.data if item.get("rfqs", {}).get("status") == "active"]


def update_rfq_supplier_reminder(rfq_supplier_id: str, reminder_count: int):
    from datetime import datetime, timezone
    supabase.table("rfq_suppliers").update({
        "reminder_count": reminder_count,
        "last_reminder_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", rfq_supplier_id).execute()


def mark_rfq_supplier_no_response(rfq_supplier_id: str):
    supabase.table("rfq_suppliers").update({
        "status": "no_response"
    }).eq("id", rfq_supplier_id).execute()


def is_rfq_fully_processed(rfq_id: str) -> bool:
    """Returns True if no suppliers for this RFQ remain in 'sent' or 'clarifying' status."""
    res = (
        supabase.table("rfq_suppliers")
        .select("id", count="exact")
        .eq("rfq_id", rfq_id)
        .in_("status", ["sent", "clarifying"])
        .execute()
    )
    return (res.count or 0) == 0


def ranking_exists(rfq_id: str) -> bool:
    res = supabase.table("rfq_rankings").select("id").eq("rfq_id", rfq_id).execute()
    return len(res.data) > 0


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


def get_suppliers_by_category(client_id: str, category: str) -> list:
    """Finds active suppliers whose category array contains the specified category for a client."""
    res = (
        supabase.table("suppliers")
        .select("*")
        .eq("client_id", client_id)
        .eq("is_active", True)
        .contains("category", [category])
        .execute()
    )
    return res.data


def create_rfq_and_match_suppliers(client_id: str, product_name: str, category: str,
                                   deadline_hours: int = 24, specs: str = None,
                                   quantity: int = None):
    """Creates a new RFQ row, queries matching active suppliers by category, and creates rfq_suppliers join records."""
    rfq_res = supabase.table("rfqs").insert({
        "client_id": client_id,
        "product_name": product_name,
        "category": category,
        "specs": specs,
        "quantity": quantity,
        "deadline_hours": deadline_hours,
        "status": "active",
    }).execute()

    rfq = rfq_res.data[0]

    matching_suppliers = get_suppliers_by_category(client_id, category)

    # Deduplicate matched suppliers by id
    unique_suppliers = []
    seen_ids = set()
    for s in matching_suppliers:
        if s["id"] not in seen_ids:
            seen_ids.add(s["id"])
            unique_suppliers.append(s)

    if unique_suppliers:
        rfq_suppliers_payload = [
            {
                "rfq_id": rfq["id"],
                "supplier_id": s["id"],
                "status": "sent",
            }
            for s in unique_suppliers
        ]
        supabase.table("rfq_suppliers").insert(rfq_suppliers_payload).execute()

    return rfq, unique_suppliers


def get_supplier_prior_quotes(supplier_id: str, rfq_ids: list = None):
    """Fetches past quote(s) for a supplier to serve as prior context for Groq contradiction detection."""
    query = supabase.table("quotes").select("*, rfqs(product_name)").eq("supplier_id", supplier_id)
    if rfq_ids:
        query = query.in_("rfq_id", rfq_ids)
    res = query.order("created_at", desc=True).limit(5).execute()
    return res.data


def flag_for_human_review(client_id: str, supplier_id: str, rfq_id: str = None,
                         reason: str = "", category: str = "other", raw_message: str = ""):
    """Inserts a new human review escalation into flagged_for_review table."""
    payload = {
        "client_id": client_id,
        "supplier_id": supplier_id,
        "reason": reason,
        "category": category,
        "raw_message": raw_message,
        "status": "pending",
    }
    if rfq_id:
        payload["rfq_id"] = rfq_id

    return supabase.table("flagged_for_review").insert(payload).execute().data


def get_pending_flags(client_id: str):
    """Returns all pending human escalation items for a client, joined with supplier and rfq info."""
    res = (
        supabase.table("flagged_for_review")
        .select("*, suppliers(name, phone_number), rfqs(product_name)")
        .eq("client_id", client_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data


def resolve_flag(flag_id: str):
    """Marks a flagged_for_review item as resolved."""
    from datetime import datetime, timezone

    res = (
        supabase.table("flagged_for_review")
        .update({
            "status": "resolved",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", flag_id)
        .execute()
    )
    return res.data[0] if res.data else None


def close_rfq(rfq_id: str, target_status: str = "closed"):
    """
    Closes or cancels an RFQ and ensures all child records are resolved cleanly:
    1. Updates rfqs.status to target_status ('closed' or 'cancelled').
    2. Updates any rfq_suppliers for this RFQ in ('sent', 'clarifying') to 'no_response'.
    3. Abandons any pending_clarifications for this RFQ currently in 'awaiting_reply' status.
    """
    # 1. Update RFQ status
    rfq_res = (
        supabase.table("rfqs")
        .update({"status": target_status})
        .eq("id", rfq_id)
        .execute()
    )

    # 2. Update hanging suppliers in 'sent' or 'clarifying' to 'no_response'
    supabase.table("rfq_suppliers").update({"status": "no_response"}).eq(
        "rfq_id", rfq_id
    ).in_("status", ["sent", "clarifying"]).execute()

    # 3. Abandon any pending clarifications involving this rfq_id
    all_pending = (
        supabase.table("pending_clarifications")
        .select("*")
        .eq("status", "awaiting_reply")
        .execute()
        .data
    )
    for p in all_pending:
        p_rfq_ids = p.get("pending_rfq_ids") or []
        if rfq_id in p_rfq_ids:
            supabase.table("pending_clarifications").update(
                {"status": "abandoned"}
            ).eq("id", p["id"]).execute()

    return rfq_res.data[0] if rfq_res.data else None


def update_rfq_status(rfq_id: str, status: str):
    """Alias wrapping close_rfq for backward compatibility."""
    return close_rfq(rfq_id, status)


def log_webhook_error(error_message: str, traceback_str: str, raw_payload: dict = None):
    """Persists an unhandled webhook or Groq exception to the webhook_errors table."""
    try:
        supabase.table("webhook_errors").insert({
            "error_message": str(error_message),
            "traceback": traceback_str,
            "raw_payload": raw_payload,
        }).execute()
    except Exception as ex:
        print(f"Failed to log webhook error to db: {ex}")
