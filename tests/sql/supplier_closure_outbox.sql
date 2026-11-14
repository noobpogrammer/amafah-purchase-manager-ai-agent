begin;
do $$
declare c uuid:=gen_random_uuid();s uuid:=gen_random_uuid();r uuid:=gen_random_uuid();r2 uuid:=gen_random_uuid();r3 uuid:=gen_random_uuid();n integer;b boolean;
begin
 insert into public.clients(id,name,whatsapp_instance) values(c,'ROLLBACK closure batching',c::text);
 insert into public.suppliers(id,client_id,name,phone_number,is_active) values(s,c,'ROLLBACK supplier',s::text,true);
 insert into public.rfqs(id,client_id,product_name,status) values(r,c,'First','closed'),(r2,c,'Second','closed'),(r3,c,'Accepted','closed');
 insert into public.rfq_suppliers(rfq_id,supplier_id,status) values(r,s,'sent'),(r2,s,'sent'),(r3,s,'responded');
 insert into public.quotes(rfq_id,supplier_id,price,is_current_accepted,is_accepted) values(r3,s,9,true,true);
 b=public.queue_supplier_closure(c,s,r);if not b then raise exception 'Queue failed';end if;
 perform public.queue_supplier_closure(c,s,r);perform public.queue_supplier_closure(c,s,r2);perform public.queue_supplier_closure(c,s,r3);
 select count(*) into n from public.supplier_closure_outbox where client_id=c;if n<>2 then raise exception 'Duplicate or accepted notice queued';end if;
 select count(*) into n from public.claim_supplier_closures(c,s);if n<>0 then raise exception 'Collection window ignored';end if;
 update public.supplier_closure_outbox set available_at=now()-interval '1 second' where rfq_id=r;
 select count(*) into n from public.claim_supplier_closures(c,s);if n<>2 then raise exception 'Adjacent endings not grouped';end if;
 select count(distinct batch_id) into n from public.supplier_closure_outbox where client_id=c;if n<>1 then raise exception 'Different batch IDs';end if;
 select count(*) into n from public.claim_supplier_closures(c,s);if n<>0 then raise exception 'Claim replayed';end if;
 update public.supplier_closure_outbox set status='unknown' where client_id=c;
 select count(*) into n from public.claim_supplier_closures(c,s);if n<>0 then raise exception 'Uncertain send retried';end if;
 b=public.queue_supplier_closure(gen_random_uuid(),s,r);if b then raise exception 'Wrong tenant queued';end if;
end $$;
rollback;
select 'PASS: deduplication, collection window, adjacent batching, accepted suppression, tenant binding and no uncertain resend' as verification;
