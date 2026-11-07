"""Durable operator instructions stored on their existing tenant-scoped review item.

HTTP acceptance, agent completion and supplier delivery are different facts.
Timeouts never release a flag for blind resending. The wrapper deduplicates job IDs.
"""
from datetime import datetime, timezone
from uuid import uuid5, NAMESPACE_URL
import requests
import db


def job_id(client_id, flag_id, instruction):
    return str(uuid5(NAMESPACE_URL, f'operator:{client_id}:{flag_id}:{instruction.strip()}'))


def save_state(flag, job):
    metadata = {**(flag.get('metadata') or {}), 'operator_instruction': job}
    result = (db.supabase.table('flagged_for_review').update({'metadata': metadata})
              .eq('id', flag['id']).eq('client_id', flag['client_id']).eq('status', 'processing').execute())
    return bool(result.data)


def enqueue(flag, instruction):
    job = {'id': job_id(flag['client_id'], flag['id'], instruction), 'state': 'queued',
           'instruction': instruction.strip(), 'created_at': datetime.now(timezone.utc).isoformat()}
    metadata = {**(flag.get('metadata') or {}), 'operator_instruction': job}
    result = (db.supabase.table('flagged_for_review').update({'status': 'processing', 'metadata': metadata})
              .eq('id', flag['id']).eq('client_id', flag['client_id']).eq('status', 'pending').execute())
    return job if result.data else None


def sync_jobs(base_url, api_key):
    if not base_url or not api_key:
        return
    flags = (db.supabase.table('flagged_for_review').select('*').eq('status', 'processing')
             .in_('metadata->operator_instruction->>state', ['queued','running','completed'])
             .order('created_at').limit(100).execute()).data or []
    headers = {'X-OpenClaw-Key': api_key}
    for flag in flags:
        job = dict((flag.get('metadata') or {}).get('operator_instruction') or {})
        if not job or job.get('state') in {'failed', 'unknown'}:
            continue
        try:
            if job.get('state') != 'completed':
                if job.get('state') == 'queued':
                    supplier = db.get_supplier_by_id(flag['supplier_id'])
                    if not supplier or supplier.get('client_id') != flag['client_id'] or not supplier.get('is_active') or supplier.get('deleted_at'):
                        job.update(state='failed', error='Supplier is unavailable for automated instructions')
                        save_state(flag, job)
                        continue
                    response = requests.post(base_url.rstrip('/')+'/internal/procurement/operator-instruction', headers=headers,
                        json={'job_id': job['id'], 'phone_number': supplier['phone_number'], 'instruction': job['instruction'],
                              'flag_id': flag['id'], 'rfq_id': flag.get('rfq_id'), 'reason': flag.get('reason')}, timeout=15)
                else:
                    response = requests.get(base_url.rstrip('/')+'/internal/procurement/operator-instruction/'+job['id'], headers=headers, timeout=15)
                if response.status_code in {400, 401, 403, 404, 409, 422}:
                    uncertain = response.status_code in {404, 409}
                    job.update(state='unknown' if uncertain else 'failed', error=(
                        'Instruction receipt is missing or conflicts; check the conversation before taking further action.'
                        if uncertain else 'Instruction was not accepted. Check configuration and supplier access.'))
                    save_state(flag, job)
                    continue
                response.raise_for_status()
                receipt = response.json()
                if receipt.get('job_id') != job['id'] or receipt.get('status') not in {'queued','running','completed','failed','unknown'}:
                    raise RuntimeError('Invalid instruction receipt')
                job['state'] = receipt['status']
                job.pop('transport_error', None)
                if receipt.get('error'): job['error'] = receipt['error']
                if not save_state(flag, job):
                    continue
            if job.get('state') == 'completed':
                db.complete_flag_operator_action(flag['id'], flag['client_id'], job['instruction'])
        except Exception:
            # The job may already be accepted: keep its stable ID and reconcile.
            job['transport_error'] = 'Waiting for a confirmed instruction status; do not resend.'
            save_state(flag, job)
