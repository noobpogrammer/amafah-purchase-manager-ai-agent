-- Run as a privileged integration test. All fixtures are invisible outside this transaction and rolled back.
begin;
do $$
declare c uuid:=gen_random_uuid(); s uuid:=gen_random_uuid(); r uuid:=gen_random_uuid();
 oldq uuid:=gen_random_uuid(); newq uuid:=gen_random_uuid(); a uuid:=gen_random_uuid(); offer jsonb; result jsonb; n integer;
begin
 insert into public.clients(id,name,whatsapp_instance) values(c,'ROLLBACK policy verification',c::text);
 insert into public.suppliers(id,client_id,name,phone_number,is_active) values(s,c,'ROLLBACK supplier',s::text,true);
 insert into public.rfqs(id,client_id,product_name,specs,quantity,last_quote,required_delivery_days,status,due_by)
 values(r,c,'ROLLBACK light','50W',10,12,2,'active',now()+interval '1 day');
 insert into public.rfq_suppliers(rfq_id,supplier_id,status) values(r,s,'sent');
 insert into public.negotiation_sessions(client_id,rfq_id,supplier_id,status) values(c,r,s,'active');
 insert into public.quotes(id,rfq_id,supplier_id,variant_label,price,quantity,delivery_time,quality_notes,is_available,created_at)
 values(oldq,r,s,'50W',12,10,'2 days','Brand A',true,now()-interval '1 minute'),
 (newq,r,s,'50W',11,null,null,null,true,now());
 offer=jsonb_build_object('id',newq,'rfq_id',r,'supplier_id',s,'variant_label','50W','price',11,'quantity',10,'delivery_time','2 days','resolved_delivery_days',2,
 'term_sources',jsonb_build_object('price',jsonb_build_object('quote_id',newq,'value',11),'quantity',jsonb_build_object('quote_id',oldq,'value',10),'delivery_time',jsonb_build_object('quote_id',oldq,'value','2 days')));
 insert into public.commercial_action_intents(id,client_id,supplier_id,rfq_id,quote_id,action_type,proposal,guard_snapshot,outcome,approved_message)
 values(a,c,s,r,newq,'accept','{"price":11,"quantity":10,"delivery_days":2}',jsonb_build_object('current_offer',offer),'APPROVE','Confirmed verification offer');
 select count(*) into n from public.claim_commercial_message_approval(c,s,'Confirmed verification offer');
 if n<>0 then raise exception 'Confirmation allowed before acceptance'; end if;
 update public.quotes set quantity=100 where id=oldq;
 result=public.accept_resolved_quote(a,c,s,r,newq,offer,'verification');
 if result is not null then raise exception 'Changed evidence was accepted'; end if;
 if exists(select 1 from public.commercial_action_intents where id=a and acceptance_consumed_at is not null) then raise exception 'Failed acceptance consumed approval'; end if;
 update public.quotes set quantity=10 where id=oldq;
 result=public.accept_resolved_quote(a,c,s,r,newq,offer,'verification');
 if result is null then raise exception 'Inherited offer rejected'; end if;
 if not exists(select 1 from public.negotiation_sessions where rfq_id=r and status='completed') then raise exception 'Session not completed atomically'; end if;
 if not exists(select 1 from public.commercial_action_intents where id=a and acceptance_consumed_at is not null and consumed_at is null) then raise exception 'Acceptance consumed send approval'; end if;
 select count(*) into n from public.claim_commercial_message_approval(c,s,'Confirmed verification offer EXTRA UNAPPROVED TERMS');
 if n<>0 then raise exception 'Tampered message authorized'; end if;
 select count(*) into n from public.claim_commercial_message_approval(c,s,'Confirmed verification offer');
 if n<>1 then raise exception 'Accepted confirmation could not send'; end if;
 select count(*) into n from public.claim_commercial_message_approval(c,s,'Confirmed verification offer');
 if n<>0 then raise exception 'Duplicate confirmation authorized'; end if;
 result=public.accept_resolved_quote(a,c,s,r,newq,offer,'verification');
 if result is not null then raise exception 'Duplicate acceptance authorized'; end if;
 -- Explicitly authorized delivery/quantity flexibility must work with this schema.
 update public.rfqs set quantity=5,required_delivery_days=1,due_by=now()-interval '1 hour' where id=r;
 update public.negotiation_sessions set status='awaiting_authorization' where rfq_id=r;
 insert into public.rfq_negotiation_constraints(client_id,rfq_id,dimension,status,constraints,source)
 values(c,r,'quantity','authorized','{"min":5,"max":15}','operator_review'),(c,r,'delivery','authorized','{"max_days":3}','operator_review');
 update public.commercial_action_intents set acceptance_consumed_at=null,consumed_at=null where id=a;
 result=public.accept_resolved_quote(a,c,s,r,newq,offer,'verification');
 if result is null then raise exception 'Authorized flexibility rejected'; end if;
 select count(*) into n from public.claim_commercial_message_approval(c,s,'Confirmed verification offer');
 if n<>1 then raise exception 'Authorized flexible confirmation rejected'; end if;

 -- The AI-led path must create an in-flight session without the legacy decision tool.
 delete from public.negotiation_sessions where rfq_id=r;
 update public.rfqs set due_by=now()+interval '1 hour' where id=r;
 insert into public.commercial_action_intents(client_id,supplier_id,rfq_id,quote_id,action_type,proposal,guard_snapshot,outcome,approved_message)
 values(c,s,r,newq,'counter','{"price":10,"quantity":10,"delivery_days":2}',jsonb_build_object('current_offer',offer,'price_policy','{"preferred_target":12,"acceptable_max":17}'::jsonb),'APPROVE','Counter verification offer');
 select count(*) into n from public.claim_commercial_message_approval(c,s,'Counter verification offer');
 if n<>1 or not exists(select 1 from public.negotiation_sessions where rfq_id=r and status='active' and latest_agent_counter=10) then raise exception 'AI-led counter did not create a negotiation'; end if;
 -- Finalized RFQ notices still require a backend-issued exact authorization.
 update public.rfqs set status='closed' where id=r;
 insert into public.commercial_action_intents(client_id,supplier_id,rfq_id,action_type,proposal,guard_snapshot,outcome,approved_message)
 values(c,s,r,'system_notice','{}','{"issued_by":"backend"}','APPROVE','RFQ verification closed');
 select count(*) into n from public.claim_commercial_message_approval(c,s,'RFQ verification closed');
 if n<>1 then raise exception 'Legitimate finalized request notice blocked'; end if;
end $$;
rollback;
select 'PASS: resolved acceptance, changed evidence, independent send claim, tampering and replay protection; fixtures rolled back' as verification;
