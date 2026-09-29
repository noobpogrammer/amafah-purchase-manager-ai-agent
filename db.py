"""
Supabase data access layer. Plain env-var config so credentials can be
plugged in whenever the Supabase project is ready.
"""

import logging
import math
import os
import secrets
import uuid
from typing import Any, Dict, List, Optional, Tuple
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
    (abandon old row + insert new row) in a single transaction without mutating rfq_suppliers.
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


def normalize_message_id(raw_id: str) -> Optional[str]:
    """
    Normalizes a WhatsApp / Evolution message ID by:
    - Stripping surrounding whitespace/quotes.
    - Extracting the suffix ONLY if formatted as a compound key (e.g., 'false_971501234567@s.whatsapp.net_3EB0428E3498' -> '3EB0428E3498').
    - Stripping trailing device or jid suffixes like ':1' or '@s.whatsapp.net' or '@lid'.
    """
    if not raw_id or not isinstance(raw_id, str):
        return None
    cleaned = raw_id.strip().strip("'\"")
    if not cleaned:
        return None
    if (cleaned.startswith("true_") or cleaned.startswith("false_")) and "@" in cleaned:
        parts = cleaned.split("_")
        cleaned = parts[-1].strip()
    if "@" in cleaned:
        cleaned = cleaned.split("@")[0].strip()
    if ":" in cleaned and not cleaned.startswith("http"):
        cleaned = cleaned.split(":")[0].strip()
    return cleaned if cleaned else None



def update_rfq_supplier_sent_message_id(
    rfq_id: str,
    supplier_id: str,
    sent_message_id: str,
    sent_message_alt_id: str = None,
):
    """Saves the Evolution API / WhatsApp outgoing message ID(s) to rfq_suppliers."""
    payload = {"sent_message_id": sent_message_id}
    if sent_message_alt_id:
        payload["sent_message_alt_id"] = sent_message_alt_id
    return supabase.table("rfq_suppliers").update(payload).eq("rfq_id", rfq_id).eq("supplier_id", supplier_id).execute()


def get_rfq_supplier_by_sent_message_id(supplier_id: str, sent_message_id: str):
    """
    Looks up an active rfq_suppliers record for a supplier that matches sent_message_id
    (or sent_message_alt_id, or an outbound message_log row for the same supplier).

    Fail-closed: Returns the entry joined with rfqs(*) if and only if the underlying RFQ is active
    and supplier status is in ('sent', 'clarifying', 'responded').
    """
    if not supplier_id or not sent_message_id:
        return None

    raw_id = str(sent_message_id).strip()
    norm_id = normalize_message_id(raw_id)
    search_ids = list(dict.fromkeys([i for i in [raw_id, norm_id] if i]))

    # 1. Primary lookup: match on rfq_suppliers.sent_message_id
    for sid in search_ids:
        try:
            res = (
                supabase.table("rfq_suppliers")
                .select("*, rfqs(*)")
                .eq("supplier_id", supplier_id)
                .eq("sent_message_id", sid)
                .in_("status", ["sent", "clarifying", "responded"])
                .execute()
            )
            active_entries = [entry for entry in (res.data or []) if is_rfq_open(entry.get("rfqs"))]
            if active_entries:
                return active_entries[0]
        except Exception as e:
            logger.warning("Error querying rfq_suppliers by sent_message_id=%s: %s", sid, e)

    # 2. Secondary lookup: match on rfq_suppliers.sent_message_alt_id
    for sid in search_ids:
        try:
            res = (
                supabase.table("rfq_suppliers")
                .select("*, rfqs(*)")
                .eq("supplier_id", supplier_id)
                .eq("sent_message_alt_id", sid)
                .in_("status", ["sent", "clarifying", "responded"])
                .execute()
            )
            active_entries = [entry for entry in (res.data or []) if is_rfq_open(entry.get("rfqs"))]
            if active_entries:
                return active_entries[0]
        except Exception as e:
            logger.warning("Error querying rfq_suppliers by sent_message_alt_id=%s: %s", sid, e)

    # 3. Tertiary lookup: match via outbound message_log rows for the same supplier
    # This covers cases where reminders, clarifications, or counteroffers were sent for the same RFQ
    for sid in search_ids:
        for col in ["external_message_id", "external_alt_message_id", "evolution_message_id"]:
            try:
                msg_res = (
                    supabase.table("message_log")
                    .select("id, related_rfq_id, supplier_id")
                    .eq("supplier_id", supplier_id)
                    .eq("direction", "outbound")
                    .eq(col, sid)
                    .not_.is_("related_rfq_id", "null")
                    .limit(1)
                    .execute()
                )
                if msg_res.data:
                    related_rfq_id = msg_res.data[0].get("related_rfq_id")
                    if related_rfq_id:
                        rfq_supp_res = (
                            supabase.table("rfq_suppliers")
                            .select("*, rfqs(*)")
                            .eq("supplier_id", supplier_id)
                            .eq("rfq_id", related_rfq_id)
                            .in_("status", ["sent", "clarifying", "responded"])
                            .execute()
                        )
                        active_entries = [entry for entry in (rfq_supp_res.data or []) if is_rfq_open(entry.get("rfqs"))]
                        if active_entries:
                            return active_entries[0]
            except Exception as e:
                logger.warning("Error querying message_log by %s=%s: %s", col, sid, e)

    return None



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
    Deprecated/Safe no-op: RFQ business lifecycle (rfq_suppliers.status) is decoupled from
    conversational clarification state. Pending clarifications are resolved in pending_clarifications
    without mutating rfq_suppliers statuses (guaranteeing 'responded' never reverts to 'sent').
    """
    pass



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


def update_message_related_rfq(message_id: str, rfq_id: str):
    """
    Updates the related_rfq_id column of a message_log row once the RFQ association
    becomes known (via stanza match, clarification resolution, unanswered routing, etc.).
    """
    if not message_id or not rfq_id:
        return None
    try:
        res = supabase.table("message_log").update({"related_rfq_id": str(rfq_id)}).eq("id", str(message_id)).execute()
        return res.data[0] if res and res.data else None
    except Exception as e:
        logger.warning("Failed to update message_log %s related_rfq_id to %s: %s", message_id, rfq_id, e)
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


PRICE_TOLERANCE_AED = 3.0
MAX_NEGOTIATION_ATTEMPTS = int(os.environ.get("MAX_NEGOTIATION_ATTEMPTS", 10))


def classify_price_position(
    quote_price: float,
    acceptable_min: float = None,
    acceptable_max: float = None,
) -> str:
    """
    Deterministically classifies a supplier quote against the RFQ's negotiation price range.

    The old percentage-based 1.15 threshold was intentionally removed because it
    scales too aggressively with item price. The upper tolerance is now a fixed
    AED 3.00.

    Returns:
      - 'NO_RANGE_SET' if neither boundary is provided
      - 'AT_OR_BELOW_MIN' if quote_price <= acceptable_min
      - 'AT_MAX' if quote_price == acceptable_max
      - 'WITHIN_ACCEPTABLE_RANGE' if acceptable_min < quote_price < acceptable_max
      - 'ABOVE_ACCEPTABLE_RANGE' if max < quote_price <= max + AED 3
      - 'SIGNIFICANTLY_ABOVE_RANGE' if quote_price > max + AED 3
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
        return (
            "ABOVE_ACCEPTABLE_RANGE"
            if quote_p <= max_p + PRICE_TOLERANCE_AED
            else "SIGNIFICANTLY_ABOVE_RANGE"
        )
    if min_p is not None:
        return (
            "ABOVE_ACCEPTABLE_RANGE"
            if quote_p <= min_p + PRICE_TOLERANCE_AED
            else "SIGNIFICANTLY_ABOVE_RANGE"
        )

    return "WITHIN_ACCEPTABLE_RANGE"


