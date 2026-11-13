-- Only completed sessions with exactly one current accepted quote can be repaired.
with accepted as (
 select q.rfq_id,q.supplier_id,min(q.id::text)::uuid as quote_id,min(q.price) as price
 from public.quotes q where q.is_current_accepted=true
 group by q.rfq_id,q.supplier_id having count(*)=1
)
update public.negotiation_sessions n set latest_supplier_offer=a.price,last_quote_id=a.quote_id
from accepted a join public.rfqs r on r.id=a.rfq_id
where n.rfq_id=a.rfq_id and n.supplier_id=a.supplier_id and n.client_id=r.client_id
and n.status='completed'
and (n.latest_supplier_offer is distinct from a.price or n.last_quote_id is distinct from a.quote_id);
