"""Pure planning and validation for bundled procurement messages.

No persistence or WhatsApp side effects belong in this module. Callers must
record a durable bundle and all of its RFQ item links before delivery.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class BundleItem:
    rfq_id: str
    product_name: str
    quantity: int | None = None
    specs: str | None = None
    deadline_hours: int | None = None
    required_delivery_days: int | None = None


@dataclass(frozen=True)
class ResolvedQuote:
    rfq_id: str
    price: Decimal
    quantity: int | None = None
    delivery_time: str | None = None
    variant_label: str | None = None


class AmbiguousSupplierQuote(ValueError):
    """Reject ambiguous mapping rather than attaching a price to a wrong RFQ."""


def plan_supplier_bundles(items: Sequence[BundleItem], max_items: int = 8) -> list[list[BundleItem]]:
    """Stable, duplicate-free batches; safe for 1–8 items in any batch."""
    if not 1 <= max_items <= 8:
        raise ValueError("max_items must be between 1 and 8")
    seen: set[str] = set()
    unique: list[BundleItem] = []
    for item in items:
        if not item.rfq_id or not item.product_name.strip():
            raise ValueError("Each bundle item requires an RFQ ID and product name")
        if item.rfq_id in seen:
            continue
        seen.add(item.rfq_id)
        unique.append(item)
    return [unique[i:i + max_items] for i in range(0, len(unique), max_items)]


def format_supplier_bundle(items: Sequence[BundleItem], *, brand: str = "Everyware®️ (Al Noon Int’l Trading LLC)") -> str:
    if not items or len(items) > 8:
        raise ValueError("One supplier message must contain between 1 and 8 RFQs")
    lines = [f"Hi! This is {brand}.", "", "Please quote the following items:"]
    for index, item in enumerate(items, 1):
        if not item.rfq_id or not item.product_name.strip():
            raise ValueError("Missing RFQ reference or product name")
        row = f"{index}. {item.product_name} (Ref: {item.rfq_id[:8]}) | Qty: {item.quantity if item.quantity is not None else 'N/A'}"
        if item.specs and item.specs.strip() not in {".", "-", "N/A"}:
            row += f" | Specs: {item.specs.strip()}"
        if item.deadline_hours is not None:
            row += f" | Quote within: {item.deadline_hours}h"
        if item.required_delivery_days is not None:
            row += f" | Delivery: {item.required_delivery_days} day(s)"
        lines.append(row)
    lines += ["", "Please reply with each item number, final AED price per unit, and estimated delivery time.", "Thanks."]
    return "\n".join(lines)


def validate_multi_rfq_quotes(
    proposed: Sequence[Mapping[str, Any]],
    candidate_rfq_ids: Sequence[str],
    *,
    min_confidence: float = 0.70,
) -> list[ResolvedQuote]:
    """Validates explicit product/RFQ mappings. Never assign by list position alone.

    Each parsed quote must explicitly carry candidate_rfq_id and confidence.
    Duplicate RFQs can represent legitimate alternative variants.
    """
    allowed = set(candidate_rfq_ids)
    if not allowed or not proposed:
        raise AmbiguousSupplierQuote("No quote candidates or parsed items")
    resolved: list[ResolvedQuote] = []
    for item in proposed:
        rfq_id = str(item.get("candidate_rfq_id") or "").strip()
        if rfq_id not in allowed:
            raise AmbiguousSupplierQuote("Quoted item does not map to an authorized supplier RFQ")
        try:
            confidence = float(item.get("confidence") or 0)
            price = Decimal(str(item.get("price")))
        except (ValueError, TypeError, InvalidOperation):
            raise AmbiguousSupplierQuote("Quote requires a valid confidence and price") from None
        if not math.isfinite(confidence) or confidence < min_confidence or not price.is_finite() or price <= 0:
            raise AmbiguousSupplierQuote("Low-confidence, non-finite, or non-positive quote")
        qty = item.get("quantity")
        if qty is not None and (isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0):
            raise AmbiguousSupplierQuote("Invalid quoted quantity")
        resolved.append(ResolvedQuote(
            rfq_id=rfq_id,
            price=price,
            quantity=qty,
            delivery_time=item.get("delivery_time"),
            variant_label=item.get("variant_label"),
        ))
    return resolved