def build_negotiation_price_context(
    acceptable_min: float = None,
    acceptable_max: float = None,
    last_quote: float = None,
    current_quote: float = None,
    tolerance_aed: float = PRICE_TOLERANCE_AED,
) -> dict | None:
    """
    Builds the deterministic price-policy context used after a quote is persisted.

    Target priority:
      1. acceptable_price_min — the optimum procurement target
      2. historical last_quote — fallback when no acceptable minimum is configured
      3. acceptable_price_max — final fallback target

    Final tolerance ceiling:
      - acceptable_price_max + tolerance_aed when max exists
      - otherwise preferred_target + tolerance_aed

    A quote above the preferred target should be negotiated toward the target while
    attempts remain, even when the quote is already inside the broad acceptable range.
    """
    if current_quote is None:
        return None

    def _positive_float(value):
        if value is None:
            return None
        try:
            number = float(value)
            if math.isnan(number) or math.isinf(number) or number <= 0:
                return None
            return number
        except (TypeError, ValueError):
            return None

    current = _positive_float(current_quote)
    min_p = _positive_float(acceptable_min)
    max_p = _positive_float(acceptable_max)
    last_p = _positive_float(last_quote)
    tolerance = _positive_float(tolerance_aed) or PRICE_TOLERANCE_AED

    if current is None:
        return None

    if min_p is not None:
        preferred_target = min_p
        target_source = "acceptable_price_min"
    elif last_p is not None:
        preferred_target = last_p
        target_source = "last_quote"
    elif max_p is not None:
        preferred_target = max_p
        target_source = "acceptable_price_max"
    else:
        return None

    ceiling_base = max_p if max_p is not None else preferred_target
    tolerated_final_ceiling = round(ceiling_base + tolerance, 2)

    return {
        "current_quote": current,
        "acceptable_min": min_p,
        "acceptable_max": max_p,
        "last_quote": last_p,
        "preferred_target": preferred_target,
        "target_source": target_source,
        "tolerance_aed": tolerance,
        "tolerated_final_ceiling": tolerated_final_ceiling,
        "within_tolerance": current <= tolerated_final_ceiling,
        "at_or_below_target": current <= preferred_target,
        "should_negotiate": current > preferred_target,
        "price_position": classify_price_position(current, min_p, max_p),
    }


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


