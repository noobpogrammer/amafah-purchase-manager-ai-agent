-- Permit omission only for counter proposals. Acceptance still requires complete terms.
do $migration$
declare definition text; updated text; old_text text; new_text text;
begin
 select pg_get_functiondef('public.claim_commercial_message_approval(uuid,uuid,text)'::regprocedure) into definition;
 for old_text,new_text in select * from (values
 ($old$(c.proposal->>'quantity')::integer>0$old$,$new$((c.action_type in ('counter','counteroffer','negotiate') and not(c.proposal ? 'quantity')) or (c.proposal->>'quantity')::integer>0)$new$),
 ($old$(c.proposal->>'delivery_days')::integer>=0$old$,$new$((c.action_type in ('counter','counteroffer','negotiate') and not(c.proposal ? 'delivery_days')) or (c.proposal->>'delivery_days')::integer>=0)$new$),
 ($old$(r.quantity is null or$old$,$new$((c.action_type in ('counter','counteroffer','negotiate') and not(c.proposal ? 'quantity')) or r.quantity is null or$new$),
 ($old$(r.required_delivery_days is null or$old$,$new$((c.action_type in ('counter','counteroffer','negotiate') and not(c.proposal ? 'delivery_days')) or r.required_delivery_days is null or$new$)
 ) replacements(old_text,new_text) loop
  updated:=replace(definition,old_text,new_text);
  if updated=definition then raise exception 'Missing counter-only boundary hook: %',old_text;end if;
  definition:=updated;
 end loop;
 execute definition;
end $migration$;
