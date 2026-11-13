"""Shared buyer authority and resolved offers. No LLM calls or database writes."""
import math
import re


def positive(value):
    try:
        number = float(value)
        return number if not isinstance(value, bool) and math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None


def price_policy(rfq):
    target, cap, last = (positive(rfq.get(key)) for key in
                         ('acceptable_price_min', 'acceptable_price_max', 'last_quote'))
    last_only = target is None and cap is None and last is not None
    return {
        'preferred_target': target if target is not None else (min(last, cap) if last is not None and cap is not None else (last if last is not None else cap)),
        'acceptable_max': cap if cap is not None else (round(last + 5, 2) if last_only else None),
        'authority_source': 'last_quote_plus_5' if last_only else 'explicit_limits',
        'last_quote': last,
        'anomaly_ratio_threshold': 3,
    }


def price_anomaly(rfq, offer):
    """A signal to verify units/specs, not proof that a supplier is wrong."""
    last, price = positive(rfq.get('last_quote')), positive(offer.get('price'))
    return bool(last and price and price >= last * 3)


def delivery_days(value):
    """Only unambiguous exact durations; ranges/conditions remain unresolved."""
    text = str(value or '').strip().lower()
    if re.fullmatch(r'(?:within\s+)?\d+\s*(?:working\s+|business\s+)?days?', text):
        return int(re.search(r'\d+', text)[0])
    if text in ('next day', 'next day delivery', 'next-day delivery', 'tomorrow'):
        return 1
    if text in ('same day', 'same-day delivery', 'today', 'immediate'):
        return 0
    return None


def resolve_offers(rows):
    """Newest-first history, scoped by RFQ/supplier/variant. Never use RFQ wishes as agreement."""
    rows = sorted(rows, key=lambda q: (q.get('created_at') or '', str(q.get('id') or '')), reverse=True)
    labels = {}
    for q in rows:
        scope = (q.get('rfq_id'), q.get('supplier_id'))
        if q.get('variant_label'):
            labels.setdefault(scope, set()).add(q['variant_label'].strip().casefold())
    grouped = {}
    for q in rows:
        scope = (q.get('rfq_id'), q.get('supplier_id'))
        key = (q.get('variant_label') or '').strip().casefold()
        # A sole known variant is unambiguous; multiple variants need the caller to clarify.
        if not key and len(labels.get(scope, set())) == 1:
            key = next(iter(labels[scope]))
        grouped.setdefault((*scope, key), []).append(q)
    resolved = []
    for history in grouped.values():
        offer = dict(history[0])
        sources = {}
        for field in ('price', 'quantity', 'delivery_time', 'quality_notes', 'variant_label'):
            # New price/availability always wins, including a withdrawn offer.
            candidates = history[:1] if field == 'price' else history
            for row in candidates:
                if row is not history[0] and row.get('is_available') is False:
                    break  # Do not resurrect withdrawn terms.
                value = row.get(field)
                if value is not None and value != '':
                    offer[field] = value
                    sources[field] = {'quote_id': row['id'], 'source_message_id': row.get('source_message_id'), 'value': value}
                    if row['id'] != offer['id']:
                        offer[field.replace('_time', '') + '_inherited'] = True
                    break
        offer['term_sources'] = sources
        offer['resolved_delivery_days'] = delivery_days(offer.get('delivery_time'))
        offer['revision_count'] = len(history)
        resolved.append(offer)
    return sorted(resolved, key=lambda q: (q.get('created_at') or '', str(q.get('id') or '')), reverse=True)


def validate_terms(rfq, offer, proposal):
    """Validate supplier-established acceptance terms, with explicit buyer flexibility."""
    if offer.get('quantity') is None or offer.get('resolved_delivery_days') is None:
        return 'Unknown supplier quantity or delivery; clarify only the missing term.'
    if proposal.get('quantity') != offer['quantity'] or proposal.get('delivery_days') != offer['resolved_delivery_days']:
        return 'Acceptance terms differ from the current supplier offer.'
    qty = offer['quantity']
    if rfq.get('quantity') is not None and qty != rfq['quantity']:
        if (not rfq.get('quantity_flexible') or (rfq.get('quantity_max') is not None and qty > rfq['quantity_max'])
                or (rfq.get('quantity_min') is not None and qty < rfq['quantity_min'])):
            return 'Quantity is outside buyer authority.'
    days = offer['resolved_delivery_days']
    if rfq.get('required_delivery_days') is not None and days > rfq['required_delivery_days']:
        if not rfq.get('delivery_flexible') or (rfq.get('max_delivery_days') is not None and days > rfq['max_delivery_days']):
            return 'Delivery is outside buyer authority.'
    return None


def apply_constraints(rfq, constraints):
    """Latest explicit operator authority overrides baseline terms, never supplier text."""
    result = dict(rfq)
    for item in constraints or []:
        if item.get('status') != 'authorized':
            continue
        values = item.get('constraints') or {}
        if item.get('dimension') == 'quantity':
            result.update(quantity_flexible=True, quantity_min=values.get('min'), quantity_max=values.get('max'))
        elif item.get('dimension') == 'delivery':
            result.update(delivery_flexible=True, max_delivery_days=values.get('max_days'))
        elif item.get('dimension') == 'price':
            if positive(values.get('max')):
                result['acceptable_price_max'] = values['max']
            if positive(values.get('preferred_target')):
                result['acceptable_price_min'] = values['preferred_target']
    return result
