begin;
do $$
declare c uuid:=gen_random_uuid();s uuid:=gen_random_uuid();r uuid:=gen_random_uuid();other uuid:=gen_random_uuid();m uuid:=gen_random_uuid();answer uuid:=gen_random_uuid();m2 uuid:=gen_random_uuid();answer2 uuid:=gen_random_uuid();d uuid;result jsonb;n integer;
begin
 insert into public.clients(id,name,whatsapp_instance) values(c,'ROLLBACK expanded clarification',c::text);
 insert into public.suppliers(id,client_id,name,phone_number,is_active) values(s,c,'ROLLBACK supplier',s::text,true);
 insert into public.rfqs(id,client_id,product_name,status) values(r,c,'20-inch hammer','active'),(other,c,'Drill','active');
 insert into public.rfq_suppliers(rfq_id,supplier_id,status) values(r,s,'sent'),(other,s,'responded');
 insert into public.quotes(rfq_id,supplier_id,price,is_available) values(other,s,12,true);
 insert into public.message_log(id,client_id,supplier_id,direction,body) values(m,c,s,'inbound','95 final'),(answer,c,s,'inbound','Neither, I mean the drill'),(m2,c,s,'inbound','another price'),(answer2,c,s,'inbound','I mean a product with no RFQ');
 result=public.prepare_rfq_clarification(c,s,m,array[r],'{"price":95}',false,null);d=(result->'draft'->>'id')::uuid;
 result=public.resolve_rfq_clarification(c,s,d,other,answer);
 if not(result->>'resolved')::boolean or not(result->>'expanded_candidate')::boolean then raise exception 'Linked product correction rejected';end if;
 if (result->'draft'->>'resolved_rfq_id')::uuid<>other then raise exception 'Wrong RFQ resolved';end if;
 if not exists(select 1 from public.quotes where rfq_id=other and price=12) then raise exception 'Resolution overwrote quote';end if;
 result=public.prepare_rfq_clarification(c,s,m2,array[r],'{}',false,null);d=(result->'draft'->>'id')::uuid;
 result=public.resolve_rfq_clarification(c,s,d,null,answer2);
 if not(result->>'needs_buyer_help')::boolean then raise exception 'Unknown product did not request help';end if;
 result=public.resolve_rfq_clarification(c,s,d,null,answer2);
 select count(*) into n from public.flagged_for_review where client_id=c and category='rfq_mapping';
 if n<>1 then raise exception 'Repeated unknown product duplicated help request';end if;
 select count(*) into n from public.quotes where supplier_id=s;
 if n<>1 then raise exception 'Unknown product created quote';end if;
end $$;
rollback;
select 'PASS: linked product outside suggestions, preserved quote history, unknown product help and retry deduplication; fixtures rolled back' as verification;
