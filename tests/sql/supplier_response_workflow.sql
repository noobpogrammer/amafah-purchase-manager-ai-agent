begin;
do $$
declare c uuid:=gen_random_uuid();s uuid:=gen_random_uuid(); r1 uuid:=gen_random_uuid();r2 uuid:=gen_random_uuid();r3 uuid:=gen_random_uuid();m uuid:=gen_random_uuid();q uuid;review jsonb;result jsonb;items jsonb;
begin
 insert into public.clients(id,name,whatsapp_instance) values(c,'ROLLBACK supplier response',c::text);
 insert into public.suppliers(id,client_id,name,phone_number,is_active) values(s,c,'ROLLBACK supplier',s::text,true);
 insert into public.rfqs(id,client_id,product_name,quantity,status,due_by,acceptable_price_min,acceptable_price_max)
 values(r1,c,'panel',20,'active',now()+interval '1 day',12,15),(r2,c,'flood',10,'active',now()+interval '1 day',25,30),(r3,c,'fan',5,'active',now()+interval '1 day',75,90);
 insert into public.rfq_suppliers(rfq_id,supplier_id,status) values(r1,s,'sent'),(r2,s,'sent'),(r3,s,'sent');
 insert into public.message_log(id,client_id,supplier_id,direction,body) values(m,c,s,'inbound','1 unavailable; 2 AED26 after4days; 3 AED105 2pieces');
 items=jsonb_build_array(jsonb_build_object('candidate_rfq_id',r1,'is_available',false,'confidence',1),jsonb_build_object('candidate_rfq_id',r2,'price',26,'delivery_time','after 4 days','confidence',1),jsonb_build_object('candidate_rfq_id',r3,'price',105,'quantity',2,'confidence',1));
 result=public.record_multi_rfqs_atomic(c,s,m,'original saved reply',items);
 if (result->>'inserted')::integer<>3 then raise exception 'Mixed response not fully recorded';end if;
 if not exists(select 1 from public.quotes where rfq_id=r1 and supplier_id=s and is_available=false and price is null) then raise exception 'Availability lost';end if;
 if not exists(select 1 from public.quotes where rfq_id=r2 and supplier_id=s and quantity is null and delivery_time='after 4 days') then raise exception 'Unknown terms invented';end if;
 select id into q from public.quotes where rfq_id=r3 and supplier_id=s;
 if public.buyer_partial_quantity_authorized(c,s,r3,q,2) then raise exception 'Supplier text granted buyer quantity authority';end if;
 result=public.record_multi_rfqs_atomic(c,s,m,'original saved reply',items);
 if (result->>'inserted')::integer<>0 or (result->>'duplicates')::integer<>3 then raise exception 'Retry duplicated quotes';end if;
 review=public.queue_buyer_final_offer(c,s,r3,q,jsonb_build_object('id',q,'price',105,'quantity',2,'resolved_delivery_days',null));
 update public.buyer_final_offer_reviews set review_kind='quantity',status='authorized',decision='partial',partial_quantity_approved=true where id=(review->>'id')::uuid;
 if not public.buyer_partial_quantity_authorized(c,s,r3,q,2) then raise exception 'Exact buyer quantity permission lost';end if;
 if public.buyer_partial_quantity_authorized(c,s,r3,q,5) or public.buyer_partial_quantity_authorized(gen_random_uuid(),s,r3,q,2) then raise exception 'Quantity permission escaped scope';end if;
 update public.buyer_final_offer_reviews set decision='decline',partial_quantity_approved=false where id=(review->>'id')::uuid;
 if public.buyer_partial_quantity_authorized(c,s,r3,q,2) then raise exception 'Decline retained quantity authority';end if;
 insert into public.commercial_action_intents(client_id,supplier_id,rfq_id,quote_id,action_type,proposal,guard_snapshot,outcome,approved_message)
 values(c,s,r3,q,'counter','{"price":75,"quantity":5,"delivery_days":4}',jsonb_build_object('current_offer',jsonb_build_object('id',q,'price',105,'quantity',2,'term_sources',jsonb_build_object('price',jsonb_build_object('quote_id',q,'value',105),'quantity',jsonb_build_object('quote_id',q,'value',2)))),'APPROVE','Declined quote must not counter');
 if exists(select 1 from public.claim_commercial_message_approval(c,s,'Declined quote must not counter')) then raise exception 'Declined offer still negotiated';end if;
 select id into q from public.quotes where rfq_id=r2 and supplier_id=s;
 insert into public.commercial_action_intents(client_id,supplier_id,rfq_id,quote_id,action_type,proposal,guard_snapshot,outcome,approved_message)
 values(c,s,r2,q,'counter','{"price":25}',jsonb_build_object('current_offer',jsonb_build_object('id',q,'price',26,'term_sources',jsonb_build_object('price',jsonb_build_object('quote_id',q,'value',26)))),'APPROVE','Price-only negotiation allowed');
 if (select count(*) from public.claim_commercial_message_approval(c,s,'Price-only negotiation allowed'))<>1 then raise exception 'Unknown delivery blocked price-only negotiation';end if;
 if (select count(*) from public.quotes where supplier_id=s)<>3 then raise exception 'Unexpected quote writes';end if;
end $$;
rollback;
