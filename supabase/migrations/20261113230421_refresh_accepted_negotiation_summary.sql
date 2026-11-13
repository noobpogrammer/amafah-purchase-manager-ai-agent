-- Refresh the reporting summary in the same transaction as acceptance.
do $migration$
declare definition text; updated text;
begin
 select pg_get_functiondef('public.accept_resolved_quote(uuid,uuid,uuid,uuid,uuid,jsonb,text)'::regprocedure) into definition;
 updated := replace(definition, 'set status=''completed'',completed_at=now(),updated_at=now()',
   'set status=''completed'',latest_supplier_offer=q.price,last_quote_id=p_quote_id,completed_at=now(),updated_at=now()');
 if updated=definition then raise exception 'Expected acceptance completion update not found'; end if;
 execute updated;
end $migration$;
