"""
Supabase data access layer. Plain env-var config so credentials can be
plugged in whenever the Supabase project is ready.
"""

import logging
import math
import os
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv()
from supabase import create_client, Client

logger = logging.getLogger(__name__)

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
    for s in (all_suppliers or []):
        s_digits = clean_phone(s.get("phone_number"))
        if s_digits == target_digits:
            return s
        if len(target_digits) >= 9 and len(s_digits) >= 9:
            if target_digits.endswith(s_digits.lstrip("0")) or s_digits.endswith(target_digits.lstrip("0")):
                return s

    return None


def get_supplier_by_phone_any_client(phone_number: str):
    """[DEPRECATED] Lookup a supplier by phone number across all clients.

    Prefer looking up the client via `get_client_by_instance` first, then using
    `get_supplier_by_phone(client_id, phone_number)` to prevent cross-tenant ambiguity.
    """
    target_digits = clean_phone(phone_number)
    if not target_digits:
        return None

    # Try exact match first
    res = (
        supabase.table("suppliers")
        .select("*")
        .eq("phone_number", phone_number)
        .execute()
    )
    if res.data:
        return res.data[0]

    # Fallback: scan all suppliers and match by cleaned digits
    all_suppliers = supabase.table("suppliers").select("*").execute().data
    for s in (all_suppliers or []):
        s_digits = clean_phone(s.get("phone_number"))
        if s_digits == target_digits:
            return s
        if len(target_digits) >= 9 and len(s_digits) >= 9:
            if target_digits.endswith(s_digits.lstrip("0")) or s_digits.endswith(target_digits.lstrip("0")):
                return s

    return None


def get_client_by_instance(instance_name: str):
    """Lookup a client row by whatsapp_instance name.

    Performs exact match first, and case-insensitive trimmed match as fallback.
    """
    if not instance_name:
        return None

    clean_instance = instance_name.strip()
    res = (
        supabase.table("clients")
        .select("*")
        .eq("whatsapp_instance", clean_instance)
        .execute()
    )
    if res.data:
        return res.data[0]

    # Fallback: case-insensitive scan across active clients
    all_clients = supabase.table("clients").select("*").execute().data
    for c in (all_clients or []):
        if (c.get("whatsapp_instance") or "").strip().lower() == clean_instance.lower():
            return c

    return None




def get_profile_by_id(user_id: str):
    """Lookup a profile row by the auth user id (profiles.id).

    Returns None when not found.
    """
    if not user_id:
        return None
    res = supabase.table("profiles").select("*").eq("id", user_id).maybe_single().execute()
    return res.data


