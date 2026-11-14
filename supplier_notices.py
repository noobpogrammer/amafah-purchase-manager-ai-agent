"""Durable supplier-level scheduled notices. Never blindly retry uncertain sends."""
import logging
import db
logger = logging.getLogger(__name__)

def description(rfq):
    text = rfq.get('product_name') or 'Requested item'
    if rfq.get('specs'): text += f" ({rfq['specs']})"
    if rfq.get('quantity') is not None: text += f", {rfq['quantity']} units"
    return text

def closure_message(rfqs):
    lines = '\n'.join(f"• {description(r)}" for r in rfqs)
    return f"The quotation window has ended for the following items:\n{lines}\nWe are no longer requesting quotes for these items. Thank you!"

def queue_closure(client_id, supplier_id, rfq_id):
    return bool(db.supabase.rpc('queue_supplier_closure', {
        'p_client_id':client_id,'p_supplier_id':supplier_id,'p_rfq_id':rfq_id,
    }).execute().data)

async def flush_closures(send):
    pending = (db.supabase.table('supplier_closure_outbox').select('client_id,supplier_id')
               .eq('status','pending').order('available_at').limit(500).execute().data or [])
    for client_id,supplier_id in sorted({(r['client_id'],r['supplier_id']) for r in pending}):
        if db.is_client_automation_paused(client_id): continue
        supplier=db.get_supplier_by_id(supplier_id,client_id=client_id)
        if not supplier or not supplier.get('is_active') or supplier.get('deleted_at') or not supplier.get('phone_number'): continue
        rows=db.supabase.rpc('claim_supplier_closures',{'p_client_id':client_id,'p_supplier_id':supplier_id}).execute().data or []
        if not rows: continue
        eligible=[]
        try:
            for row in rows:
                rfq=db.get_rfq_by_id(row['rfq_id'],client_id=client_id)
                if not rfq or rfq.get('status')!='closed' or db.supplier_has_accepted_quote(row['rfq_id'],supplier_id):
                    db.supabase.table('supplier_closure_outbox').update({'status':'suppressed'}).eq('id',row['id']).execute()
                    continue
                eligible.append((row,rfq))
            if not eligible: continue
            batch_id=rows[0]['batch_id']
            text=closure_message([r for _,r in eligible])
            log_id=db.log_message(client_id,supplier_id,'outbound',text,
                                  related_rfq_id=eligible[0][1]['id'],event_key=f'closure_bundle:{batch_id}')
            if not log_id: raise RuntimeError('Could not durably log closure batch')
            for row,_ in eligible:
                db.supabase.table('supplier_closure_outbox').update({'message_log_id':log_id}).eq('id',row['id']).execute()
            sent=await send(supplier['phone_number'],text,rfq_id=eligible[0][1]['id'],supplier_id=supplier_id,message_log_id=log_id)
            for row,_ in eligible:
                db.supabase.table('supplier_closure_outbox').update({'status':'sent' if sent else 'unknown'}).eq('id',row['id']).execute()
        except Exception:
            logger.exception('Closure batch unconfirmed; no automatic retry')
            for row in rows:
                db.supabase.table('supplier_closure_outbox').update({'status':'unknown'}).eq('id',row['id']).eq('status','processing').execute()


def due_reminder_stage(percentage, completed_stage):
    """Skip missed stages after downtime instead of sending catch-up bursts."""
    if percentage < 50 or percentage >= 100:
        return None
    stage = 3 if percentage >= 90 else (2 if percentage >= 70 else 1)
    if completed_stage >= stage:
        return None
    return ({1:'50%',2:'70%',3:'90%'}[stage], stage)
