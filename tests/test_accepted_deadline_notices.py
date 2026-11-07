import os
os.environ.setdefault('SUPABASE_URL','https://localhost.supabase.co')
os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY','offline-test-key')
from unittest.mock import patch,MagicMock,AsyncMock
from contextlib import ExitStack
import pytest
import main
import db

@pytest.mark.asyncio
@pytest.mark.parametrize('accepted',[True,False])
async def test_deadline_notice_checks_each_supplier_and_keeps_other_rfqs_independent(accepted):
    rfq={'id':'rfq','product_name':'LED lights','specs':'20W','quantity':20,'client_id':'tenant',
         'rfq_suppliers':[{'supplier_id':sid,'suppliers':{'id':sid,'phone_number':'+123'}} for sid in ['A','B']]}
    with ExitStack() as stack:
        stack.enter_context(patch.object(db,'get_quotes_for_rfq',return_value=[]))
        check=stack.enter_context(patch.object(db,'supplier_has_accepted_quote',side_effect=lambda r,s:accepted and s=='A'))
        stack.enter_context(patch.object(db,'get_message_by_event_key',return_value=None))
        log=stack.enter_context(patch.object(db,'log_message',return_value='message'))
        send=stack.enter_context(patch.object(main,'enqueue_message',new_callable=AsyncMock))
        done=stack.enter_context(patch.object(db,'mark_rfq_finalization_completed',return_value=True))
        stack.enter_context(patch.object(db,'expire_negotiation_sessions_for_rfq'))
        await main.finalize_rfq_job(rfq)
        assert send.await_count==(1 if accepted else 2)
        assert check.call_args_list[0].args==('rfq','A')
        text=send.call_args.args[1]
        assert '20W' in text and '20 units' in text
        assert 'quotation window' in text and 'closed as the deadline' not in text
        if accepted:assert log.call_args.args[1]=='B'
        done.assert_called_once()

@pytest.mark.asyncio
async def test_unknown_acceptance_state_does_not_send_or_finish_finalization():
    rfq={'id':'rfq','product_name':'lights','rfq_suppliers':[{'supplier_id':'supplier','suppliers':{'id':'supplier','phone_number':'+123'}}]}
    with patch.object(db,'get_quotes_for_rfq',return_value=[]),patch.object(db,'supplier_has_accepted_quote',side_effect=RuntimeError('database unavailable')),patch.object(db,'log_webhook_error'),patch.object(main,'enqueue_message',new_callable=AsyncMock) as send,patch.object(db,'mark_rfq_finalization_completed') as done:
        await main.finalize_rfq_job(rfq)
        send.assert_not_awaited();done.assert_not_called()

def test_accepted_quote_lookup_is_scoped_to_both_rfq_and_supplier():
    q=MagicMock();q.select.return_value=q;q.eq.return_value=q;q.limit.return_value=q;q.execute.return_value.data=[{'id':'accepted'}]
    with patch.object(db.supabase,'table',return_value=q):assert db.supplier_has_accepted_quote('rfq','supplier')
    assert [c.args for c in q.eq.call_args_list]==[('rfq_id','rfq'),('supplier_id','supplier'),('is_accepted',True)]
