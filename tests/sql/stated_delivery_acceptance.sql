begin;
do $$
declare c uuid:=gen_random_uuid();s uuid:=gen_random_uuid();r uuid:=gen_random_uuid();q uuid:=gen_random_uuid();a uuid:=gen_random_uuid();term jsonb;offer jsonb;proposal jsonb;result jsonb;
begin
 if public.parse_supplier_delivery_term('Delivery after 4 days')->>'relation'<>'after' then raise exception 'Lower bound lost';end if;
 if public.parse_supplier_delivery_term('Delivery after 4 days')->>'days'<>'4' then raise exception 'Duration lost';end if;
 if public.parse_supplier_delivery_term('Delivery: within 2 working days')->>'unit'<>'business_days' then raise exception 'Business day unit lost';end if;
 if public.parse_supplier_delivery_term('4 days if stock arrives') is not null then raise exception 'Conditional delivery invented';end if;
 insert into public.clients(id,name,whatsapp_instance) values(c,'ROLLBACK delivery verification',c::text);
 insert into public.suppliers(id,client_id,name,phone_number,is_active) values(s,c,'ROLLBACK supplier',s::text,true);
 insert into public.rfqs(id,client_id,product_name,quantity,acceptable_price_min,acceptable_price_max,last_quote,status,due_by)
 values(r,c,'ROLLBACK floodlights',10,25,30,25,'active',now()+interval '1 day');
 insert into public.rfq_suppliers(rfq_id,supplier_id,status) values(r,s,'sent');
 insert into public.quotes(id,rfq_id,supplier_id,price,quantity,delivery_time,is_available) values(q,r,s,25.5,10,'Delivery after 4 days',true);
 term:=public.parse_supplier_delivery_term('Delivery after 4 days');
 offer:=jsonb_build_object('id',q,'rfq_id',r,'supplier_id',s,'price',25.5,'quantity',10,'delivery_time','Delivery after 4 days','resolved_delivery_days',4,'delivery_term',term,'term_sources',
 jsonb_build_object('price',jsonb_build_object('quote_id',q,'value',25.5),'quantity',jsonb_build_object('quote_id',q,'value',10),'delivery_time',jsonb_build_object('quote_id',q,'value','Delivery after 4 days')));
 proposal:=jsonb_build_object('price',25.5,'quantity',10,'delivery_days',4,'delivery_term',term);
 insert into public.commercial_action_intents(id,client_id,supplier_id,rfq_id,quote_id,action_type,proposal,guard_snapshot,outcome,approved_message)
 values(a,c,s,r,q,'accept',proposal,jsonb_build_object('current_offer',offer),'APPROVE','Confirmed with stated delivery after 4 days');
 perform public.queue_buyer_final_offer(c,s,r,q,offer);
 if public.valid_offer_delivery_acceptance(c,s,r,q,offer,proposal) then raise exception 'Missing buyer approval ignored';end if;
 update public.buyer_final_offer_reviews set status='authorized',decision='accept' where quote_id=q;
 if not public.valid_offer_delivery_acceptance(c,s,r,q,offer,proposal) then raise exception 'Approved stated term blocked';end if;
 if public.valid_offer_delivery_acceptance(c,s,r,q,offer,proposal-'delivery_term') then raise exception 'Required structured term omitted';end if;
 if public.valid_offer_delivery_acceptance(c,s,r,q,offer,jsonb_set(proposal,'{delivery_term,relation}','"within"')) then raise exception 'After rewritten as within';end if;
 update public.rfqs set required_delivery_days=4 where id=r;
 if public.valid_offer_delivery_acceptance(c,s,r,q,offer,proposal) then raise exception 'Lower bound treated as latest arrival';end if;
 update public.rfqs set required_delivery_days=null where id=r;
 result:=public.accept_resolved_quote(a,c,s,r,q,offer,'ROLLBACK verification');
 if result is null then raise exception 'Approved after term did not pass actual acceptance';end if;
 if (select count(*) from public.claim_commercial_message_approval(c,s,'Confirmed with stated delivery after 4 days'))<>1 then raise exception 'Approved stated confirmation blocked';end if;
end $$;
rollback;
