begin;
do $$
declare c uuid:=gen_random_uuid();s uuid:=gen_random_uuid();r uuid:=gen_random_uuid();a uuid:=gen_random_uuid();v uuid;
begin
 insert into public.clients(id,name,whatsapp_instance) values(c,'ROLLBACK clarification',c::text);
 insert into public.suppliers(id,client_id,name,phone_number,is_active) values(s,c,'ROLLBACK supplier',s::text,true);
 insert into public.rfqs(id,client_id,product_name,status,due_by) values(r,c,'ROLLBACK product','active',now()+interval '1 day');
 insert into public.rfq_suppliers(rfq_id,supplier_id,status) values(r,s,'sent');
 insert into public.commercial_action_intents(id,client_id,supplier_id,action_type,proposal,guard_snapshot,outcome,approved_message)
 values(a,c,s,'clarify_rfq','{}',jsonb_build_object('candidate_rfq_ids',jsonb_build_array(r::text)),'APPROVE','Could you please confirm which product your message refers to?');
 v=public.claim_rfq_clarification(c,s,'Confirmed purchase');
 if v is not null then raise exception 'Arbitrary message authorized'; end if;
 v=public.claim_rfq_clarification(gen_random_uuid(),s,'Could you please confirm which product your message refers to?');
 if v is not null then raise exception 'Wrong tenant authorized'; end if;
 v=public.claim_rfq_clarification(c,s,'Could you please confirm which product your message refers to?');
 if v is distinct from a then raise exception 'Clarification not claimed'; end if;
 v=public.claim_rfq_clarification(c,s,'Could you please confirm which product your message refers to?');
 if v is not null then raise exception 'Replay authorized'; end if;
 if exists(select 1 from public.quotes where rfq_id=r) or exists(select 1 from public.negotiation_sessions where rfq_id=r) then raise exception 'Clarification changed deal'; end if;
end $$;
rollback;
select 'PASS: clarification exact wording, tenant binding, single-use and no deal mutation; fixtures rolled back' as verification;