def increment_negotiation_attempts(rfq_id: str, supplier_id: str, max_attempts: int = MAX_NEGOTIATION_ATTEMPTS) -> int:
    """
    Atomically increments the negotiation attempt counter on rfq_suppliers
    only if current attempts < max_attempts.
    Returns the new attempt count (e.g. 1..10) on success, or -1 if the limit was reached / update failed.
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


def expire_negotiation_sessions_for_rfq(rfq_id: str) -> int:
    """Idempotently expires all active or awaiting negotiation sessions for an RFQ."""
    now_utc = datetime.now(timezone.utc).isoformat()
    try:
        try:
            res = supabase.rpc("expire_negotiation_sessions_for_rfq_rpc", {"p_rfq_id": rfq_id}).execute()
            if res.data is not None:
                return int(res.data)
        except Exception:
            pass

        res = (
            supabase.table("negotiation_sessions")
            .update({
                "status": "expired",
                "completed_at": now_utc,
                "updated_at": now_utc,
            })
            .eq("rfq_id", rfq_id)
            .in_("status", ["active", "awaiting_human_review", "awaiting_authorization"])
            .execute()
        )
        return len(res.data) if res.data else 0
    except Exception as e:
        logger.error("expire_negotiation_sessions_for_rfq error for RFQ %s: %s", rfq_id, e)
        return 0


def get_active_negotiation_session(client_id: str, rfq_id: str, supplier_id: str) -> dict | None:
    """Retrieves active negotiation session for a specific rfq and supplier, verifying RFQ is active and not expired."""
    try:
        rfq = get_rfq_by_id(rfq_id)
        if rfq and rfq.get("status") != "active":
            expire_negotiation_sessions_for_rfq(rfq_id)
            return None
        if rfq and rfq.get("due_by"):
            try:
                due_dt = datetime.fromisoformat(str(rfq["due_by"]).replace("Z", "+00:00"))
                if due_dt <= datetime.now(timezone.utc):
                    expire_negotiation_sessions_for_rfq(rfq_id)
                    return None
            except Exception:
                pass

        res = (
            supabase.table("negotiation_sessions")
            .select("*")
            .eq("client_id", client_id)
            .eq("rfq_id", rfq_id)
            .eq("supplier_id", supplier_id)
            .eq("status", "active")
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        logger.error("get_active_negotiation_session error: %s", e)
    return None


def get_active_negotiation_session_for_supplier(client_id: str, supplier_id: str) -> dict | None:
    """Retrieves the most recently updated active negotiation session for a supplier across any open, unexpired RFQ."""
    try:
        res = (
            supabase.table("negotiation_sessions")
            .select("*, rfqs(*)")
            .eq("client_id", client_id)
            .eq("supplier_id", supplier_id)
            .eq("status", "active")
            .order("updated_at", desc=True)
            .execute()
        )
        if res.data and len(res.data) > 0:
            now_dt = datetime.now(timezone.utc)
            for row in res.data:
                rfq = row.get("rfqs")
                rfq_id = row.get("rfq_id")
                if not rfq or not isinstance(rfq, dict):
                    rfq = get_rfq_by_id(rfq_id) if rfq_id else None
                if not rfq or rfq.get("status") != "active":
                    if rfq_id:
                        expire_negotiation_sessions_for_rfq(rfq_id)
                    continue
                due_by = rfq.get("due_by")
                if due_by:
                    try:
                        due_dt = datetime.fromisoformat(str(due_by).replace("Z", "+00:00"))
                        if due_dt <= now_dt:
                            if rfq_id:
                                expire_negotiation_sessions_for_rfq(rfq_id)
                            continue
                    except Exception:
                        pass
                return row
    except Exception as e:
        logger.error("get_active_negotiation_session_for_supplier error: %s", e)
    return None


def get_negotiation_session_by_id(
    session_id: str,
    client_id: str,
    rfq_id: Optional[str] = None,
    supplier_id: Optional[str] = None,
) -> dict | None:
    """Retrieves a specific negotiation session by id strictly scoped to client_id, optionally validating rfq_id and supplier_id."""
    if not session_id or not client_id:
        return None
    try:
        query = (
            supabase.table("negotiation_sessions")
            .select("*")
            .eq("id", session_id)
            .eq("client_id", client_id)
        )
        if rfq_id:
            query = query.eq("rfq_id", rfq_id)
        if supplier_id:
            query = query.eq("supplier_id", supplier_id)
        res = query.limit(1).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        logger.error("get_negotiation_session_by_id error: %s", e)
    return None


def resume_negotiation_session(
    session_id: str,
    client_id: str,
    rfq_id: str,
    supplier_id: str,
) -> dict | None:
    """
    Resumes a paused negotiation session (e.g. from awaiting_authorization or awaiting_human_review back to active).
    Validates tenant isolation, matching RFQ and supplier, that RFQ is active and deadline has not passed.
    Preserves all negotiation history, attempt counts, offers, and strategy states.
    """
    if not session_id or not client_id or not rfq_id or not supplier_id:
        return None

    try:
        # 1. Verify RFQ active and not expired
        rfq = get_rfq_by_id(rfq_id)
        if not rfq or rfq.get("status") != "active":
            return None
        if rfq.get("due_by"):
            try:
                due_dt = datetime.fromisoformat(str(rfq["due_by"]).replace("Z", "+00:00"))
                if due_dt <= datetime.now(timezone.utc):
                    return None
            except Exception:
                pass

        # 2. Get session and verify status and ownership
        session = get_negotiation_session_by_id(
            session_id=session_id,
            client_id=client_id,
            rfq_id=rfq_id,
            supplier_id=supplier_id,
        )
        if not session:
            return None

        current_status = session.get("status")
        if current_status not in ("awaiting_authorization", "awaiting_human_review", "active"):
            return None

        now_utc = datetime.now(timezone.utc).isoformat()
        res = (
            supabase.table("negotiation_sessions")
            .update({
                "status": "active",
                "completed_at": None,
                "updated_at": now_utc,
            })
            .eq("id", session_id)
            .eq("client_id", client_id)
            .eq("rfq_id", rfq_id)
            .eq("supplier_id", supplier_id)
            .execute()
        )
        if res.data and len(res.data) > 0:
            return res.data[0]
    except Exception as e:
        logger.error("resume_negotiation_session error: %s", e)
    return None


def create_or_update_negotiation_session(client_id: str, rfq_id: str, supplier_id: str, **kwargs) -> dict | None:
    """Creates a new active negotiation session or updates the existing active session.
    Fails closed (returns None) if persistence fails.
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    try:
        existing = get_active_negotiation_session(client_id, rfq_id, supplier_id)
        if existing:
            update_data = {**kwargs, "updated_at": now_utc}
            res = (
                supabase.table("negotiation_sessions")
                .update(update_data)
                .eq("id", existing["id"])
                .execute()
            )
            return res.data[0] if (res.data and len(res.data) > 0) else None
        else:
            insert_data = {
                "client_id": client_id,
                "rfq_id": rfq_id,
                "supplier_id": supplier_id,
                "status": "active",
                "created_at": now_utc,
                "updated_at": now_utc,
                **kwargs,
            }
            res = supabase.table("negotiation_sessions").insert(insert_data).execute()
            return res.data[0] if (res.data and len(res.data) > 0) else None
    except Exception as e:
        logger.error("create_or_update_negotiation_session error: %s", e)
        return None


def complete_negotiation_session(session_id: str, status: str, **kwargs) -> dict | None:
    """Marks a negotiation session completed with a final status."""
    now_utc = datetime.now(timezone.utc).isoformat()
    update_data = {
        "status": status,
        "completed_at": now_utc,
        "updated_at": now_utc,
        **kwargs,
    }
    try:
        res = (
            supabase.table("negotiation_sessions")
            .update(update_data)
            .eq("id", session_id)
            .execute()
        )
        return res.data[0] if (res.data and len(res.data) > 0) else None
    except Exception as e:
        logger.error("complete_negotiation_session error: %s", e)
        return None


def deactivate_negotiation_session(session_id: str, reason: str = "deactivated"):
    """Deactivates a negotiation session (e.g. on RFQ close or operator switch)."""
    return complete_negotiation_session(session_id, status="expired")


