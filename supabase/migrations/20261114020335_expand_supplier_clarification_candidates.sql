-- Suggested candidates are not the complete authorization boundary. Supplier linkage is.
create or replace function public.resolve_rfq_clarification(p_client_id uuid,p_supplier_id uuid,p_draft_id uuid,p_rfq_id uuid,p_answer_id uuid)
returns jsonb language plpgsql security definer set search_path='' as $$
declare d public.pending_commercial_drafts%rowtype;m public.message_log%rowtype;r public.rfqs%rowtype;expanded boolean;duplicates integer;flag uuid;
begin
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_client_id::text||p_supplier_id::text,0));
 select * into d from public.pending_commercial_drafts where id=p_draft_id and client_id=p_client_id and supplier_id=p_supplier_id for update;
 if not found then return jsonb_build_object('conflict',true,'reason','Draft missing');end if;
 if d.status='resolved' then
  if d.resolved_rfq_id=p_rfq_id and d.answer_source_message_id=p_answer_id then return jsonb_build_object('resolved',true,'draft',to_jsonb(d),'already_resolved',true);end if;
  return jsonb_build_object('conflict',true,'reason','Draft already resolved differently');
 end if;
 if d.status='abandoned' and d.answer_source_message_id=p_answer_id then return jsonb_build_object('needs_buyer_help',true,'already_requested',true,'draft',to_jsonb(d));end if;
 if d.status<>'awaiting_rfq_resolution' then return jsonb_build_object('conflict',true,'reason','Draft not waiting');end if;
 select * into m from public.message_log where id=p_answer_id and client_id=p_client_id and supplier_id=p_supplier_id and direction='inbound';
 if not found or p_answer_id::text=d.source_message_id or m.created_at<d.created_at then return jsonb_build_object('conflict',true,'reason','A subsequent supplier answer is required');end if;
 if not exists(select 1 from public.suppliers where id=p_supplier_id and client_id=p_client_id and is_active is not false and deleted_at is null) then return jsonb_build_object('conflict',true,'reason','Supplier unavailable');end if;
 select r0.* into r from public.rfqs r0 join public.rfq_suppliers rs on rs.rfq_id=r0.id
 where r0.id=p_rfq_id and r0.client_id=p_client_id and rs.supplier_id=p_supplier_id and r0.status='active' limit 1;
 if not found then
  insert into public.flagged_for_review(client_id,supplier_id,reason,category,raw_message,status,metadata)
  values(p_client_id,p_supplier_id,'Supplier clarified a product without an active RFQ linked to this supplier. Check the buying request before proceeding.','rfq_mapping',m.body,'pending',jsonb_build_object('draft_id',d.id,'answer_source_message_id',p_answer_id)) returning id into flag;
  update public.pending_commercial_drafts set status='abandoned',answer_source_message_id=p_answer_id,updated_at=now() where id=d.id returning * into d;
  return jsonb_build_object('needs_buyer_help',true,'flag_id',flag,'draft',to_jsonb(d));
 end if;
 expanded:=not(r.id=any(d.candidate_rfq_ids));
 if expanded then
  select count(distinct r0.id) into duplicates from public.rfqs r0 join public.rfq_suppliers rs on rs.rfq_id=r0.id
  where r0.client_id=p_client_id and rs.supplier_id=p_supplier_id and r0.status='active'
  and lower(trim(r0.product_name))=lower(trim(r.product_name)) and lower(trim(coalesce(r0.specs,'')))=lower(trim(coalesce(r.specs,'')));
  if duplicates<>1 then return jsonb_build_object('conflict',true,'reason','Several linked RFQs have this product and specifications; ask which invitation the supplier means');end if;
 end if;
 update public.pending_commercial_drafts set status='resolved',resolved_rfq_id=r.id,answer_source_message_id=p_answer_id,resolved_at=now(),updated_at=now() where id=d.id returning * into d;
 return jsonb_build_object('resolved',true,'draft',to_jsonb(d),'expanded_candidate',expanded);
end $$;
