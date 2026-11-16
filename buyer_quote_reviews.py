"""Buyer final-offer choices, separate from Agent Attention and purchase authority."""
import requests
import db


def snapshot(offer):
    return {k:offer.get(k) for k in ('id','price','quantity','resolved_delivery_days','quality_notes','variant_label','term_sources')}


def queue(client_id,supplier_id,rfq_id,offer,kind="final_price"):
    result = db.supabase.rpc('queue_buyer_final_offer',{'p_client_id':client_id,'p_supplier_id':supplier_id,'p_rfq_id':rfq_id,'p_quote_id':offer['id'],'p_snapshot':snapshot(offer)}).execute().data
    if kind == 'quantity':
        db.supabase.table('buyer_final_offer_reviews').update({'review_kind':'quantity'}).eq('id', result['id']).eq('client_id', client_id).execute()
        result['review_kind'] = kind
    if kind == 'final_price' and result.get('review_kind') == 'quantity' and result.get('decision') == 'partial' and result.get('job_state') not in ('queued','running'):
        updated = db.supabase.table('buyer_final_offer_reviews').update({'status':'pending','decision':None,'review_kind':'final_price','job_state':None,'job_id':__import__('uuid').uuid4().hex}).eq('id',result['id']).eq('client_id',client_id).eq('status','authorized').execute().data
        if updated: result = updated[0]
    return result


def quantity_issue(rfq, offer):
    qty, requested = offer.get('quantity'), rfq.get('quantity')
    if not offer.get('is_available', True) or offer.get('is_current_accepted') or qty is None or requested is None or qty == requested:
        return False
    return (not rfq.get('quantity_flexible') or
            (rfq.get('quantity_min') is not None and qty < rfq['quantity_min']) or
            (rfq.get('quantity_max') is not None and qty > rfq['quantity_max']))


def partial_quantity_authorized(client_id, supplier_id, rfq_id, offer):
    if not offer.get('id') or offer.get('quantity') is None:
        return False
    return bool(db.supabase.rpc('buyer_partial_quantity_authorized', {
        'p_client_id':client_id, 'p_supplier_id':supplier_id, 'p_rfq_id':rfq_id,
        'p_quote_id':offer['id'], 'p_quantity':offer['quantity']}).execute().data)


def ensure_quantity_reviews(client_id, rfq_id, supplier_id=None):
    from commercial_policy import apply_constraints
    rfq = db.get_rfq_by_id(rfq_id, client_id=client_id)
    if not rfq or rfq.get('client_id') != client_id or rfq.get('status') != 'active':
        return []
    rfq = apply_constraints(rfq, db.get_rfq_negotiation_constraints(client_id, rfq_id))
    result = []
    existing_reviews = list_reviews(client_id, rfq_id)
    for offer in db.get_quotes_for_rfq(rfq_id):
        if supplier_id and offer['supplier_id'] != supplier_id:
            continue
        existing = next((r for r in existing_reviews if r['quote_id'] == offer['id']), None)
        if existing and existing.get('partial_quantity_approved'):
            continue
        if quantity_issue(rfq, offer):
            result.append(queue(client_id, offer['supplier_id'], rfq_id, offer, kind='quantity'))
    return result


def list_reviews(client_id,rfq_id):
    return db.supabase.table('buyer_final_offer_reviews').select('*').eq('client_id',client_id).eq('rfq_id',rfq_id).order('created_at',desc=True).execute().data or []


def has_review(client_id,supplier_id,quote_id):
    return bool(db.supabase.table('buyer_final_offer_reviews').select('id').eq('client_id',client_id).eq('supplier_id',supplier_id).eq('quote_id',quote_id).eq('review_kind','final_price').limit(1).execute().data)


def authorized_accept(client_id,supplier_id,offer):
    rows=db.supabase.table('buyer_final_offer_reviews').select('*').eq('client_id',client_id).eq('supplier_id',supplier_id).eq('quote_id',offer['id']).eq('status','authorized').eq('decision','accept').limit(1).execute().data or []
    return bool(rows and rows[0]['offer_snapshot']==snapshot(offer))


def sync_jobs(base_url,key):
    if not base_url or not key:return
    rows=(db.supabase.table('buyer_final_offer_reviews').select('*').in_('status',['authorized','completed']).in_('job_state',['queued','running']).limit(100).execute().data or [])
    for row in rows:
        update=db.supabase.table('buyer_final_offer_reviews')
        try:
            supplier=db.get_supplier_by_id(row['supplier_id'],client_id=row['client_id'])
            offers=db.get_quotes_for_rfq(row['rfq_id'])
            offer=next((o for o in offers if o['id']==row['quote_id'] and o['supplier_id']==row['supplier_id']),None)
            if not offer or snapshot(offer)!=row['offer_snapshot'] or not supplier or not supplier.get('is_active') or supplier.get('deleted_at'):
                update.update({'status':'stale','job_state':'failed','job_error':'Offer or supplier changed; review the current quote.'}).eq('id',row['id']).execute();continue
            if db.is_client_automation_paused(row['client_id']):continue
            headers={'X-OpenClaw-Key':key}
            if row['job_state']=='queued':
                task=(f"Buyer declines this exact quotation. Do not accept or counter it. Send one polite decline through normal reviewed messaging. " if row['decision']=='decline' else f"Buyer agrees to CONSIDER only {offer['quantity']} available pieces, not the full RFQ quantity. Negotiate toward the target within the price ceiling; clarify missing delivery. This is quantity permission only, not purchase acceptance. " if row['decision']=='partial' else f"Buyer approved acceptance of exact quote {row['quote_id']} at AED {offer['price']}. " if row['decision']=='accept' else f"Buyer requests one more negotiation. Ask for AED {row['counter_price']} per unit. Do not accept the existing final offer. " if row['counter_price'] is not None else 'Buyer requests another negotiation toward the preferred target. Do not accept the existing final offer. ')
                task+=f"Buyer review ID {row['id']}. RFQ {row['rfq_id']}. Recheck the saved offer, use commercial review with supplier_final=true, then normal acceptance/send tools. This instruction does not override spending limits or terms. For a price-only negotiation, omit unknown quantity/delivery_days from proposal and message_facts; do not invent delivery or include null. Considering a partial quantity still needs buyer permission."
                response=requests.post(base_url.rstrip('/')+'/internal/procurement/operator-instruction',headers=headers,json={'job_id':row['job_id'],'flag_id':row['id'],'rfq_id':row['rfq_id'],'phone_number':supplier['phone_number'],'reason':'Buyer decision from RFQ recorded quotes','instruction':task},timeout=15)
            else:response=requests.get(base_url.rstrip('/')+'/internal/procurement/operator-instruction/'+row['job_id'],headers=headers,timeout=15)
            if response.status_code in {404,409}:
                update.update({'job_state':'unknown','job_error':'Instruction outcome uncertain; check conversation before retry.'}).eq('id',row['id']).execute();continue
            response.raise_for_status();receipt=response.json()
            if receipt.get('job_id')!=row['job_id'] or receipt.get('status') not in {'queued','running','completed','failed','unknown'}:raise RuntimeError('Invalid receipt')
            update.update({'job_state':receipt['status'],'job_error':receipt.get('error')}).eq('id',row['id']).execute()
        except Exception:
            # Retry only the same durable job ID; remote worker deduplicates execution.
            update.update({'job_error':'Waiting for a confirmed instruction status; do not resend.'}).eq('id',row['id']).execute()