def get_rfq_negotiation_constraints(client_id: str, rfq_id: str) -> list[dict]:
    """Retrieves all negotiation constraint dimensions for an RFQ."""
    try:
        res = (
            supabase.table("rfq_negotiation_constraints")
            .select("*")
            .eq("client_id", client_id)
            .eq("rfq_id", rfq_id)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error("get_rfq_negotiation_constraints error: %s", e)
        return []


def set_rfq_negotiation_constraint(
    client_id: str,
    rfq_id: str,
    dimension: str,
    status: str,
    constraints: dict,
    source: str = "rfq_creation",
    authorized_by: Optional[str] = None,
) -> dict | None:
    """Upserts a specific dimension negotiation constraint for an RFQ."""
    now_utc = datetime.now(timezone.utc).isoformat()
    try:
        existing = (
            supabase.table("rfq_negotiation_constraints")
            .select("id")
            .eq("rfq_id", rfq_id)
            .eq("dimension", dimension)
            .execute()
        )
        payload = {
            "client_id": client_id,
            "rfq_id": rfq_id,
            "dimension": dimension,
            "status": status,
            "constraints": constraints or {},
            "source": source,
            "authorized_by": authorized_by,
            "updated_at": now_utc,
        }
        if existing.data and len(existing.data) > 0:
            res = (
                supabase.table("rfq_negotiation_constraints")
                .update(payload)
                .eq("id", existing.data[0]["id"])
                .execute()
            )
        else:
            payload["created_at"] = now_utc
            res = supabase.table("rfq_negotiation_constraints").insert(payload).execute()
        return res.data[0] if (res.data and len(res.data) > 0) else None
    except Exception as e:
        logger.error("set_rfq_negotiation_constraint error: %s", e)
        return None


def build_default_negotiation_constraints_payload(
    client_id: str,
    rfq_data: dict,
    flexibility: Optional[dict] = None,
    authorized_by: Optional[str] = None,
) -> list[dict]:
    """
    Constructs the list of negotiation constraint records for all 4 dimensions
    using the canonical nested flexibility payload format:
    {
      "quantity": {"authorized": bool, "min": int, "max": int},
      "delivery": {"authorized": bool, "max_days": int},
      "specification": {"authorized": bool, "allowed_alternatives": str}
    }
    """
    flex = flexibility or {}
    results = []

    # 1. Price
    price_constraints = {}
    if rfq_data.get("acceptable_price_min") is not None:
        price_constraints["preferred_target"] = float(rfq_data["acceptable_price_min"])
    elif rfq_data.get("last_quote") is not None:
        price_constraints["preferred_target"] = float(rfq_data["last_quote"])
    if rfq_data.get("acceptable_price_max") is not None:
        price_constraints["max"] = float(rfq_data["acceptable_price_max"])

    results.append({
        "client_id": client_id,
        "dimension": "price",
        "status": "authorized",
        "constraints": price_constraints,
        "source": "rfq_creation",
        "authorized_by": authorized_by,
    })

    # 2. Quantity
    req_qty = rfq_data.get("quantity")
    qty_flex = flex.get("quantity") if isinstance(flex.get("quantity"), dict) else None
    is_qty_auth = bool(
        (qty_flex and qty_flex.get("authorized"))
        or flex.get("quantity_flexible")
    )
    if is_qty_auth:
        q_min = qty_flex.get("min") if qty_flex else flex.get("quantity_min")
        q_max = qty_flex.get("max") if qty_flex else flex.get("quantity_max")
        if q_min is None:
            q_min = req_qty
        if q_max is None:
            q_max = req_qty
        q_constraints = {
            "required": req_qty,
            "min": int(q_min) if q_min is not None else None,
            "max": int(q_max) if q_max is not None else None,
        }
        q_status = "authorized"
    else:
        q_status = "fixed"
        q_constraints = {"required": req_qty}

    results.append({
        "client_id": client_id,
        "dimension": "quantity",
        "status": q_status,
        "constraints": q_constraints,
        "source": "rfq_creation",
        "authorized_by": authorized_by,
    })

    # 3. Delivery
    req_days = rfq_data.get("required_delivery_days")
    deliv_flex = flex.get("delivery") if isinstance(flex.get("delivery"), dict) else None
    is_deliv_auth = bool(
        (deliv_flex and deliv_flex.get("authorized"))
        or flex.get("delivery_flexible")
    )
    if is_deliv_auth:
        d_max = deliv_flex.get("max_days") if deliv_flex else flex.get("delivery_max_days")
        d_constraints = {
            "required_days": req_days,
            "max_days": int(d_max) if d_max is not None else None,
        }
        d_status = "authorized"
    else:
        d_status = "fixed"
        d_constraints = {"required_days": req_days}

    results.append({
        "client_id": client_id,
        "dimension": "delivery",
        "status": d_status,
        "constraints": d_constraints,
        "source": "rfq_creation",
        "authorized_by": authorized_by,
    })

    # 4. Specification
    spec_flex = flex.get("specification") if isinstance(flex.get("specification"), dict) else None
    is_spec_auth = bool(
        (spec_flex and spec_flex.get("authorized"))
        or flex.get("specs_flexible")
    )
    alt = spec_flex.get("allowed_alternatives") if spec_flex else flex.get("allowed_alternatives")
    if is_spec_auth and alt and str(alt).strip():
        s_status = "authorized"
        s_constraints = {"allowed_alternatives": str(alt).strip()}
    else:
        s_status = "fixed"
        s_constraints = {"allowed_alternatives": None}

    results.append({
        "client_id": client_id,
        "dimension": "specification",
        "status": s_status,
        "constraints": s_constraints,
        "source": "rfq_creation",
        "authorized_by": authorized_by,
    })

    return results


def create_default_rfq_negotiation_constraints(
    client_id: str,
    rfq_id: str,
    rfq_data: dict,
    flexibility: Optional[dict] = None,
    authorized_by: Optional[str] = None,
) -> list[dict]:
    """
    Initializes default negotiation constraints for all 4 dimensions:
    - price: authorized from acceptable_price_min / max / last_quote
    - quantity: fixed unless flexibility authorized
    - delivery: fixed unless flexibility authorized
    - specification: fixed unless flexibility authorized
    """
    constraints_payload = build_default_negotiation_constraints_payload(
        client_id=client_id,
        rfq_data=rfq_data,
        flexibility=flexibility,
        authorized_by=authorized_by,
    )
    results = []
    for item in constraints_payload:
        saved = set_rfq_negotiation_constraint(
            client_id=client_id,
            rfq_id=rfq_id,
            dimension=item["dimension"],
            status=item["status"],
            constraints=item["constraints"],
            source=item.get("source", "rfq_creation"),
            authorized_by=item.get("authorized_by"),
        )
        if saved:
            results.append(saved)
    return results



def build_historical_price_context(last_quote: float = None, current_quote: float = None) -> dict:
    """
    Deterministically computes trusted historical price comparison metrics.
    Used only as a historical fallback when no acceptable minimum is configured.
    preferred_target = last_quote
    tolerance_aed = AED 3.0
    tolerated_final_ceiling = last_quote + AED 3.0
    """
    if last_quote is None or current_quote is None:
        return None
    try:
        lq = float(last_quote)
        cq = float(current_quote)
        if math.isnan(lq) or math.isinf(lq) or lq <= 0 or math.isnan(cq) or math.isinf(cq) or cq <= 0:
            return None
        diff_aed = round(cq - lq, 2)
        diff_pct = round(((cq - lq) / lq) * 100.0, 4) if lq > 0 else 0.0
        tolerance_aed = PRICE_TOLERANCE_AED
        tolerated_ceiling = round(lq + tolerance_aed, 2)
        within_tolerance = bool(cq <= tolerated_ceiling)
        at_or_below_target = bool(cq <= lq)
        return {
            "last_quote": lq,
            "current_quote": cq,
            "difference_aed": diff_aed,
            "difference_percent": diff_pct,
            "preferred_target": lq,
            "tolerance_aed": tolerance_aed,
            "tolerated_final_ceiling": tolerated_ceiling,
            "within_tolerance": within_tolerance,
            "at_or_below_target": at_or_below_target,
        }
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def create_rfq_and_match_suppliers(
    client_id: str,
    product_name: str,
    category: str,
    deadline_hours: int = 24,
    specs: str = None,
    quantity: int = None,
    last_quote: float = None,
    acceptable_price_min: float = None,
    acceptable_price_max: float = None,
    required_delivery_days: int = None,
    flexibility: Optional[dict] = None,
    authorized_by: Optional[str] = None,
):
    """
    Atomically creates a new RFQ row and initial rfq_negotiation_constraints in a single PostgreSQL transaction
    using create_rfq_with_constraints_rpc. Then matches active suppliers by category and creates rfq_suppliers join records.
    """
    rfq_data = {
        "client_id": client_id,
        "product_name": product_name,
        "category": category,
        "specs": specs,
        "quantity": quantity,
        "deadline_hours": deadline_hours,
        "last_quote": last_quote,
        "acceptable_price_min": acceptable_price_min,
        "acceptable_price_max": acceptable_price_max,
        "required_delivery_days": required_delivery_days,
    }

    constraints_payload = build_default_negotiation_constraints_payload(
        client_id=client_id,
        rfq_data=rfq_data,
        flexibility=flexibility,
        authorized_by=authorized_by,
    )

    rpc_params = {
        "p_client_id": client_id,
        "p_product_name": product_name,
        "p_category": category,
        "p_specs": specs,
        "p_quantity": quantity,
        "p_last_quote": last_quote,
        "p_acceptable_price_min": acceptable_price_min,
        "p_acceptable_price_max": acceptable_price_max,
        "p_deadline_hours": deadline_hours,
        "p_required_delivery_days": required_delivery_days,
        "p_constraints": constraints_payload,
    }

    rpc_res = supabase.rpc("create_rfq_with_constraints_rpc", rpc_params).execute()
    if not rpc_res or not rpc_res.data:
        raise RuntimeError("create_rfq_with_constraints_rpc returned empty response")
    res_data = rpc_res.data
    rfq = res_data[0] if isinstance(res_data, list) and len(res_data) > 0 else res_data

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


def get_rfq_by_id(rfq_id: str, client_id: str = None) -> dict | None:
    """Fetches a single RFQ by ID, optionally scoped by client_id."""
    if not rfq_id:
        return None
    try:
        query = supabase.table("rfqs").select("*").eq("id", rfq_id)
        if client_id:
            query = query.eq("client_id", client_id)
        res = query.execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.warning("get_rfq_by_id error for %s: %s", rfq_id, e)
        return None


def get_quote_by_id(quote_id: str, client_id: str = None, supplier_id: str = None, rfq_id: str = None) -> dict | None:
    """Fetches a single quote row by ID joined with its RFQ details, optionally scoped by client/supplier/rfq."""
    if not quote_id:
        return None
    try:
        query = supabase.table("quotes").select("*, rfqs(*)").eq("id", quote_id)
        if supplier_id:
            query = query.eq("supplier_id", supplier_id)
        if rfq_id:
            query = query.eq("rfq_id", rfq_id)
        res = query.execute()
        row = res.data[0] if res and res.data else None
        if row and client_id:
            rfq_obj = row.get("rfqs") or {}
            if str(rfq_obj.get("client_id")) != str(client_id):
                return None
        return row
    except Exception as e:
        logger.warning(f"get_quote_by_id error for {quote_id}: {e}")
        return None


def get_supplier_prior_quotes(supplier_id: str, rfq_ids: list = None):
    """Fetches past quote(s) for a supplier to serve as prior context for Groq contradiction detection."""
    query = supabase.table("quotes").select("*, rfqs(product_name)").eq("supplier_id", supplier_id)
    if rfq_ids:
        query = query.in_("rfq_id", rfq_ids)
    res = query.order("created_at", desc=True).order("id", desc=True).limit(10).execute()
    return res.data


def flag_for_human_review(
    client_id: str,
    supplier_id: str,
    rfq_id: str = None,
    reason: str = "",
    category: str = "other",
    raw_message: str = "",
    metadata: dict = None,
):
    """Inserts a new human review escalation into flagged_for_review table."""
    payload = {
        "client_id": client_id,
        "supplier_id": supplier_id,
        "reason": reason,
        "category": category,
        "raw_message": raw_message,
        "status": "pending",
        "metadata": metadata or {},
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


def resolve_flag(flag_id: str, client_id: str = None, human_response: str = None):
    """Marks a flagged_for_review item as resolved (administrative dismissal or resolution)."""
    from datetime import datetime, timezone

    update_payload = {
        "status": "resolved",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    if human_response is not None:
        update_payload["human_response"] = human_response

    query = (
        supabase.table("flagged_for_review")
        .update(update_payload)
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

    # 4. Expire any active/awaiting negotiation sessions for this RFQ
    expire_negotiation_sessions_for_rfq(rfq_id)

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


def mark_message_sent(
    message_log_id: str,
    evolution_message_id: str = None,
    external_message_id: str = None,
    external_alt_message_id: str = None,
) -> bool:
    """Marks an outbound message as sent with timestamp and Evolution / WhatsApp message IDs."""
    if not message_log_id:
        return False
    now_iso = datetime.now(timezone.utc).isoformat()
    upd_payload = {
        "status": "sent",
        "sent_at": now_iso,
    }
    primary_id = external_message_id or evolution_message_id
    if primary_id:
        upd_payload["evolution_message_id"] = str(primary_id)
        upd_payload["external_message_id"] = str(primary_id)
    if external_alt_message_id:
        upd_payload["external_alt_message_id"] = str(external_alt_message_id)

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


# ============================================================
# Phase 14: Decision Auditability & Operational Hardening
# ============================================================

DISALLOWED_ARG_KEYS = {
    "system_prompt", "prompt", "internal_prompt", "chain_of_thought",
    "thought", "reasoning", "model_scratchpad", "api_key", "token",
    "secret", "authorization", "password"
}


def sanitize_decision_arguments(args: dict) -> dict:
    """Sanitizes arguments dictionary by stripping prompts, tokens, and secrets."""
    if not isinstance(args, dict):
        return {}
    clean = {}
    for k, v in args.items():
        if str(k).lower() in DISALLOWED_ARG_KEYS:
            continue
        if isinstance(v, (str, int, float, bool, list)) or v is None:
            clean[k] = v
        elif isinstance(v, dict):
            clean[k] = sanitize_decision_arguments(v)
    return clean


def record_agent_decision(
    client_id: str,
    origin: str,
    tool_name: str,
    arguments: dict,
    validation_status: str,
    validation_reason: str = None,
    execution_status: str = "pending",
    execution_error: str = None,
    rfq_id: str = None,
    supplier_id: str = None,
    inbound_message_id: str = None,
    outbound_message_id: str = None,
    flag_id: str = None,
    executed_at: str = None,
) -> dict:
    """Inserts a structured agent decision record into agent_decisions without storing chain-of-thought."""
    clean_args = sanitize_decision_arguments(arguments)
    payload = {
        "client_id": client_id,
        "origin": origin,
        "tool_name": tool_name,
        "arguments": clean_args,
        "validation_status": validation_status,
        "validation_reason": validation_reason,
        "execution_status": execution_status,
        "execution_error": execution_error,
        "rfq_id": rfq_id,
        "supplier_id": supplier_id,
        "inbound_message_id": inbound_message_id,
        "outbound_message_id": outbound_message_id,
        "flag_id": flag_id,
    }
    if executed_at:
        payload["executed_at"] = executed_at

    try:
        res = supabase.table("agent_decisions").insert(payload).execute()
        if res.data and len(res.data) > 0:
            return res.data[0]
        return {"id": "mock-decision-id", **payload}
    except Exception as e:
        logger.warning("record_agent_decision error: %s", e)
        return {"id": "fallback-decision-id", **payload}


def update_agent_decision(decision_id: str, **updates) -> dict | None:
    """Updates an existing agent_decision record."""
    if not decision_id or decision_id.startswith("mock") or decision_id.startswith("fallback"):
        return None
    allowed_fields = {
        "validation_status", "validation_reason", "execution_status",
        "execution_error", "outbound_message_id", "executed_at",
        "rfq_id", "supplier_id", "arguments", "flag_id"
    }
    payload = {k: v for k, v in updates.items() if k in allowed_fields}
    if "arguments" in payload:
        payload["arguments"] = sanitize_decision_arguments(payload["arguments"])
    try:
        res = supabase.table("agent_decisions").update(payload).eq("id", decision_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.warning("update_agent_decision error for %s: %s", decision_id, e)
        return None


def get_agent_decisions_for_rfq(rfq_id: str, client_id: str) -> list[dict]:
    """Fetches agent decisions for a specific RFQ and client."""
    try:
        res = (
            supabase.table("agent_decisions")
            .select("*, suppliers(name, phone_number)")
            .eq("client_id", client_id)
            .eq("rfq_id", rfq_id)
            .order("created_at", desc=False)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.warning("get_agent_decisions_for_rfq error: %s", e)
        return []


def complete_webhook_message(client_id: str, message_id: str) -> bool:
    """Marks a webhook message as completed in processed_webhooks."""
    if not client_id or not message_id:
        return True
    try:
        res = supabase.rpc("complete_webhook_message", {
            "p_client_id": client_id,
            "p_message_id": message_id,
        }).execute()
        return bool(res.data)
    except Exception as e:
        logger.debug("RPC complete_webhook_message error, fallback to table update: %s", e)
        try:
            upd = (
                supabase.table("processed_webhooks")
                .update({"status": "completed", "completed_at": datetime.now(timezone.utc).isoformat()})
                .eq("client_id", client_id)
                .eq("message_id", message_id)
                .execute()
            )
            return bool(upd.data)
        except Exception as ex:
            logger.warning("complete_webhook_message fallback error: %s", ex)
            return False


def fail_webhook_message(client_id: str, message_id: str, error_message: str) -> bool:
    """Marks a webhook message as failed in processed_webhooks to allow controlled retry."""
    if not client_id or not message_id:
        return True
    clean_err = str(error_message)[:500] if error_message else "Processing failed"
    try:
        res = supabase.rpc("fail_webhook_message", {
            "p_client_id": client_id,
            "p_message_id": message_id,
            "p_error": clean_err,
        }).execute()
        return bool(res.data)
    except Exception as e:
        logger.debug("RPC fail_webhook_message error, fallback to table update: %s", e)
        try:
            upd = (
                supabase.table("processed_webhooks")
                .update({"status": "failed", "last_error": clean_err})
                .eq("client_id", client_id)
                .eq("message_id", message_id)
                .execute()
            )
            return bool(upd.data)
        except Exception as ex:
            logger.warning("fail_webhook_message fallback error: %s", ex)
            return False


def get_delivery_issues(client_id: str, page: int = 1, limit: int = 50) -> dict:
    """Returns paginated outbound messages with status 'failed' or 'unknown' for a client."""
    if not client_id:
        return {"items": [], "total": 0, "page": page, "limit": limit}
    try:
        offset = max(0, (page - 1) * limit)
        query = (
            supabase.table("message_log")
            .select("*, suppliers(name, phone_number), rfqs(product_name)", count="exact")
            .eq("client_id", client_id)
            .eq("direction", "outbound")
            .in_("status", ["failed", "unknown"])
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
        )
        res = query.execute()
        return {
            "items": res.data or [],
            "total": res.count if res.count is not None else len(res.data or []),
            "page": page,
            "limit": limit
        }
    except Exception as e:
        logger.warning("get_delivery_issues error: %s", e)
        return {"items": [], "total": 0, "page": page, "limit": limit}


def get_rfq_activity(rfq_id: str, client_id: str) -> list[dict] | None:
    """
    Constructs a presentation-ready chronological activity and decision audit timeline
    for an RFQ, strictly isolated to the caller's client_id.
    """
    if not rfq_id or not client_id:
        return None

    # 1. Fetch and verify RFQ tenant ownership
    rfq = get_rfq_by_id(rfq_id)
    if not rfq or str(rfq.get("client_id")) != str(client_id):
        return None

    timeline = []

    # RFQ Created event
    timeline.append({
        "id": f"rfq-created-{rfq_id}",
        "timestamp": rfq.get("created_at"),
        "event_type": "rfq_created",
        "title": "RFQ Created",
        "summary": f"Created RFQ for '{rfq.get('product_name')}' (Specs: {rfq.get('specs') or 'Standard'}, Qty: {rfq.get('quantity') or 'N/A'}, Deadline: {rfq.get('deadline_hours') or 24}h).",
        "status": rfq.get("status"),
        "origin": "system",
        "details": {
            "rfq_id": rfq_id,
            "product_name": rfq.get("product_name"),
            "deadline_hours": rfq.get("deadline_hours"),
            "status": rfq.get("status"),
        }
    })

    # 2. Outbound Broadcast Messages
    try:
        outbound_msgs = (
            supabase.table("message_log")
            .select("*, suppliers(name, phone_number)")
            .eq("client_id", client_id)
            .eq("related_rfq_id", rfq_id)
            .eq("direction", "outbound")
            .order("created_at", desc=False)
            .execute()
            .data or []
        )
        for msg in outbound_msgs:
            supp_name = msg.get("suppliers", {}).get("name") if isinstance(msg.get("suppliers"), dict) else "Supplier"
            msg_status = msg.get("status") or "sent"
            timeline.append({
                "id": f"msg-out-{msg['id']}",
                "timestamp": msg.get("created_at"),
                "event_type": "outbound_message",
                "title": f"Message Sent to {supp_name}",
                "summary": f"Outbound message dispatched ({msg_status}).",
                "supplier_name": supp_name,
                "status": msg_status,
                "origin": "system",
                "details": {
                    "message_log_id": msg["id"],
                    "delivery_status": msg_status,
                    "error_message": msg.get("error_message"),
                    "retry_count": msg.get("retry_count"),
                }
            })
    except Exception as e:
        logger.warning("Error fetching outbound messages for activity: %s", e)

    # 3. Quotes Recorded
    try:
        quotes = (
            supabase.table("quotes")
            .select("*, suppliers(name, phone_number)")
            .eq("rfq_id", rfq_id)
            .order("created_at", desc=False)
            .execute()
            .data or []
        )
        for q in quotes:
            supp_name = q.get("suppliers", {}).get("name") if isinstance(q.get("suppliers"), dict) else "Supplier"
            variant_str = f" ({q.get('variant_label')})" if q.get("variant_label") else ""
            deliv_str = f", Delivery: {q.get('delivery_time')}" if q.get("delivery_time") else ""
            timeline.append({
                "id": f"quote-{q['id']}",
                "timestamp": q.get("created_at"),
                "event_type": "quote_recorded",
                "title": f"Quote Received from {supp_name}",
                "summary": f"Quoted AED {q['price']}{variant_str}{deliv_str}.",
                "supplier_name": supp_name,
                "status": "recorded",
                "origin": "supplier",
                "details": {
                    "quote_id": q["id"],
                    "price": q.get("price"),
                    "variant_label": q.get("variant_label"),
                    "delivery_time": q.get("delivery_time"),
                    "quality_notes": q.get("quality_notes"),
                    "source_message_id": q.get("source_message_id"),
                    "raw_message": q.get("raw_message"),
                }
            })
    except Exception as e:
        logger.warning("Error fetching quotes for activity: %s", e)

    # 4. Agent Decisions
    try:
        decisions = (
            supabase.table("agent_decisions")
            .select("*, suppliers(name, phone_number)")
            .eq("client_id", client_id)
            .eq("rfq_id", rfq_id)
            .order("created_at", desc=False)
            .execute()
            .data or []
        )
        for d in decisions:
            tool = d.get("tool_name", "")
            val_status = d.get("validation_status", "approved")
            exec_status = d.get("execution_status", "executed")
            orig = d.get("origin", "supplier")
            args = d.get("arguments") or {}
            supp_name = d.get("suppliers", {}).get("name") if isinstance(d.get("suppliers"), dict) else "Supplier"

            if val_status == "rejected":
                event_type = "decision_rejected"
                title = f"Agent Action Blocked ({tool})"
                summary = f"Blocked by Policy Validator: {d.get('validation_reason') or 'Policy rule violated'}."
                status = "rejected"
            elif tool == "negotiate_price":
                event_type = "negotiation_sent"
                variant_part = f" for '{args.get('variant_label')}'" if args.get("variant_label") else ""
                quoted_part = f"AED {args.get('quoted_price')}" if args.get('quoted_price') is not None else "Quote"
                counter_part = f"AED {args.get('counter_price')}" if args.get('counter_price') is not None else "Counter"
                target_part = f" towards target AED {args.get('last_quote')}" if args.get("last_quote") is not None else ""
                title = f"Negotiation Counteroffer to {supp_name}"
                summary = f"Countered offer{variant_part} from {quoted_part} to {counter_part}{target_part}."
                status = exec_status
            elif tool == "request_clarification":
                event_type = "clarification_requested"
                title = f"Clarification Requested from {supp_name}"
                summary = f"Requested supplier clarification on ambiguous quote."
                status = exec_status
            elif tool == "flag_for_human_review":
                event_type = "human_review_created"
                title = f"Escalated to Human Review for {supp_name}"
                summary = f"Reason: {args.get('reason') or d.get('validation_reason') or 'Review required'}."
                status = "escalated"
            else:
                event_type = "agent_decision"
                title = f"Agent Action: {tool}"
                summary = f"Proposed {tool} ({val_status}, {exec_status})."
                status = exec_status

            timeline.append({
                "id": f"decision-{d['id']}",
                "timestamp": d.get("created_at"),
                "event_type": event_type,
                "title": title,
                "summary": summary,
                "supplier_name": supp_name,
                "status": status,
                "origin": orig,
                "details": {
                    "decision_id": d["id"],
                    "tool_name": tool,
                    "arguments": args,
                    "validation_status": val_status,
                    "validation_reason": d.get("validation_reason"),
                    "execution_status": exec_status,
                    "execution_error": d.get("execution_error"),
                    "inbound_message_id": d.get("inbound_message_id"),
                    "outbound_message_id": d.get("outbound_message_id"),
                    "flag_id": d.get("flag_id"),
                }
            })
    except Exception as e:
        logger.warning("Error fetching agent decisions for activity: %s", e)

    # 5. Operator Interventions (from flagged_for_review)
    try:
        flags = (
            supabase.table("flagged_for_review")
            .select("*, suppliers(name, phone_number)")
            .eq("client_id", client_id)
            .eq("rfq_id", rfq_id)
            .eq("status", "resolved")
            .order("created_at", desc=False)
            .execute()
            .data or []
        )
        for flg in flags:
            if flg.get("human_response"):
                supp_name = flg.get("suppliers", {}).get("name") if isinstance(flg.get("suppliers"), dict) else "Supplier"
                timeline.append({
                    "id": f"operator-flag-{flg['id']}",
                    "timestamp": flg.get("resolved_at") or flg.get("created_at"),
                    "event_type": "operator_response",
                    "title": f"Operator Instruction for {supp_name}",
                    "summary": f"Operator Response: \"{flg['human_response']}\"",
                    "supplier_name": supp_name,
                    "status": "resolved",
                    "origin": "operator",
                    "details": {
                        "flag_id": flg["id"],
                        "category": flg.get("category"),
                        "reason": flg.get("reason"),
                        "human_response": flg.get("human_response"),
                    }
                })
    except Exception as e:
        logger.warning("Error fetching flags for activity: %s", e)

    # 6. AI Ranking & Finalization
    try:
        rankings = (
            supabase.table("rfq_rankings")
            .select("*, suppliers(name, phone_number)")
            .eq("rfq_id", rfq_id)
            .order("created_at", desc=False)
            .execute()
            .data or []
        )
        if rankings:
            rk = rankings[0]
            best_supp = rk.get("suppliers", {}).get("name") if isinstance(rk.get("suppliers"), dict) else "Selected Supplier"
            timeline.append({
                "id": f"ranking-{rk['id']}",
                "timestamp": rk.get("created_at"),
                "event_type": "ranking_generated",
                "title": "AI Quote Ranking Generated",
                "summary": f"Evaluated supplier quotes. Selected best supplier: {best_supp}.",
                "supplier_name": best_supp,
                "status": "completed",
                "origin": "system",
                "details": {
                    "ranking_id": rk["id"],
                    "best_supplier_id": rk.get("best_supplier_id"),
                    "best_quote_id": rk.get("best_quote_id"),
                    "ranking_json": rk.get("ranking_json"),
                }
            })
    except Exception as e:
        logger.warning("Error fetching rankings for activity: %s", e)

    if rfq.get("finalization_status") == "completed" and rfq.get("finalized_at"):
        timeline.append({
            "id": f"rfq-finalized-{rfq_id}",
            "timestamp": rfq.get("finalized_at"),
            "event_type": "rfq_finalized",
            "title": "RFQ Finalization Completed",
            "summary": "RFQ closed and finalization workflow completed.",
            "status": "completed",
            "origin": "system",
            "details": {
                "finalization_status": "completed",
                "finalized_at": rfq.get("finalized_at"),
            }
        })

    # Sort chronological by timestamp
    timeline.sort(key=lambda x: str(x.get("timestamp") or ""))
    return timeline



def log_llm_usage(
    *,
    provider: str,
    model: str,
    call_type: str,
    environment: str,
    execution_context: str,
    test_name: Optional[str],
    success: bool,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    latency_ms: int = 0,
    request_id: Optional[str] = None,
    client_id: Optional[str] = None,
    supplier_id: Optional[str] = None,
    rfq_id: Optional[str] = None,
    error_type: Optional[str] = None,
):
    """Persist metadata for one LLM API call. No prompts, outputs, or credentials are stored."""
    payload = {
        "provider": provider,
        "model": model,
        "call_type": call_type,
        "environment": environment,
        "execution_context": execution_context,
        "test_name": test_name,
        "success": bool(success),
        "input_tokens": max(0, int(input_tokens or 0)),
        "output_tokens": max(0, int(output_tokens or 0)),
        "total_tokens": max(0, int(total_tokens or 0)),
        "latency_ms": max(0, int(latency_ms or 0)),
        "request_id": request_id,
        "client_id": client_id,
        "supplier_id": supplier_id,
        "rfq_id": rfq_id,
        "error_type": error_type,
    }
    return supabase.table("llm_usage_log").insert(payload).execute().data