def create_invite_token(client_id: str, role: str = "member", created_by: str = None, expires_in_days: int = 7) -> dict:
    """Generates an opaque URL-safe invite token and inserts it into invite_tokens."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
    row = {
        "token": token,
        "client_id": client_id,
        "role": role if role in ("admin", "member") else "member",
        "created_by": created_by,
        "expires_at": expires_at,
    }
    res = supabase.table("invite_tokens").insert(row).execute()
    return res.data[0] if res.data else row


def get_invite_token(token: str) -> dict | None:
    """Retrieves an invite token row from invite_tokens."""
    if not token:
        return None
    res = supabase.table("invite_tokens").select("*").eq("token", token).maybe_single().execute()
    return res.data


def mark_invite_token_used(token: str) -> dict | None:
    """Marks an invite token as used at the current timestamp."""
    if not token:
        return None
    now_iso = datetime.now(timezone.utc).isoformat()
    res = supabase.table("invite_tokens").update({"used_at": now_iso}).eq("token", token).execute()
    return res.data[0] if res.data else None


def list_invite_tokens(client_id: str) -> list[dict]:
    """Lists pending unused invite tokens for a client."""
    if not client_id:
        return []
    res = (
        supabase.table("invite_tokens")
        .select("*")
        .eq("client_id", client_id)
        .is_("used_at", "null")
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []



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


def is_rfq_open(rfq: dict) -> bool:
    """
    Returns True if an RFQ is strictly in 'active' status and within its deadline window (now < due_by).
    """
    if not rfq or not isinstance(rfq, dict):
        return False
    if rfq.get("status") != "active":
        return False
    due_by_str = rfq.get("due_by")
    if due_by_str:
        try:
            due_dt = datetime.fromisoformat(due_by_str.replace("Z", "+00:00"))
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < due_dt
        except Exception:
            pass
    created_at_str = rfq.get("created_at")
    deadline_hours = rfq.get("deadline_hours")
    if created_at_str and deadline_hours:
        try:
            created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) < (created_dt + timedelta(hours=float(deadline_hours)))
        except Exception:
            pass
    return True


def get_open_rfqs_for_supplier(supplier_id: str):
    """All active RFQs sent to this supplier, awaiting an initial reply or revision."""
    res = (
        supabase.table("rfq_suppliers")
        .select("*, rfqs(*)")
        .eq("supplier_id", supplier_id)
        .in_("status", ["sent", "clarifying", "responded"])
        .execute()
    )
    # Strictly filter only entries where the underlying RFQ is active and open
    return [entry for entry in (res.data or []) if is_rfq_open(entry.get("rfqs"))]


def record_quotes_batch(rfq_id: str, supplier_id: str, variants: list[dict],
                        raw_message: str = None, source_message_id: str = None,
                        confidence: str = "high", quote_group_id: str = None) -> list[dict]:
    """
    Atomically records one or more quote variants for an open RFQ from a supplier message.
    1. Verifies RFQ exists and is open.
    2. Assigns a single shared quote_group_id across all variants in the batch.
    3. Persists all variant rows via the database-authoritative RPC (or atomic table insert).
    4. Updates rfq_suppliers.status = 'responded' if at least one available priced variant is recorded.
    5. Returns all inserted quote rows.
    """
    if not rfq_id or not supplier_id or not variants:
        return []

    rfq_res = supabase.table("rfqs").select("*").eq("id", rfq_id).execute()
    if not rfq_res.data or not is_rfq_open(rfq_res.data[0]):
        logger.warning(
            "record_quotes_batch rejected for rfq %s from supplier %s: RFQ is not active or deadline passed",
            rfq_id, supplier_id
        )
        return []

    group_id = quote_group_id or str(uuid.uuid4())

    # Try RPC first for transaction-authoritative persistence
    try:
        rpc_payload = {
            "p_rfq_id": rfq_id,
            "p_supplier_id": supplier_id,
            "p_quote_group_id": group_id,
            "p_raw_message": raw_message,
            "p_source_message_id": source_message_id,
            "p_confidence": confidence or "high",
            "p_variants": variants,
        }
        res = supabase.rpc("record_quote_variants", rpc_payload).execute()
        if res.data and len(res.data) > 0:
            return res.data
    except Exception as e:
        logger.warning(f"record_quote_variants RPC error: {e}")

    # Fallback to direct batch insert
    try:
        rows_to_insert = []
        has_available_priced_variant = False
        for v in variants:
            price = v.get("price")
            avail = v.get("is_available", True)
            if avail and price is not None and float(price) > 0:
                has_available_priced_variant = True
            rows_to_insert.append({
                "rfq_id": rfq_id,
                "supplier_id": supplier_id,
                "quote_group_id": group_id,
                "variant_label": v.get("variant_label"),
                "price": price,
                "delivery_time": v.get("delivery_time"),
                "quality_notes": v.get("quality_notes"),
                "raw_message": raw_message,
                "confidence": confidence or "high",
                "is_available": avail,
                "source_message_id": source_message_id,
            })

        insert_res = supabase.table("quotes").insert(rows_to_insert).execute()
        if insert_res.data and has_available_priced_variant:
            supabase.table("rfq_suppliers").update({"status": "responded"}).eq(
                "rfq_id", rfq_id
            ).eq("supplier_id", supplier_id).execute()
        return insert_res.data or []
    except Exception as e:
        logger.error(f"Fallback record_quotes_batch error: {e}")
        return []


def record_quote(rfq_id: str, supplier_id: str, price: float = None,
                 delivery_time: str = None, quality_notes: str = None,
                 raw_message: str = None, confidence: str = "high",
                 variant_label: str = None, is_available: bool = True,
                 source_message_id: str = None, quote_group_id: str = None):
    """
    Backward-compatible quote recording.
    If variant metadata or group/source IDs are provided, routes through record_quotes_batch.
    Otherwise executes single-row insert with exact legacy payload structure.
    """
    if variant_label is not None or not is_available or source_message_id is not None or quote_group_id is not None:
        variants = [{
            "variant_label": variant_label,
            "price": price,
            "delivery_time": delivery_time,
            "quality_notes": quality_notes,
            "is_available": is_available,
        }]
        res = record_quotes_batch(
            rfq_id=rfq_id,
            supplier_id=supplier_id,
            variants=variants,
            raw_message=raw_message,
            source_message_id=source_message_id,
            confidence=confidence,
            quote_group_id=quote_group_id,
        )
        return res[0] if res else None

    rfq_res = supabase.table("rfqs").select("*").eq("id", rfq_id).execute()
    if not rfq_res.data or not is_rfq_open(rfq_res.data[0]):
        logger.warning(
            "record_quote rejected for rfq %s from supplier %s: RFQ is not active or deadline passed",
            rfq_id, supplier_id
        )
        return None

    insert_res = supabase.table("quotes").insert({
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

    return insert_res.data[0] if insert_res.data else None


def create_pending_clarification(client_id: str, supplier_id: str,
                                  candidate_rfq_ids: list, raw_message: str,
                                  extracted_price: float = None,
                                  extracted_delivery: str = None,
                                  extracted_notes: str = None,
                                  round_number: int = 1,
                                  no_progress_count: int = 0,
                                  last_question: str = None):
    payload = {
        "client_id": client_id,
        "supplier_id": supplier_id,
        "pending_rfq_ids": candidate_rfq_ids,
        "raw_message": raw_message,
        "extracted_price": extracted_price,
        "extracted_delivery": extracted_delivery,
        "extracted_notes": extracted_notes,
        "round_number": round_number,
        "no_progress_count": no_progress_count,
    }
    if last_question:
        payload["last_question"] = last_question
    res = supabase.table("pending_clarifications").insert(payload).execute()

    for rfq_id in candidate_rfq_ids:
        supabase.table("rfq_suppliers").update({"status": "clarifying"}).eq(
            "rfq_id", rfq_id
        ).eq("supplier_id", supplier_id).execute()

    return res.data[0].get("id") if res and res.data else None


def advance_pending_clarification(
    previous_id: str,
    client_id: str,
    supplier_id: str,
    candidate_rfq_ids: list,
    raw_message: str,
    extracted_price: float = None,
    extracted_delivery: str = None,
    extracted_notes: str = None,
    round_number: int = 2,
    no_progress_count: int = 0,
    last_question: str = None,
) -> dict | None:
    """
    Database-authoritative atomic replacement of an active pending clarification.
    Calls PostgreSQL RPC advance_pending_clarification to guarantee atomic transition
    (abandon old row + insert new row + update rfq_suppliers) in a single transaction.
    Fails closed (returns None) on any RPC or database error.
    """
    if not previous_id or not client_id or not supplier_id or not candidate_rfq_ids:
        return None

    try:
        params = {
            "p_previous_id": previous_id,
            "p_client_id": client_id,
            "p_supplier_id": supplier_id,
            "p_candidate_rfq_ids": candidate_rfq_ids,
            "p_raw_message": raw_message,
            "p_extracted_price": extracted_price,
            "p_extracted_delivery": extracted_delivery,
            "p_extracted_notes": extracted_notes,
            "p_round_number": round_number,
            "p_no_progress_count": no_progress_count,
            "p_last_question": last_question,
        }
        res = supabase.rpc("advance_pending_clarification", params).execute()
        if res and res.data and isinstance(res.data, list) and len(res.data) > 0 and isinstance(res.data[0], dict):
            return res.data[0]
        return None
    except Exception as e:
        logger.error("Failed to advance pending clarification %s: %s", previous_id, e)
        return None


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
    return [{"rfqs": rfq} for rfq in (res.data or []) if is_rfq_open(rfq)]


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


def update_rfq_supplier_sent_message_id(rfq_id: str, supplier_id: str, sent_message_id: str):
    """Saves the Evolution API outgoing message ID to rfq_suppliers.sent_message_id."""
    return supabase.table("rfq_suppliers").update({
        "sent_message_id": sent_message_id
    }).eq("rfq_id", rfq_id).eq("supplier_id", supplier_id).execute()


def get_rfq_supplier_by_sent_message_id(supplier_id: str, sent_message_id: str):
    """
    Looks up an active rfq_suppliers record for a supplier that matches sent_message_id.
    Returns the entry joined with rfqs(*) if the underlying RFQ is active and supplier status in ('sent', 'clarifying', 'responded').
    """
    if not supplier_id or not sent_message_id:
        return None
    res = (
        supabase.table("rfq_suppliers")
        .select("*, rfqs(*)")
        .eq("supplier_id", supplier_id)
        .eq("sent_message_id", sent_message_id)
        .in_("status", ["sent", "clarifying", "responded"])
        .execute()
    )
    active_entries = [entry for entry in (res.data or []) if is_rfq_open(entry.get("rfqs"))]
    return active_entries[0] if active_entries else None


def get_rfq_supplier_by_quoted_text(supplier_id: str, quoted_text: str):
    """
    Fallback for legacy RFQs: matches quoted message text against active open RFQs for a supplier
    by checking product name and specs match inside the quoted text body.
    """
    if not supplier_id or not quoted_text:
        return None
    open_rfqs = get_open_rfqs_for_supplier(supplier_id)
    if not open_rfqs:
        return None

    q_lower = quoted_text.lower()
    best_match = None
    best_score = 0

    for entry in open_rfqs:
        rfq = entry.get("rfqs", {})
        product_name = (rfq.get("product_name") or "").strip().lower()
        specs = (rfq.get("specs") or "").strip().lower()
        score = 0
        if product_name and product_name in q_lower:
            score += 2
        if specs and specs in q_lower:
            score += 1
        if score > best_score:
            best_score = score
            best_match = entry

    return best_match if best_score > 0 else None



def revert_unresolved_candidates(supplier_id: str, resolved_rfq_id: str, candidate_rfq_ids: list):
    """
    When one candidate RFQ is resolved, resets any remaining candidate RFQs for this supplier
    that are currently in 'clarifying' status back to 'sent'.
    """
    if not candidate_rfq_ids:
        return
    remaining_rfq_ids = [rfq_id for rfq_id in candidate_rfq_ids if rfq_id != resolved_rfq_id]
    if not remaining_rfq_ids:
        return

    supabase.table("rfq_suppliers").update({"status": "sent"}).eq(
        "supplier_id", supplier_id
    ).in_("rfq_id", remaining_rfq_ids).eq("status", "clarifying").execute()



def get_active_rfqs_past_deadline():
    """
    Returns active RFQs whose deadline has passed (due_by <= now, or created_at + deadline_hours <= now).
    Joined with rfq_suppliers and suppliers so participating suppliers can be notified.
    """
    res = (
        supabase.table("rfqs")
        .select("*, rfq_suppliers(*, suppliers(*))")
        .eq("status", "active")
        .execute()
    )
    now_dt = datetime.now(timezone.utc)
    expired_rfqs = []
    for rfq in (res.data or []):
        due_by_str = rfq.get("due_by")
        if due_by_str:
            try:
                due_dt = datetime.fromisoformat(due_by_str.replace("Z", "+00:00"))
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
                if now_dt >= due_dt:
                    expired_rfqs.append(rfq)
                continue
            except Exception:
                pass

        # Fallback to created_at + deadline_hours if due_by was missing
        created_at_str = rfq.get("created_at")
        deadline_hours = rfq.get("deadline_hours") or 24
        if created_at_str:
            try:
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                if now_dt >= (created_dt + timedelta(hours=deadline_hours)):
                    expired_rfqs.append(rfq)
            except Exception:
                pass
    return expired_rfqs


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


def get_message_by_event_key(event_key: str) -> dict | None:
    """Retrieves a message_log row matching event_key for idempotency checking."""
    if not event_key:
        return None
    try:
        res = supabase.table("message_log").select("*").eq("event_key", event_key).limit(1).execute()
        if res and res.data and isinstance(res.data, list) and len(res.data) > 0 and isinstance(res.data[0], dict):
            return res.data[0]
        return None
    except Exception as e:
        logger.debug("get_message_by_event_key error for %s: %s", event_key, e)
        return None


def get_supplier_conversation_history(client_id: str, supplier_id: str, limit: int = 10, exclude_message_id: str = None) -> list[dict]:
    """
    Fetches bounded chronological conversation history (oldest -> newest) for a supplier from message_log.
    """
    if not client_id or not supplier_id:
        return []
    try:
        query = (
            supabase.table("message_log")
            .select("id, direction, body, related_rfq_id, created_at, status")
            .eq("client_id", client_id)
            .eq("supplier_id", supplier_id)
        )
        if exclude_message_id:
            query = query.neq("id", exclude_message_id)
        res = query.order("created_at", desc=True).limit(limit).execute()
        return list(reversed(res.data or []))
    except Exception as ex:
        logger.warning("get_supplier_conversation_history error for supplier %s: %s", supplier_id, ex)
        return []


def get_rfq_suppliers_for_rfq(rfq_id: str) -> list[dict]:
    """Retrieves all rfq_suppliers records for an RFQ joined with suppliers."""
    if not rfq_id:
        return []
    try:
        res = (
            supabase.table("rfq_suppliers")
            .select("*, suppliers(*)")
            .eq("rfq_id", rfq_id)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error("get_rfq_suppliers_for_rfq error for rfq %s: %s", rfq_id, e)
        return []


def log_message(client_id: str, supplier_id: str, direction: str,
                 body: str, related_rfq_id: str = None, status: str = None,
                 event_key: str = None) -> str:
    """
    Logs an inbound or outbound message in message_log and returns the inserted row ID.
    If event_key is provided and already exists, returns the existing row ID (idempotency).
    If event_key is provided and insert fails (e.g. race conflict), looks up the existing row ID
    and strictly fails closed (returns None) without performing any unkeyed fallback insert.
    """
    if event_key:
        existing = get_message_by_event_key(event_key)
        if existing and existing.get("id"):
            return existing.get("id")

    payload = {
        "client_id": client_id,
        "supplier_id": supplier_id,
        "direction": direction,
        "body": body,
        "related_rfq_id": related_rfq_id,
    }
    if status is not None:
        payload["status"] = status
    elif direction == "outbound":
        payload["status"] = "queued"

    if event_key is not None:
        payload["event_key"] = event_key

    # Case 1: Keyed business event (event_key is provided)
    if event_key is not None:
        try:
            res = supabase.table("message_log").insert(payload).execute()
            if res.data and isinstance(res.data, list) and len(res.data) > 0:
                return res.data[0].get("id")
        except Exception as ex:
            logger.debug("log_message insert with event_key %s failed: %s", event_key, ex)
            # Check if another process won the insert race
            existing = get_message_by_event_key(event_key)
            if existing and existing.get("id"):
                return existing.get("id")
            # Fail closed: NEVER perform fallback insert without event_key!
            logger.error("log_message fail-closed for event_key %s: could not insert or confirm existing keyed row", event_key)
            return None
        return None

    # Case 2: Unkeyed ordinary message (event_key is None)
    try:
        res = supabase.table("message_log").insert(payload).execute()
        if res.data and isinstance(res.data, list) and len(res.data) > 0:
            return res.data[0].get("id")
    except Exception as ex:
        logger.debug("log_message insert for unkeyed message failed, trying base insert: %s", ex)
        try:
            res = supabase.table("message_log").insert({
                "client_id": client_id,
                "supplier_id": supplier_id,
                "direction": direction,
                "body": body,
                "related_rfq_id": related_rfq_id,
            }).execute()
            if res.data and isinstance(res.data, list) and len(res.data) > 0:
                return res.data[0].get("id")
        except Exception as e2:
            logger.error("log_message unkeyed insert fallback failed: %s", e2)
    return None


def get_quotes_for_rfq(rfq_id: str, include_unavailable: bool = False) -> list:
    """
    Returns the latest effective quote per supplier + variant_label for an RFQ, sorted newest first with id tie-breaker.
    By default, returns only currently available effective variants (is_available=True, price is not None).
    """
    res = (
        supabase.table("quotes")
        .select("*, suppliers(name)")
        .eq("rfq_id", rfq_id)
        .order("created_at", desc=True)
        .order("id", desc=True)
        .execute()
    )
    latest_quotes = []
    seen_variant_keys = set()
    for q in (res.data or []):
        supplier_id = q.get("supplier_id")
        if not supplier_id:
            continue
        v_label_norm = (q.get("variant_label") or "").strip().casefold()
        key = (supplier_id, v_label_norm)
        if key not in seen_variant_keys:
            seen_variant_keys.add(key)
            if include_unavailable:
                latest_quotes.append(q)
            else:
                # Include only if active / available and priced
                if q.get("is_available", True) is True and q.get("price") is not None:
                    latest_quotes.append(q)
    return latest_quotes


def get_all_quotes_for_rfq(rfq_id: str) -> list:
    """Returns all historical quotes for an RFQ in chronological order (created_at asc)."""
    res = (
        supabase.table("quotes")
        .select("*, suppliers(name)")
        .eq("rfq_id", rfq_id)
        .order("created_at", desc=False)
        .execute()
    )
    return res.data or []


def save_ranking(rfq_id: str, best_supplier_id: str | None, reasoning: str, ranking_json: dict, best_quote_id: str | None = None):
    """Persists AI comparison ranking using upsert on rfq_id to guarantee idempotency."""
    payload = {
        "rfq_id": rfq_id,
        "best_supplier_id": best_supplier_id,
        "reasoning": reasoning,
        "ranking_json": ranking_json,
    }
    if best_quote_id:
        payload["best_quote_id"] = best_quote_id
    return supabase.table("rfq_rankings").upsert(payload, on_conflict="rfq_id").execute().data


def get_ranking_for_rfq(rfq_id: str) -> dict | None:
    res = supabase.table("rfq_rankings").select("*").eq("rfq_id", rfq_id).order("created_at", desc=True).limit(1).execute()
    return res.data[0] if res.data else None


def get_rfqs_by_date(client_id: str, start_dt: str, end_dt: str) -> list:
    """Returns all RFQs for client_id created between start_dt and end_dt."""
    res = (
        supabase.table("rfqs")
        .select("*")
        .eq("client_id", client_id)
        .gte("created_at", start_dt)
        .lt("created_at", end_dt)
        .order("created_at", desc=False)
        .execute()
    )
    return res.data or []


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


def classify_price_position(
    quote_price: float,
    acceptable_min: float = None,
    acceptable_max: float = None,
) -> str:
    """
    Deterministically classifies a supplier quote against the RFQ's negotiation price range.
    Returns:
      - 'NO_RANGE_SET' if neither boundary is provided
      - 'AT_OR_BELOW_MIN' if quote_price <= acceptable_min
      - 'AT_MAX' if quote_price == acceptable_max
      - 'WITHIN_ACCEPTABLE_RANGE' if acceptable_min < quote_price < acceptable_max
      - 'ABOVE_ACCEPTABLE_RANGE' if max < quote_price <= max * 1.15
      - 'SIGNIFICANTLY_ABOVE_RANGE' if quote_price > max * 1.15
    """
    if quote_price is None:
        return "UNKNOWN"
    try:
        quote_p = float(quote_price)
        if math.isnan(quote_p) or math.isinf(quote_p):
            return "UNKNOWN"
    except (TypeError, ValueError):
        return "UNKNOWN"

    if acceptable_min is None and acceptable_max is None:
        return "NO_RANGE_SET"

    min_p = float(acceptable_min) if acceptable_min is not None else None
    max_p = float(acceptable_max) if acceptable_max is not None else None

    if min_p is not None and quote_p <= min_p:
        return "AT_OR_BELOW_MIN"
    if max_p is not None and quote_p == max_p:
        return "AT_MAX"
    if min_p is not None and max_p is not None and min_p < quote_p < max_p:
        return "WITHIN_ACCEPTABLE_RANGE"
    if max_p is not None:
        if quote_p <= max_p * 1.15:
            return "ABOVE_ACCEPTABLE_RANGE"
        else:
            return "SIGNIFICANTLY_ABOVE_RANGE"
    if min_p is not None:
        if quote_p <= min_p * 1.15:
            return "ABOVE_ACCEPTABLE_RANGE"
        else:
            return "SIGNIFICANTLY_ABOVE_RANGE"

    return "WITHIN_ACCEPTABLE_RANGE"


def get_competitive_pricing_context(rfq_id: str, current_supplier_id: str) -> dict:
    """
    Returns competitive pricing context from other suppliers for the SAME RFQ.
    Strictly excludes the current supplier's own quotes and masks competitor identities.
    """
    if not rfq_id:
        return {"competing_quotes_count": 0, "best_competing_price": None, "has_competition": False}

    effective_quotes = get_quotes_for_rfq(rfq_id)
    competing_quotes = [
        q for q in (effective_quotes or [])
        if q.get("supplier_id") != current_supplier_id and q.get("price") is not None
    ]

    if not competing_quotes:
        return {
            "competing_quotes_count": 0,
            "best_competing_price": None,
            "has_competition": False,
        }

    prices = []
    for q in competing_quotes:
        try:
            p = float(q["price"])
            if not math.isnan(p) and not math.isinf(p) and p > 0:
                prices.append(p)
        except (ValueError, TypeError):
            pass

    best_price = min(prices) if prices else None
    return {
        "competing_quotes_count": len(prices),
        "best_competing_price": best_price,
        "has_competition": best_price is not None,
    }


def get_negotiation_attempts(rfq_id: str, supplier_id: str) -> int:
    """Returns the current number of autonomous negotiation attempts made to this supplier for this RFQ."""
    if not rfq_id or not supplier_id:
        return 0
    try:
        res = (
            supabase.table("rfq_suppliers")
            .select("negotiation_attempts")
            .eq("rfq_id", rfq_id)
            .eq("supplier_id", supplier_id)
            .execute()
        )
        if res.data and isinstance(res.data, list) and len(res.data) > 0 and isinstance(res.data[0], dict):
            val = res.data[0].get("negotiation_attempts")
            if val is not None and isinstance(val, (int, float, str)):
                return int(val)
    except Exception:
        return 0
    return 0


def increment_negotiation_attempts(rfq_id: str, supplier_id: str, max_attempts: int = 3) -> int:
    """
    Atomically increments the negotiation attempt counter on rfq_suppliers
    only if current attempts < max_attempts.
    Returns the new attempt count (e.g. 1, 2, 3) on success, or -1 if the limit was reached / update failed.
    """
    if not rfq_id or not supplier_id:
        return -1

    # 1. Try Supabase RPC for atomic PostgreSQL execution
    try:
        rpc_res = supabase.rpc("increment_negotiation_attempts", {
            "p_rfq_id": rfq_id,
            "p_supplier_id": supplier_id,
            "p_max_attempts": max_attempts,
        }).execute()
        if rpc_res.data is not None:
            val = int(rpc_res.data)
            if val > 0:
                return val
            elif val == -1:
                return -1
    except Exception as e:
        logger.debug("RPC increment_negotiation_attempts not available, using atomic CAS fallback: %s", e)

    # 2. Fallback using optimistic concurrency control (Compare-And-Swap)
    try:
        res = (
            supabase.table("rfq_suppliers")
            .select("negotiation_attempts")
            .eq("rfq_id", rfq_id)
            .eq("supplier_id", supplier_id)
            .execute()
        )
        if res.data and isinstance(res.data, list) and len(res.data) > 0:
            current = int(res.data[0].get("negotiation_attempts") or 0)
            if current >= max_attempts:
                return -1
            new_count = current + 1
            upd = (
                supabase.table("rfq_suppliers")
                .update({"negotiation_attempts": new_count})
                .eq("rfq_id", rfq_id)
                .eq("supplier_id", supplier_id)
                .eq("negotiation_attempts", current)
                .execute()
            )
            if upd.data:
                return new_count
            return -1
    except Exception as ex:
        logger.error("increment_negotiation_attempts error: %s", ex)
        return -1

    return -1


def create_rfq_and_match_suppliers(client_id: str, product_name: str, category: str,
                                   deadline_hours: int = 24, specs: str = None,
                                   quantity: int = None,
                                   acceptable_price_min: float = None,
                                   acceptable_price_max: float = None):
    """Creates a new RFQ row, queries matching active suppliers by category, and creates rfq_suppliers join records."""
    now_utc = datetime.now(timezone.utc)
    due_by = (now_utc + timedelta(hours=deadline_hours)).isoformat()

    insert_data = {
        "client_id": client_id,
        "product_name": product_name,
        "category": category,
        "specs": specs,
        "quantity": quantity,
        "deadline_hours": deadline_hours,
        "due_by": due_by,
        "status": "active",
        "finalization_status": "pending",
        "acceptable_price_min": acceptable_price_min,
        "acceptable_price_max": acceptable_price_max,
    }

    rfq_res = supabase.table("rfqs").insert(insert_data).execute()

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


def get_incomplete_rfqs_audit(client_id: str = None):
    """Returns RFQs with missing category/deadline metadata or no matched suppliers."""
    query = supabase.table("rfqs").select("*, rfq_suppliers(id)")
    if client_id:
        query = query.eq("client_id", client_id)

    res = query.execute()
    issues = []
    for rfq in res.data:
        matched_count = len(rfq.get("rfq_suppliers") or [])
        if rfq.get("category") is None or rfq.get("deadline_hours") is None or matched_count == 0:
            issues.append({
                "id": rfq.get("id"),
                "product_name": rfq.get("product_name"),
                "category": rfq.get("category"),
                "deadline_hours": rfq.get("deadline_hours"),
                "matched_suppliers_count": matched_count,
                "status": rfq.get("status"),
                "created_at": rfq.get("created_at"),
            })
    return {
        "count": len(issues),
        "items": issues,
    }


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


def get_flag_by_id(flag_id: str, client_id: str = None) -> dict | None:
    """Returns a single flagged_for_review item with joined supplier and rfq details, optionally scoped by client_id."""
    query = supabase.table("flagged_for_review").select("*, suppliers(*), rfqs(*)").eq("id", flag_id)
    if client_id:
        query = query.eq("client_id", client_id)
    res = query.execute()
    return res.data[0] if res.data else None


def claim_flag_for_operator_action(flag_id: str, client_id: str) -> dict | None:
    """
    Atomically claims a pending flag for operator reasoning by transitioning status from 'pending' to 'processing'.
    Returns the claimed flag dict with joined supplier and RFQ details if successful, or None if already claimed/resolved.
    """
    try:
        res = supabase.rpc(
            "claim_flag_for_operator_action",
            {"p_flag_id": flag_id, "p_client_id": client_id}
        ).execute()
        if res.data and len(res.data) > 0:
            # Re-fetch joined details
            return get_flag_by_id(flag_id, client_id)
    except Exception as e:
        logger.warning(f"claim_flag_for_operator_action RPC error: {e}")

    # Fallback to direct atomic conditional update
    try:
        res = (
            supabase.table("flagged_for_review")
            .update({"status": "processing"})
            .eq("id", flag_id)
            .eq("client_id", client_id)
            .eq("status", "pending")
            .select("*, suppliers(*), rfqs(*)")
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Fallback claim_flag_for_operator_action error: {e}")
        return None


def release_flag_claim(flag_id: str, client_id: str) -> dict | None:
    """
    Releases an operator claim on failure, strictly reverting status from 'processing' back to 'pending'.
    Uses the database-authoritative RPC when available.
    """
    try:
        res = supabase.rpc(
            "release_flag_claim",
            {"p_flag_id": flag_id, "p_client_id": client_id}
        ).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        logger.warning(f"release_flag_claim RPC error: {e}")

    # Fallback to direct strictly conditional update (processing -> pending only)
    try:
        res = (
            supabase.table("flagged_for_review")
            .update({"status": "pending"})
            .eq("id", flag_id)
            .eq("client_id", client_id)
            .eq("status", "processing")
            .select("*")
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Error releasing flag claim for {flag_id}: {e}")
        return None


def complete_flag_operator_action(flag_id: str, client_id: str, human_response: str = None) -> dict | None:
    """
    Marks a claimed flag as resolved after successful operator action execution.
    Strictly transitions from 'processing' to 'resolved' only.
    Uses the database-authoritative RPC when available.
    """
    from datetime import datetime, timezone
    try:
        res = supabase.rpc(
            "complete_flag_operator_action",
            {
                "p_flag_id": flag_id,
                "p_client_id": client_id,
                "p_human_response": human_response or "",
            }
        ).execute()
        if res.data and len(res.data) > 0:
            return get_flag_by_id(flag_id, client_id) or res.data[0]
    except Exception as e:
        logger.warning(f"complete_flag_operator_action RPC error: {e}")

    # Fallback to direct strictly conditional update (processing -> resolved only)
    try:
        res = (
            supabase.table("flagged_for_review")
            .update({
                "status": "resolved",
                "human_response": human_response,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", flag_id)
            .eq("client_id", client_id)
            .eq("status", "processing")
            .select("*, suppliers(*), rfqs(*)")
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Error completing flag action for {flag_id}: {e}")
        return None


def resolve_flag(flag_id: str, client_id: str = None):
    """Marks a flagged_for_review item as resolved (administrative dismissal)."""
    from datetime import datetime, timezone

    query = (
        supabase.table("flagged_for_review")
        .update({
            "status": "resolved",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", flag_id)
    )
    if client_id:
        query = query.eq("client_id", client_id)
    res = query.execute()
    return res.data[0] if res.data else None


def resolve_flag_with_response(flag_id: str, human_response: str = None, client_id: str = None):
    """Stores human response and marks a flagged_for_review item as resolved (tenant-scoped)."""
    from datetime import datetime, timezone

    query = (
        supabase.table("flagged_for_review")
        .update({
            "status": "resolved",
            "human_response": human_response,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", flag_id)
    )
    if client_id:
        query = query.eq("client_id", client_id)
    res = query.select("*, suppliers(*), rfqs(*)").execute()
    return res.data if res.data else []



def close_rfq(rfq_id: str, target_status: str = "closed"):
    """
    Closes or cancels an RFQ and ensures all child records are resolved cleanly:
    1. Updates rfqs.status to target_status ('closed' or 'cancelled') and marks finalization_status='completed'.
    2. Updates any rfq_suppliers for this RFQ in ('sent', 'clarifying') to 'no_response'.
    3. Abandons any pending_clarifications for this RFQ currently in 'awaiting_reply' status.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    # 1. Update RFQ status
    rfq_res = (
        supabase.table("rfqs")
        .update({
            "status": target_status,
            "finalization_status": "completed",
            "finalized_at": now_iso,
        })
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


def claim_rfq_for_finalization(rfq_id: str) -> bool:
    """
    Atomically acquires finalization authority for an expired active RFQ via PostgreSQL RPC.
    Transitions status to 'closed' and finalization_status to 'processing', cascading child status cleanup.
    Returns True if authority was acquired, False otherwise. Fail-closed.
    """
    if not rfq_id:
        return False
    try:
        res = supabase.rpc("claim_rfq_for_finalization", {"p_rfq_id": str(rfq_id)}).execute()
        if res.data is not None:
            return bool(res.data)
        return False
    except Exception as e:
        logger.error("claim_rfq_for_finalization RPC error for rfq %s: %s", rfq_id, e)
        return False


def get_rfqs_pending_finalization_recovery() -> list[dict]:
    """
    Finds RFQs that are in status 'closed' and finalization_status 'processing',
    representing incomplete finalizations that must be resumed.
    """
    try:
        res = (
            supabase.table("rfqs")
            .select("*, rfq_suppliers(*, suppliers(*))")
            .eq("status", "closed")
            .eq("finalization_status", "processing")
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error("get_rfqs_pending_finalization_recovery error: %s", e)
        return []


def mark_rfq_finalization_completed(rfq_id: str) -> bool:
    """
    Marks an RFQ's finalization as completed with timestamp.
    Only updates if current status is 'closed' and finalization_status is 'processing'.
    """
    if not rfq_id:
        return False
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            supabase.table("rfqs")
            .update({
                "finalization_status": "completed",
                "finalized_at": now_iso,
            })
            .eq("id", rfq_id)
            .eq("status", "closed")
            .eq("finalization_status", "processing")
            .execute()
        )
        return bool(res.data)
    except Exception as e:
        logger.error("mark_rfq_finalization_completed error for rfq %s: %s", rfq_id, e)
        return False


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


def claim_webhook_message(client_id: str, message_id: str) -> bool:
    """
    Atomically attempts to claim an incoming WhatsApp webhook message for a specific client/tenant.
    Uses PostgreSQL RPC or atomic insert on `processed_webhooks (client_id, message_id)`.
    Returns True if successfully claimed (first arrival).
    Returns False if already claimed (duplicate webhook).
    """
    if not client_id or not message_id:
        return True

    # 1. Try Supabase RPC for atomic PostgreSQL execution
    try:
        rpc_res = supabase.rpc("claim_webhook_message", {
            "p_client_id": client_id,
            "p_message_id": message_id,
        }).execute()
        if rpc_res.data is not None:
            return bool(rpc_res.data)
    except Exception as e:
        logger.debug("RPC claim_webhook_message not available, fallback to table insert: %s", e)

    # 2. Direct table insert fallback
    try:
        res = (
            supabase.table("processed_webhooks")
            .insert({"client_id": client_id, "message_id": message_id})
            .execute()
        )
        if res.data:
            return True
        return False
    except Exception as ex:
        err_str = str(ex).lower()
        if "duplicate" in err_str or "unique" in err_str or "conflict" in err_str or "primary key" in err_str or "23505" in err_str:
            return False
        logger.warning("claim_webhook_message error: %s", ex)
        return True


def is_webhook_message_claimed(client_id: str, message_id: str) -> bool:
    """Checks whether a message has already been claimed for a client."""
    if not client_id or not message_id:
        return False
    try:
        res = (
            supabase.table("processed_webhooks")
            .select("message_id")
            .eq("client_id", client_id)
            .eq("message_id", message_id)
            .execute()
        )
        return bool(res.data and len(res.data) > 0)
    except Exception:
        return False


def mark_message_sending(message_log_id: str) -> bool:
    """
    Atomically claims authority to send an outbound message by transitioning
    message_log status from 'queued' to 'sending' and incrementing retry_count
    via the claim_outbound_message PostgreSQL RPC.

    Returns True if acquisition succeeded (message was in 'queued' state and claimed),
    or False if acquisition was rejected (not queued) or if an RPC/DB error occurred.
    Fail-closed: strictly avoids non-atomic SELECT+UPDATE fallback to eliminate duplicate-send risk.
    """
    if not message_log_id:
        return False

    try:
        rpc_res = supabase.rpc("claim_outbound_message", {"p_message_log_id": str(message_log_id)}).execute()
        if rpc_res.data is not None:
            return bool(rpc_res.data)
        return False
    except Exception as e:
        logger.error("claim_outbound_message RPC error for id %s: %s", message_log_id, e)
        return False


def mark_message_sent(message_log_id: str, evolution_message_id: str = None) -> bool:
    """Marks an outbound message as sent with timestamp and optional Evolution message ID."""
    if not message_log_id:
        return False
    now_iso = datetime.now(timezone.utc).isoformat()
    upd_payload = {
        "status": "sent",
        "sent_at": now_iso,
    }
    if evolution_message_id:
        upd_payload["evolution_message_id"] = str(evolution_message_id)
    try:
        upd = supabase.table("message_log").update(upd_payload).eq("id", message_log_id).execute()
        return bool(upd.data)
    except Exception as ex:
        logger.warning("mark_message_sent error for id %s: %s", message_log_id, ex)
        return False


def mark_message_failed(message_log_id: str, error_message: str) -> bool:
    """Marks an outbound message as definitely failed with sanitized error reason."""
    if not message_log_id:
        return False
    clean_err = str(error_message)[:500] if error_message else "Send failed"
    try:
        upd = (
            supabase.table("message_log")
            .update({
                "status": "failed",
                "error_message": clean_err,
            })
            .eq("id", message_log_id)
            .execute()
        )
        return bool(upd.data)
    except Exception as ex:
        logger.warning("mark_message_failed error for id %s: %s", message_log_id, ex)
        return False


def mark_message_unknown(message_log_id: str, error_message: str) -> bool:
    """Marks an outbound message as unknown delivery state (timeout/network drop)."""
    if not message_log_id:
        return False
    clean_err = str(error_message)[:500] if error_message else "Delivery unconfirmed (timeout/network error)"
    try:
        upd = (
            supabase.table("message_log")
            .update({
                "status": "unknown",
                "error_message": clean_err,
            })
            .eq("id", message_log_id)
            .execute()
        )
        return bool(upd.data)
    except Exception as ex:
        logger.warning("mark_message_unknown error for id %s: %s", message_log_id, ex)
        return False


def get_queued_outbound_messages() -> list[dict]:
    """
    Fetches all outbound messages currently in 'queued' status on startup.
    Joined with suppliers(phone_number) to retrieve recipient phone number for re-enqueuing.
    """
    try:
        res = (
            supabase.table("message_log")
            .select("*, suppliers(phone_number)")
            .eq("direction", "outbound")
            .eq("status", "queued")
            .order("created_at", desc=False)
            .execute()
        )
        return res.data or []
    except Exception as ex:
        logger.warning("get_queued_outbound_messages error: %s", ex)
        return []

