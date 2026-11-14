begin;
do $$
declare c uuid:=gen_random_uuid();s uuid:=gen_random_uuid();r uuid:=gen_random_uuid();r2 uuid:=gen_random_uuid();m uuid:=gen_random_uuid();m2 uuid:=gen_random_uuid();answer uuid:=gen_random_uuid();q uuid:=gen_random_uuid();a uuid:=gen_random_uuid();d uuid;v uuid;x jsonb;y jsonb;n integer;
begin
 insert into public.clients(id,name,whatsapp_instance) values(c,'ROLLBACK clarification',c::text);
 insert into public.suppliers(id,client_id,name,phone_number,is_active) values(s,c,'ROLLBACK supplier',s::text,true);
 insert into public.rfqs(id,client_id,product_name,status,due_by) values(r,c,'ROLLBACK product','active',now()+interval '1 day'),(r2,c,'ROLLBACK other','active',now()+interval '1 day');
 insert into public.rfq_suppliers(rfq_id,supplier_id,status) values(r,s,'sent'),(r2,s,'sent');
 insert into public.message_log(id,client_id,supplier_id,direction,body) values(m,c,s,'inbound','95 final'),(m2,c,s,'inbound','?'),(answer,c,s,'inbound','The first product');
 x=public.prepare_rfq_clarification(c,s,m,array[r,r2],'{"price":95}',true,'کیا آپ بیس انچ یا دس انچ والے ہتھوڑے کی بات کر رہے ہیں؟');d=(x->'draft'->>'id')::uuid;
 y=public.prepare_rfq_clarification(c,s,m,array[r,r2],'{"price":999}',true,'Changed question must not replace saved wording');
 if x->'draft'->>'id'<>y->'draft'->>'id' or y->'draft'->'extracted_facts'->>'price'<>'95' then raise exception 'Retry overwrote or duplicated draft'; end if;
 select count(*) into n from public.commercial_action_intents where client_id=c and action_type='clarify_rfq';
 if n<>1 then raise exception 'Duplicate approval'; end if;
 v=public.claim_rfq_clarification(c,s,'Confirmed purchase'); if v is not null then raise exception 'Arbitrary text authorized'; end if;
 v=public.claim_rfq_clarification(gen_random_uuid(),s,'کیا آپ بیس انچ یا دس انچ والے ہتھوڑے کی بات کر رہے ہیں؟'); if v is not null then raise exception 'Wrong tenant authorized'; end if;
 v=public.claim_rfq_clarification(c,s,'کیا آپ بیس انچ یا دس انچ والے ہتھوڑے کی بات کر رہے ہیں؟'); if v is null then raise exception 'Question rejected'; end if;
 v=public.claim_rfq_clarification(c,s,'کیا آپ بیس انچ یا دس انچ والے ہتھوڑے کی بات کر رہے ہیں؟'); if v is not null then raise exception 'Replay authorized'; end if;
 y=public.prepare_rfq_clarification(c,s,m2,array[r,r2],'{}',true,'کیا آپ بیس انچ یا دس انچ والے ہتھوڑے کی بات کر رہے ہیں؟');if y->>'outcome'<>'ALREADY_PENDING' then raise exception 'Second question not suppressed';end if;
 y=public.resolve_rfq_clarification(c,s,d,r,m);if not(y->>'conflict')::boolean then raise exception 'Original message used as answer';end if;
 y=public.resolve_rfq_clarification(c,s,d,r,answer);if not(y->>'resolved')::boolean then raise exception 'Answer not resolved';end if;
 y=public.resolve_rfq_clarification(c,s,d,r,answer);if not(y->>'already_resolved')::boolean then raise exception 'Same resolution not idempotent';end if;
 y=public.resolve_rfq_clarification(c,s,d,r2,answer);if not(y->>'conflict')::boolean then raise exception 'Conflicting answer overwrote draft';end if;
 y=public.prepare_rfq_clarification(c,s,m,array[r,r2],'{}',true,'کیا آپ بیس انچ یا دس انچ والے ہتھوڑے کی بات کر رہے ہیں؟');if y->>'outcome'<>'ALREADY_HANDLED' then raise exception 'Resolved draft reopened';end if;
 if exists(select 1 from public.quotes where rfq_id=r) or exists(select 1 from public.negotiation_sessions where rfq_id=r) then raise exception 'Clarification changed deal'; end if;
 -- An accepted deal remains acknowledgeable even when closed and past deadline.
 insert into public.quotes(id,rfq_id,supplier_id,price,is_available,is_accepted,is_current_accepted) values(q,r,s,95,true,true,true);
 update public.rfqs set status='closed',due_by=now()-interval '1 day' where id=r;
 insert into public.commercial_action_intents(id,client_id,supplier_id,rfq_id,quote_id,action_type,proposal,guard_snapshot,outcome,approved_message)
 values(a,c,s,r,q,'acknowledge','{}',jsonb_build_object('current_offer',jsonb_build_object('id',q,'price',95)),'APPROVE','Your quotation is already accepted');
 select count(*) into n from public.claim_commercial_message_approval(c,s,'Your quotation is already accepted');if n<>1 then raise exception 'Historical acknowledgement blocked';end if;
end $$;
rollback;
select 'PASS: durable waiting, duplicate suppression, source binding, conflicting resolution, replay and historical acknowledgement; fixtures rolled back' as verification;
