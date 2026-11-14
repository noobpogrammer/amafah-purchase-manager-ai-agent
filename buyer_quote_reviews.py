"""Buyer final-offer choices, separate from Agent Attention and purchase authority."""
import requests
import db


def snapshot(offer):
    return {k:offer.get(k) for k in ('id','price','quantity','resolved_delivery_days','quality_notes','variant_label','term_sources')}


def queue(client_id,supplier_id,rfq_id,offer):
    return db.supabase.rpc('queue_buyer_final_offer',{'p_client_id':client_id,'p_supplier_id':supplier_id,'p_rfq_id':rfq_id,'p_quote_id':offer['id'],'p_snapshot':snapshot(offer)}).execute().data


def list_reviews(client_id,rfq_id):
    return db.supabase.table('buyer_final_offer_reviews').select('*').eq('client_id',client_id).eq('rfq_id',rfq_id).order('created_at',desc=True).execute().data or []


def has_review(client_id,supplier_id,quote_id):
    return bool(db.supabase.table('buyer_final_offer_reviews').select('id').eq('client_id',client_id).eq('supplier_id',supplier_id).eq('quote_id',quote_id).limit(1).execute().data)


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
                task=(f"Buyer approved acceptance of exact quote {row['quote_id']} at AED {offer['price']}. " if row['decision']=='accept' else f"Buyer requests one more negotiation. Ask for AED {row['counter_price']} per unit. Do not accept the existing final offer. " if row['counter_price'] is not None else 'Buyer requests another negotiation toward the preferred target. Do not accept the existing final offer. ')
                task+=f"Buyer review ID {row['id']}. RFQ {row['rfq_id']}. Recheck the saved offer, use commercial review with supplier_final=true, then normal acceptance/send tools. This instruction does not override spending limits or terms."
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
