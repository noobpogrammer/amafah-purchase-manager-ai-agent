-- Keep the established state transition function; add AI text only under the same transaction lock.
alter function public.prepare_rfq_clarification(uuid,uuid,uuid,uuid[],jsonb,boolean) rename to prepare_rfq_clarification_state;
create function public.prepare_rfq_clarification(p_client_id uuid,p_supplier_id uuid,p_source_id uuid,p_candidates uuid[],p_facts jsonb,p_issue_question boolean default true,p_message text default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare result jsonb; approval uuid; text_body text;
begin
 if p_issue_question and (p_message is null or length(trim(p_message))=0 or length(p_message)>4000) then raise exception 'AI-authored question required'; end if;
 result=public.prepare_rfq_clarification_state(p_client_id,p_supplier_id,p_source_id,p_candidates,p_facts,p_issue_question);
 if result->>'outcome'='APPROVE' then
  approval=(result->'draft'->>'clarification_approval_id')::uuid;
  update public.commercial_action_intents set approved_message=trim(p_message),guard_snapshot=guard_snapshot||'{"ai_authored":true}'::jsonb
  where id=approval and consumed_at is null and not coalesce((guard_snapshot->>'ai_authored')::boolean,false);
  select approved_message into text_body from public.commercial_action_intents where id=approval;
  result=jsonb_set(result,'{approved_message}',to_jsonb(text_body));
 end if;
 return result;
end $$;
revoke all on function public.prepare_rfq_clarification(uuid,uuid,uuid,uuid[],jsonb,boolean,text) from public,anon,authenticated;
grant execute on function public.prepare_rfq_clarification(uuid,uuid,uuid,uuid[],jsonb,boolean,text) to service_role;
do $migration$
declare definition text;
begin
 select pg_get_functiondef('public.claim_rfq_clarification(uuid,uuid,text)'::regprocedure) into definition;
 definition:=replace(definition,'if p_body<>''Could you please confirm which product your message refers to?'' then return null; end if;','if p_body is null or length(trim(p_body))=0 then return null; end if;');
 execute definition;
end $migration$;
